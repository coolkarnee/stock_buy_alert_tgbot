import unittest
from datetime import date
from unittest.mock import patch

from config import settings
from scanner import universe, upstox


class IpoUniverseTests(unittest.TestCase):
    def test_cutoff_uses_configured_rolling_months(self):
        self.assertEqual(universe.ipo_cutoff_date(date(2026, 10, 20)), date(2025, 10, 20))
        with patch.object(settings, "IPO_LOOKBACK_MONTHS", 6):
            self.assertEqual(universe.ipo_cutoff_date(date(2026, 10, 20)), date(2026, 4, 20))

    def test_universe_includes_only_recent_listings_and_keeps_dates_for_exemptions(self):
        listings = {
            "AT_CUTOFF": "2025-10-20",
            "RECENT": "2026-06-15",
            "OLD": "2025-10-19",
            "FUTURE": "2026-10-21",
        }
        with (
            patch.object(universe, "_index_symbols", return_value=set()),
            patch.object(universe, "_listed_ipo_dates", return_value=listings),
            patch("builtins.print"),
        ):
            symbols, listing_dates = universe.load_requested_universe(date(2026, 10, 20))
        self.assertEqual(symbols, {"AT_CUTOFF", "RECENT"})
        self.assertEqual(listing_dates, listings)

    def test_recent_ipo_filter_uses_inclusive_listing_date_cutoff(self):
        listings = {
            "AT_CUTOFF": "2025-10-20",
            "RECENT": "2026-06-15",
            "OLD": "2025-10-19",
            "FUTURE": "2026-10-21",
            "NO_DATE": "not-a-date",
        }
        with patch.object(settings, "IPO_LOOKBACK_MONTHS", 12):
            self.assertEqual(
                universe.recent_ipo_listing_dates(listings, date(2026, 10, 20)),
                {"AT_CUTOFF": "2025-10-20", "RECENT": "2026-06-15"},
            )

    def test_ipo_api_uses_listing_date(self):
        payload = {
            "data": [
                {"symbol": "LISTED", "listing_date": "2026-01-05"},
                {"symbol": "NO_DATE", "bidding_start_date": "2026-01-01"},
            ],
            "meta_data": {"page": {"total_pages": 1}},
        }
        with patch("scanner.upstox._request_json", return_value=payload):
            self.assertEqual(universe._listed_ipo_dates(), {"LISTED": "2026-01-05"})

    def test_equity_universe_carries_ipo_listing_date_into_live_state(self):
        with (
            patch.object(
                universe, "load_requested_universe",
                return_value=({"TEST"}, {"TEST": "2026-06-15"}),
            ),
            patch.object(upstox, "load_instruments", return_value=[{
                "segment": "NSE_EQ", "instrument_type": "EQ",
                "trading_symbol": "TEST", "instrument_key": "NSE_EQ|TEST",
            }]),
        ):
            frame = upstox.load_nse_equities()
        self.assertEqual(frame.iloc[0].listing_date, "2026-06-15")


if __name__ == "__main__":
    unittest.main()
