import csv
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd
from openpyxl import load_workbook

import performance
import swing_performance
from scanner import sector


class SectorTests(unittest.TestCase):
    def setUp(self):
        sector.get_sector.cache_clear()
        sector._symbol_isins.cache_clear()
        self.addCleanup(sector.get_sector.cache_clear)
        self.addCleanup(sector._symbol_isins.cache_clear)
        instruments = patch.object(sector, "load_instruments", return_value=[{
            "segment": "NSE_EQ", "instrument_type": "EQ",
            "trading_symbol": "TEST", "isin": "INE002A01018",
        }])
        self.instruments = instruments.start()
        self.addCleanup(instruments.stop)

    def test_fetches_profile_sector_by_isin_and_caches_per_symbol(self):
        with patch.object(sector, "_request_json") as request:
            request.return_value = {"status": "success", "data": {
                "sector": "Refineries", "company_profile": "Reliance Industries Limited",
            }}
            self.assertEqual(sector.get_sector("TEST"), "Refineries")
            self.assertEqual(sector.get_sector("TEST"), "Refineries")
            self.assertEqual(sector.get_sector("OTHER"), "NA")
            request.assert_called_once_with(
                f"{sector.BASE}/v2/fundamentals/INE002A01018/profile"
            )
            self.instruments.assert_called_once()

    def test_resolves_only_normal_nse_equities_not_other_exchanges(self):
        self.instruments.return_value = [
            {"segment": "NSE_EQ", "instrument_type": "EQ", "trading_symbol": "TEST",
             "isin": "INE002A01018"},
            {"segment": "BSE_EQ", "instrument_type": "EQ", "trading_symbol": "TEST",
             "isin": "OTHER"},
            {"segment": "NSE_EQ", "instrument_type": "INDEX", "trading_symbol": "INDEX",
             "isin": "INDEX"},
        ]
        with patch.object(sector, "_request_json") as request:
            request.return_value = {"status": "success", "data": {"sector": "Machinery"}}
            self.assertEqual(sector.get_sector("TEST"), "Machinery")
            self.assertEqual(sector.get_sector("INDEX"), "NA")
            request.assert_called_once_with(f"{sector.BASE}/v2/fundamentals/INE002A01018/profile")

    def test_missing_sector_never_uses_legacy_industry(self):
        with patch.object(sector, "_request_json") as request, patch("builtins.print") as log:
            request.return_value = {"status": "success", "data": {"industry": "Basic Materials"}}
            self.assertEqual(sector.get_sector("TEST"), "NA")
            log.assert_called_once()

    def test_normalizes_ticker_and_sector_without_partial_symbol_matching(self):
        with patch.object(sector, "_request_json") as request, patch("builtins.print"):
            request.return_value = {"status": "success", "data": {"sector": " Machinery "}}
            self.assertEqual(sector.get_sector(" test "), "Machinery")
            self.assertEqual(sector.get_sector("TES"), "NA")
            request.assert_called_once()

    def test_empty_and_invalid_sectors_return_na(self):
        for value in (None, "", " ", "NA", "Unavailable", 123):
            with self.subTest(value=value):
                sector.get_sector.cache_clear()
                with patch.object(sector, "_request_json") as request, patch("builtins.print"):
                    request.return_value = {"status": "success", "data": {"sector": value}}
                    self.assertEqual(sector.get_sector("TEST"), "NA")

    def test_missing_isin_returns_na_without_profile_request(self):
        self.instruments.return_value[0].pop("isin")
        with patch.object(sector, "_request_json") as request:
            with patch("builtins.print"):
                self.assertEqual(sector.get_sector("TEST"), "NA")
            request.assert_not_called()

    def test_unsuccessful_or_invalid_payload_is_logged_and_returns_na(self):
        for payload in (
            {"status": "error", "data": []},
            {"status": "success", "data": None},
            {"status": "success", "data": []},
        ):
            with self.subTest(payload=payload):
                sector.get_sector.cache_clear()
                with (patch.object(sector, "_request_json", return_value=payload),
                      patch("builtins.print") as log):
                    self.assertEqual(sector.get_sector("TEST"), "NA")
                    self.assertTrue(any("lookup failed" in call.args[0] for call in log.call_args_list))

    def test_lookup_failure_is_visible_and_does_not_block_alerts(self):
        with patch.object(sector, "_request_json") as request, patch("builtins.print") as log:
            request.side_effect = RuntimeError("provider unavailable")
            self.assertEqual(sector.get_sector("TEST"), "NA")
            self.assertTrue(any("provider unavailable" in call.args[0] for call in log.call_args_list))
            self.assertEqual(sector.get_sector("OTHER"), "NA")
            request.assert_called_once()

    def test_network_failure_returns_na_without_blocking_alert(self):
        with (patch.object(sector, "_request_json", side_effect=sector.RequestException("timeout")),
              patch("builtins.print") as log):
            self.assertEqual(sector.get_sector("TEST"), "NA")
            self.assertTrue(any("timeout" in call.args[0] for call in log.call_args_list))

    def test_instrument_lookup_failure_is_logged_and_cached(self):
        self.instruments.side_effect = sector.RequestException("instrument timeout")
        with patch.object(sector, "_request_json") as request, patch("builtins.print") as log:
            self.assertEqual(sector.get_sector("TEST"), "NA")
            self.assertEqual(sector.get_sector("OTHER"), "NA")
            self.assertTrue(any("instrument timeout" in call.args[0] for call in log.call_args_list))
            self.instruments.assert_called_once()
            request.assert_not_called()

    def test_failure_for_one_stock_preserves_other_stock_sector(self):
        self.instruments.return_value.append({
            "segment": "NSE_EQ", "instrument_type": "EQ",
            "trading_symbol": "OTHER", "isin": "INE009A01021",
        })
        with patch.object(sector, "_request_json") as request, patch("builtins.print"):
            request.side_effect = [
                {"status": "success", "data": {"sector": "Machinery"}},
                sector.RequestException("timeout"),
            ]
            self.assertEqual(sector.get_sector("TEST"), "Machinery")
            self.assertEqual(sector.get_sector("OTHER"), "NA")
            self.assertEqual(sector.get_sector("TEST"), "Machinery")
            self.assertEqual(request.call_count, 2)

    def test_preserves_recorded_sector_and_backfills_legacy_values(self):
        with patch.object(sector, "get_sector", return_value="Software - Application") as lookup:
            self.assertEqual(sector.resolve_sector("TEST", "Specialty Chemicals"), "Specialty Chemicals")
            lookup.assert_not_called()
            for recorded in (None, "", "Unavailable", "NA", float("nan")):
                self.assertEqual(sector.resolve_sector("TEST", recorded), "Software - Application")

    def test_summary_counts_alert_events_and_distinct_stocks(self):
        alerts = [
            {"symbol": "TEST", "alert_type": "BUY", "sector": "Specialty Chemicals"},
            {"symbol": "TEST", "alert_type": "EP", "sector": "Specialty Chemicals"},
            {"symbol": "OTHER", "alert_type": "BUY", "sector": "Specialty Chemicals"},
            {"symbol": "UNKNOWN", "alert_type": "EP", "sector": "NA"},
        ]
        text = "\n".join(performance.sector_summary(alerts))
        self.assertIn("Specialty Chemicals: 3 alerts | 2 Momentum BUY | 1 EP | 2 distinct stocks", text)
        self.assertIn("NA: 1 alerts | 0 Momentum BUY | 1 EP | 1 distinct stocks", text)

    def test_performance_backfills_legacy_alert_log_and_exports_sector(self):
        today = datetime.now(performance.IST).date().isoformat()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            alert_file = root / "live_alerts.csv"
            with alert_file.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["alert_date", "symbol", "alert_price"])
                writer.writeheader()
                writer.writerow({"alert_date": today, "symbol": "TEST", "alert_price": "100"})
            with (patch.object(performance, "Path", side_effect=lambda name: root / name),
                  patch.object(performance, "resolve_sector", return_value="Specialty Chemicals"),
                  patch.object(performance, "load_nse_equities",
                               return_value=pd.DataFrame({"trading_symbol": ["TEST"], "instrument_key": ["KEY"]})),
                  patch.object(performance, "current_day_daily",
                               return_value=pd.DataFrame({"timestamp": [today], "close": [110]})),
                  patch.object(performance, "send") as send):
                performance.run()
            report = send.call_args.args[0]
            self.assertIn("Sector: Specialty Chemicals", report)
            self.assertIn("Specialty Chemicals: 1 alerts | 1 Momentum BUY | 0 EP", report)
            with (root / "performance.csv").open(newline="", encoding="utf-8") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["sector"], "Specialty Chemicals")
            self.assertEqual(row["alert_type"], "BUY")
            self.assertEqual(row["status"], "NO HIGH DATA")

    def test_swing_workbook_includes_sector_without_shifting_status_format(self):
        row = dict.fromkeys(swing_performance.OUTPUT_COLUMNS, "")
        row.update({
            "Alert ID": "TEST", "Ticker Name": "TEST", "Alert Date": "2026-10-16",
            "Alert Time": "09:16:01", "Sector": "Specialty Chemicals",
            "Status": "Full exit", "Performance %": "5.25",
        })
        with TemporaryDirectory() as directory:
            target = Path(directory) / "swing.xlsx"
            with patch.object(swing_performance, "EXCEL_FILE", target):
                swing_performance._write_excel(pd.DataFrame([row]))
            workbook = load_workbook(target)
            sheet = workbook["Swing Performance"]
            self.assertEqual(sheet["P3"].value, "Sector")
            self.assertEqual(sheet["P5"].value, "Specialty Chemicals")
            self.assertEqual(sheet["N5"].value, "Full exit")
            self.assertEqual(sheet["M5"].value, 5.25)
            self.assertEqual(sheet["M5"].number_format, '0.00"%"')
            workbook.close()

    def test_swing_reads_sector_from_new_logs_and_accepts_legacy_logs(self):
        for include_sector in (True, False):
            with self.subTest(include_sector=include_sector), TemporaryDirectory() as directory:
                target = Path(directory) / "alerts.csv"
                fields = ["symbol", "alert_price", "alert_date", "industry"]
                source = {"symbol": "TEST", "alert_price": "100", "alert_date": "2026-10-16",
                          "industry": "Legacy industry label"}
                if include_sector:
                    fields.append("sector")
                    source["sector"] = "Specialty Chemicals"
                with target.open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.DictWriter(handle, fieldnames=fields)
                    writer.writeheader()
                    writer.writerow(source)
                with patch.object(swing_performance, "ALERT_FILE", target):
                    rows = swing_performance._read_current_alerts()
                self.assertEqual(rows[0]["Sector"], "Specialty Chemicals" if include_sector else "")

    def test_swing_backfills_profile_sector_instead_of_relabelling_legacy_industry(self):
        previous = {
            "Alert ID": "TEST", "Ticker Name": "TEST", "Industry": "Legacy industry label",
            "Buy Price": "100", "Date Time": "2026-10-16T09:16:01+05:30",
        }
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with (patch.object(swing_performance, "_read_existing", return_value={"TEST": previous}),
                  patch.object(swing_performance, "_read_current_alerts", return_value=[]),
                  patch.object(swing_performance, "ARTIFACT_DIR", root),
                  patch.object(swing_performance, "OUTPUT_FILE", root / "swing.csv"),
                  patch.object(swing_performance, "load_nse_equities",
                               return_value=pd.DataFrame({"trading_symbol": ["TEST"], "instrument_key": ["KEY"]})),
                  patch.object(sector, "get_sector", return_value="Refineries") as lookup,
                  patch.object(swing_performance, "_evaluate_alert",
                               side_effect=lambda alert, key: {**alert, "Status": "Trade open"}),
                  patch.object(swing_performance, "_write_excel") as write_excel,
                  patch.object(swing_performance, "_send_exit_alerts"),
                  patch.object(swing_performance, "_send_excel_report")):
                swing_performance.run()
                lookup.assert_called_once_with("TEST")
                frame = write_excel.call_args.args[0]
                self.assertEqual(frame.iloc[0]["Sector"], "Refineries")
                self.assertNotIn("Industry", frame.columns)

    def test_swing_legacy_rerun_preserves_recorded_sector(self):
        previous = {
            "Alert ID": "TEST", "Ticker Name": "TEST", "Sector": "Specialty Chemicals",
            "Buy Price": "100", "Date Time": "2026-10-16T09:16:01+05:30",
        }
        legacy = {key: value for key, value in previous.items() if key != "Sector"}
        legacy["Sector"] = ""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with (patch.object(swing_performance, "_read_existing", return_value={"TEST": previous}),
                  patch.object(swing_performance, "_read_current_alerts", return_value=[legacy]),
                  patch.object(swing_performance, "ARTIFACT_DIR", root),
                  patch.object(swing_performance, "OUTPUT_FILE", root / "swing.csv"),
                  patch.object(swing_performance, "load_nse_equities",
                               return_value=pd.DataFrame({"trading_symbol": ["TEST"], "instrument_key": ["KEY"]})),
                  patch.object(sector, "get_sector") as lookup,
                  patch.object(swing_performance, "_evaluate_alert",
                               side_effect=lambda alert, key: {**alert, "Status": "Trade open"}),
                  patch.object(swing_performance, "_write_excel") as write_excel,
                  patch.object(swing_performance, "_send_exit_alerts"),
                  patch.object(swing_performance, "_send_excel_report")):
                swing_performance.run()
                lookup.assert_not_called()
                self.assertEqual(write_excel.call_args.args[0].iloc[0]["Sector"], "Specialty Chemicals")


if __name__ == "__main__":
    unittest.main()
