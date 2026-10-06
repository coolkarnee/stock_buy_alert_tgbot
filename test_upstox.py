import unittest
from unittest.mock import patch
import pandas as pd

from scanner import upstox

class UpstoxUniverseTests(unittest.TestCase):
    
    @patch('scanner.upstox.load_instruments')
    def test_load_nse_equities_filters_correctly(self, mock_load_instruments):
        # Mock the Upstox JSON instrument master response
        mock_load_instruments.return_value = [
            {"instrument_key": "NSE_EQ|RELIANCE", "trading_symbol": "RELIANCE", "segment": "NSE_EQ", "instrument_type": "EQ", "security_type": "NORMAL"},
            {"instrument_key": "NSE_EQ|TCS", "trading_symbol": "TCS", "segment": "NSE_EQ", "instrument_type": "EQ", "security_type": "NORMAL"},
            # These should be filtered out:
            {"instrument_key": "NSE_INDEX|NIFTY50", "trading_symbol": "NIFTY 50", "segment": "NSE_INDEX"}, # Index
            {"instrument_key": "BSE_EQ|INFY", "trading_symbol": "INFY", "segment": "BSE_EQ", "instrument_type": "EQ"}, # Wrong Exchange
            {"instrument_key": "NSE_EQ|NIFTYBEES", "trading_symbol": "NIFTYBEES", "segment": "NSE_EQ", "instrument_type": "ETF"} # ETF
        ]
        
        universe = upstox.load_nse_equities()
        
        # Verify it only returns the 2 pure NSE equities
        self.assertEqual(len(universe), 2)
        self.assertIn("RELIANCE", universe["trading_symbol"].values)
        self.assertIn("TCS", universe["trading_symbol"].values)
        self.assertNotIn("NIFTY 50", universe["trading_symbol"].values)
        self.assertNotIn("INFY", universe["trading_symbol"].values)
        
    @patch('scanner.upstox.load_instruments')
    def test_find_smallcap250_key_locates_index(self, mock_load_instruments):
        # We also want to ensure the engine can find the smallcap context index
        mock_load_instruments.return_value = [
            {"instrument_key": "NSE_INDEX|NIFTY SMALLCAP 250", "trading_symbol": "NIFTY SMALLCAP 250", "segment": "NSE_INDEX"}
        ]
        
        from scanner.live_data import find_smallcap250_key
        key = find_smallcap250_key()
        self.assertEqual(key, "NSE_INDEX|NIFTY SMALLCAP 250")

if __name__ == "__main__":
    unittest.main()