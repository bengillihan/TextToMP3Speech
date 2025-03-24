import pytz
from datetime import datetime

# Seattle timezone (GMT-7 during Daylight Saving Time, GMT-8 during Standard Time)
SEATTLE_TZ = pytz.timezone('America/Los_Angeles')

def utc_to_seattle_time(utc_datetime):
    """
    Convert UTC datetime to Seattle time (GMT-7 or GMT-8 depending on DST)
    
    Args:
        utc_datetime: The UTC datetime to convert
        
    Returns:
        datetime: The datetime in Seattle time (GMT-7/GMT-8)
    """
    if utc_datetime is None:
        return None
        
    # Make sure the datetime is timezone-aware as UTC
    if utc_datetime.tzinfo is None:
        utc_datetime = pytz.utc.localize(utc_datetime)
    
    # Convert to Seattle time
    seattle_time = utc_datetime.astimezone(SEATTLE_TZ)
    return seattle_time

def format_seattle_time(utc_datetime, format_str='%Y-%m-%d %H:%M:%S'):
    """
    Format a UTC datetime to Seattle time string
    
    Args:
        utc_datetime: The UTC datetime to convert
        format_str: The format string to use
        
    Returns:
        str: The formatted Seattle time string
    """
    if utc_datetime is None:
        return ""
        
    seattle_time = utc_to_seattle_time(utc_datetime)
    return seattle_time.strftime(format_str)