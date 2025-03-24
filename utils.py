import os
import logging
from app import app, db
from models import Conversion
from timezone_utils import utc_to_seattle_time, format_seattle_time

logger = logging.getLogger(__name__)

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
            if conversion.file_path and os.path.exists(conversion.file_path):
                try:
                    os.remove(conversion.file_path)
                    # Update the conversion record
                    conversion.file_path = None
                    count += 1
                except Exception as e:
                    logger.error(f"Failed to delete file {conversion.file_path}: {str(e)}")
        
        db.session.commit()
        return count
        
    except Exception as e:
        logger.error(f"Error during file cleanup: {str(e)}")
        raise
