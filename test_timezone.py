import unittest
from datetime import datetime
from utils import utc_to_seattle_time, format_seattle_time

class TimezoneTests(unittest.TestCase):
    
    def test_utc_to_seattle_conversion(self):
        """Test that UTC time is correctly converted to Seattle time (GMT-8)"""
        # Create a sample UTC time
        sample_utc = datetime(2025, 3, 24, 12, 0, 0)  # March 24, 2025, 12:00 UTC
        
        # Convert to Seattle time
        seattle_time = utc_to_seattle_time(sample_utc)
        
        # Seattle is GMT-8, so it should be 8 hours behind UTC
        expected_hour = 4  # 12 - 8 = 4
        
        self.assertEqual(seattle_time.hour, expected_hour)
        self.assertEqual(seattle_time.day, 24)  # Same day since it's only 8 hours difference
        self.assertEqual(seattle_time.month, 3)
        self.assertEqual(seattle_time.year, 2025)
        
    def test_format_seattle_time(self):
        """Test that the format_seattle_time utility formats dates correctly"""
        # Create a sample UTC time
        sample_utc = datetime(2025, 3, 24, 20, 30, 15)  # March 24, 2025, 20:30:15 UTC
        
        # Format the time
        formatted = format_seattle_time(sample_utc, '%Y-%m-%d %H:%M:%S')
        
        # Seattle is GMT-8, so it should be 12:30:15 in Seattle
        expected_time = "2025-03-24 12:30:15"
        
        self.assertEqual(formatted, expected_time)
        
    def test_null_handling(self):
        """Test that the functions handle None values gracefully"""
        self.assertIsNone(utc_to_seattle_time(None))
        self.assertEqual(format_seattle_time(None), "")
        
if __name__ == '__main__':
    unittest.main()