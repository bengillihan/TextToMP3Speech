import unittest
import pytz
from datetime import datetime
from timezone_utils import utc_to_seattle_time, format_seattle_time, SEATTLE_TZ

class TimezoneTests(unittest.TestCase):
    
    def test_utc_to_seattle_conversion(self):
        """Test that UTC time is correctly converted to Seattle time (GMT-7/GMT-8 depending on DST)"""
        # Create a sample UTC time
        sample_utc = datetime(2025, 3, 24, 12, 0, 0)  # March 24, 2025, 12:00 UTC
        
        # Convert to Seattle time
        seattle_time = utc_to_seattle_time(sample_utc)
        
        # Seattle is GMT-7 during Daylight Saving Time (March-November)
        # or GMT-8 during Standard Time (November-March)
        utc_offset = SEATTLE_TZ.utcoffset(sample_utc).total_seconds() / 3600
        expected_hour = int(12 + utc_offset)  # 12 + offset (-7 or -8)
        
        self.assertEqual(seattle_time.hour, expected_hour)
        self.assertEqual(seattle_time.day, 24)  # Same day since it's only 7-8 hours difference
        self.assertEqual(seattle_time.month, 3)
        self.assertEqual(seattle_time.year, 2025)
        
    def test_format_seattle_time(self):
        """Test that the format_seattle_time utility formats dates correctly"""
        # Create a sample UTC time
        sample_utc = datetime(2025, 3, 24, 20, 30, 15)  # March 24, 2025, 20:30:15 UTC
        
        # Format the time
        formatted = format_seattle_time(sample_utc, '%Y-%m-%d %H:%M:%S')
        
        # Calculate expected time based on DST rules
        seattle_time = utc_to_seattle_time(sample_utc)
        expected_time = seattle_time.strftime('%Y-%m-%d %H:%M:%S')
        
        self.assertEqual(formatted, expected_time)
        
    def test_null_handling(self):
        """Test that the functions handle None values gracefully"""
        self.assertIsNone(utc_to_seattle_time(None))
        self.assertEqual(format_seattle_time(None), "")
        
if __name__ == '__main__':
    unittest.main()