import os
import logging
from datetime import datetime, timedelta
from app import db
from models import APILog, Conversion, ConversionMetrics

logger = logging.getLogger(__name__)


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

    cutoff = (now or datetime.utcnow()) - timedelta(days=retention_days)
    expired_conversions = Conversion.query.filter(
        Conversion.created_at < cutoff
    ).all()

    deleted_files = 0
    deleted_conversions = 0

    try:
        for conversion in expired_conversions:
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
                "Deleted %s conversions older than %s days and %s audio files",
                deleted_conversions,
                retention_days,
                deleted_files,
            )

        return {
            "conversions": deleted_conversions,
            "files": deleted_files,
            "cutoff": cutoff,
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
