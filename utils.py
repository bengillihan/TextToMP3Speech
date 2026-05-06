import os
import logging
from datetime import datetime, timedelta
from sqlalchemy import inspect, text, update
from app import db
from models import (
    DEFAULT_CONVERSION_RETENTION_DAYS,
    RETENTION_DAY_CHOICES,
    APILog,
    Conversion,
    ConversionMetrics,
    TTS_MODEL_FAST,
)

logger = logging.getLogger(__name__)

DATABASE_INDEXES = (
    ("ix_conversion_user_id", "conversion", "user_id"),
    ("ix_conversion_created_at", "conversion", "created_at"),
    ("ix_conversion_status", "conversion", "status"),
    ("ix_api_log_conversion_id", "api_log", "conversion_id"),
    ("ix_api_log_timestamp", "api_log", "timestamp"),
)


def _create_index_statement(index_name, table_name, column_name, concurrently=False):
    concurrent_sql = " CONCURRENTLY" if concurrently else ""
    return text(
        f'CREATE INDEX{concurrent_sql} IF NOT EXISTS {index_name} '
        f'ON "{table_name}" ("{column_name}")'
    )


def ensure_database_indexes():
    """Create performance indexes that may be missing on existing databases."""
    ensured_indexes = []

    use_concurrently = db.engine.dialect.name == "postgresql"
    if use_concurrently:
        connection_context = db.engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        )
    else:
        connection_context = db.engine.begin()

    with connection_context as connection:
        for index_name, table_name, column_name in DATABASE_INDEXES:
            connection.execute(
                _create_index_statement(
                    index_name,
                    table_name,
                    column_name,
                    concurrently=use_concurrently,
                )
            )
            ensured_indexes.append(index_name)

    logger.info("Ensured database indexes: %s", ", ".join(ensured_indexes))
    return ensured_indexes


def ensure_database_schema():
    """Apply small idempotent schema additions for deployments without migrations."""
    inspector = inspect(db.engine)
    if not inspector.has_table("conversion"):
        return {"changes": [], "tables_ready": False}

    changes = []
    conversion_columns = {
        column["name"] for column in inspector.get_columns("conversion")
    }

    try:
        if "tts_model" not in conversion_columns:
            if db.engine.dialect.name == "postgresql":
                statement = (
                    'ALTER TABLE "conversion" '
                    "ADD COLUMN IF NOT EXISTS tts_model VARCHAR(64) DEFAULT 'tts-1'"
                )
            else:
                statement = (
                    'ALTER TABLE "conversion" '
                    "ADD COLUMN tts_model VARCHAR(64) DEFAULT 'tts-1'"
                )
            db.session.execute(text(statement))
            changes.append("added conversion.tts_model")

        if "keep_forever" not in conversion_columns:
            if db.engine.dialect.name == "postgresql":
                statement = (
                    'ALTER TABLE "conversion" '
                    "ADD COLUMN IF NOT EXISTS keep_forever BOOLEAN DEFAULT FALSE"
                )
            else:
                statement = (
                    'ALTER TABLE "conversion" '
                    "ADD COLUMN keep_forever BOOLEAN DEFAULT FALSE"
                )
            db.session.execute(text(statement))
            changes.append("added conversion.keep_forever")

        if "retention_days" not in conversion_columns:
            if db.engine.dialect.name == "postgresql":
                statement = (
                    'ALTER TABLE "conversion" '
                    f"ADD COLUMN IF NOT EXISTS retention_days INTEGER DEFAULT {DEFAULT_CONVERSION_RETENTION_DAYS}"
                )
            else:
                statement = (
                    'ALTER TABLE "conversion" '
                    f"ADD COLUMN retention_days INTEGER DEFAULT {DEFAULT_CONVERSION_RETENTION_DAYS}"
                )
            db.session.execute(text(statement))
            changes.append("added conversion.retention_days")

        result = db.session.execute(
            update(Conversion)
            .where(Conversion.tts_model.is_(None))
            .values(tts_model=TTS_MODEL_FAST)
        )
        if result.rowcount:
            changes.append(f"backfilled {result.rowcount} conversion.tts_model values")

        result = db.session.execute(
            update(Conversion)
            .where(Conversion.keep_forever.is_(None))
            .values(keep_forever=False)
        )
        if result.rowcount:
            changes.append(f"backfilled {result.rowcount} conversion.keep_forever values")

        result = db.session.execute(
            update(Conversion)
            .where(Conversion.retention_days.is_(None))
            .values(retention_days=DEFAULT_CONVERSION_RETENTION_DAYS)
        )
        if result.rowcount:
            changes.append(f"backfilled {result.rowcount} conversion.retention_days values")

        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    if changes:
        logger.info("Ensured database schema: %s", ", ".join(changes))
    return {"changes": changes, "tables_ready": True}


def _stale_conversion_cutoff(stale_after_minutes, now=None):
    if stale_after_minutes < 1:
        raise ValueError("stale_after_minutes must be at least 1")

    return (now or datetime.utcnow()) - timedelta(minutes=stale_after_minutes)


def requeue_stale_processing_conversions(stale_after_minutes=15, now=None, conversion_id=None):
    """
    Move interrupted processing conversions back to pending without starting work.

    This is designed for app startup: it repairs database state after a deploy or
    restart, while letting normal user polling restart only the conversion being
    viewed.
    """
    cutoff = _stale_conversion_cutoff(stale_after_minutes, now=now)
    query = Conversion.query.with_entities(Conversion.id).filter(
        Conversion.status == 'processing',
        Conversion.updated_at < cutoff,
    )
    if conversion_id is not None:
        query = query.filter(Conversion.id == conversion_id)

    stale_ids = [row.id for row in query.all()]
    if not stale_ids:
        return {"conversions": 0, "ids": [], "cutoff": cutoff}

    try:
        db.session.execute(
            update(Conversion)
            .where(Conversion.id.in_(stale_ids))
            .values(
                status='pending',
                progress=0.0,
                updated_at=Conversion.updated_at,
            )
        )
        db.session.add_all([
            APILog(
                conversion_id=stale_id,
                type='warning',
                message=(
                    "Conversion was reset to pending after an app restart or "
                    "interrupted worker."
                ),
            )
            for stale_id in stale_ids
        ])
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    logger.warning(
        "Requeued %s processing conversions stale for more than %s minutes",
        len(stale_ids),
        stale_after_minutes,
    )
    return {"conversions": len(stale_ids), "ids": stale_ids, "cutoff": cutoff}


def claim_stale_pending_conversion_for_restart(conversion_id, stale_after_minutes=15, now=None):
    """
    Atomically claim one old pending conversion so only one request restarts it.
    """
    now = now or datetime.utcnow()
    cutoff = _stale_conversion_cutoff(stale_after_minutes, now=now)

    try:
        result = db.session.execute(
            update(Conversion)
            .where(
                Conversion.id == conversion_id,
                Conversion.status == 'pending',
                Conversion.updated_at < cutoff,
            )
            .values(
                progress=0.0,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            db.session.rollback()
            return False

        db.session.add(APILog(
            conversion_id=conversion_id,
            type='warning',
            message="Conversion was restarted after a stale pending state.",
        ))
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        raise


def _delete_conversion_file(conversion):
    """Delete the generated audio file for a conversion if it still exists."""
    if not conversion.file_path:
        return False

    if not os.path.exists(conversion.file_path):
        return False

    try:
        os.remove(conversion.file_path)
        return True
    except Exception as e:
        logger.error(f"Failed to delete file {conversion.file_path}: {str(e)}")
        return False


def _conversion_retention_days(conversion, default_retention_days):
    try:
        retention_days = int(conversion.retention_days or default_retention_days)
    except (TypeError, ValueError):
        retention_days = default_retention_days

    return max(1, retention_days)


def cleanup_expired_conversions(retention_days=90, now=None):
    """
    Delete conversions older than the retention window, including related logs,
    metrics, and generated audio files.

    Args:
        retention_days: Number of days to keep conversions
        now: Optional datetime override for tests

    Returns:
        dict: Counts of deleted conversions and files
    """
    if retention_days < 1:
        raise ValueError("retention_days must be at least 1")

    now = now or datetime.utcnow()
    shortest_retention_days = min(RETENTION_DAY_CHOICES + (retention_days,))
    earliest_cutoff = now - timedelta(days=shortest_retention_days)
    candidates = Conversion.query.filter(
        Conversion.keep_forever.isnot(True),
        Conversion.created_at < earliest_cutoff,
    ).all()

    deleted_files = 0
    deleted_conversions = 0

    try:
        for conversion in candidates:
            conversion_retention_days = _conversion_retention_days(
                conversion,
                retention_days,
            )
            cutoff = now - timedelta(days=conversion_retention_days)
            if conversion.created_at >= cutoff:
                continue

            if _delete_conversion_file(conversion):
                deleted_files += 1

            APILog.query.filter_by(conversion_id=conversion.id).delete(
                synchronize_session=False
            )
            ConversionMetrics.query.filter_by(conversion_id=conversion.id).delete(
                synchronize_session=False
            )
            db.session.delete(conversion)
            deleted_conversions += 1

        db.session.commit()

        if deleted_conversions:
            logger.info(
                "Deleted %s expired conversions and %s audio files",
                deleted_conversions,
                deleted_files,
            )

        return {
            "conversions": deleted_conversions,
            "files": deleted_files,
            "cutoff": earliest_cutoff,
        }
    except Exception:
        db.session.rollback()
        raise

def cleanup_old_files(user_id, keep_latest=50):
    """
    Cleanup old audio files for a user, keeping only the specified number of latest files.
    
    Args:
        user_id: The ID of the user whose files should be cleaned up
        keep_latest: Number of latest files to keep
        
    Returns:
        int: Number of files deleted
    """
    try:
        # Get all completed conversions for the user, ordered by creation date
        conversions = Conversion.query.filter_by(
            user_id=user_id, 
            status='completed'
        ).filter(
            Conversion.keep_forever.isnot(True)
        ).order_by(Conversion.created_at.desc()).all()
        
        # If we have more than keep_latest, delete the oldest ones
        files_to_delete = conversions[keep_latest:] if len(conversions) > keep_latest else []
        
        count = 0
        for conversion in files_to_delete:
            if _delete_conversion_file(conversion):
                # Update the conversion record
                conversion.file_path = None
                count += 1
        
        db.session.commit()
        return count
        
    except Exception as e:
        logger.error(f"Error during file cleanup: {str(e)}")
        raise
