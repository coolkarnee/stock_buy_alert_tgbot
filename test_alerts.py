import unittest
from unittest.mock import patch

import performance
from config import settings
from scanner import alerts


def utf16_length(text):
    return len(text.encode("utf-16-le")) // 2


class TelegramMessageTests(unittest.TestCase):
    def setUp(self):
        credentials = patch.multiple(settings, TELEGRAM_BOT_TOKEN="test-token",
                                     TELEGRAM_CHAT_ID="123,-456")
        credentials.start()
        self.addCleanup(credentials.stop)
        post = patch.object(alerts, "_post", return_value=True)
        self.post = post.start()
        self.addCleanup(post.stop)

    def assert_text_parts(self, expected, chart_symbol=None):
        for chat_id in ("123", "-456"):
            calls = [
                call for call in self.post.call_args_list
                if call.args == ("sendMessage", chat_id)
            ]
            self.assertTrue(calls)
            parts = [call.kwargs["json"]["text"] for call in calls]
            self.assertEqual("".join(parts), expected)
            self.assertTrue(all(0 < utf16_length(part) <= 3900 for part in parts))
            for index, call in enumerate(calls):
                payload = call.kwargs["json"]
                self.assertEqual(payload["chat_id"], chat_id)
                self.assertEqual(call.kwargs["timeout"], 20)
                if chart_symbol and index == 0:
                    button = payload["reply_markup"]["inline_keyboard"][0][0]
                    self.assertEqual(button["url"], alerts.tradingview_chart_url(chart_symbol))
                else:
                    self.assertNotIn("reply_markup", payload)

    def test_ascii_message_boundaries_and_multiple_parts(self):
        for length, count in ((1, 1), (3899, 1), (3900, 1), (3901, 2), (7800, 2), (7801, 3)):
            with self.subTest(length=length):
                self.post.reset_mock()
                message = "A" * length
                alerts.send(message, chart_symbol="TEST")
                self.assertEqual(self.post.call_count, count * 2)
                self.assert_text_parts(message, chart_symbol="TEST")

    def test_emoji_and_mixed_unicode_stay_within_utf16_limit(self):
        for message in ("\U0001f7e2" * 3900, "A" * 3899 + "\U0001f7e2" + "\u20b9" * 4000):
            with self.subTest(length=len(message)):
                self.post.reset_mock()
                alerts.send(message)
                self.assert_text_parts(message)

    def test_long_sector_alert_is_preserved_and_split(self):
        message = "EPISODIC PIVOT BUY - TEST\nSector: " + "Specialty Chemicals " * 600
        alerts.send(message, chart_symbol="TEST")
        self.assertGreater(self.post.call_count, 2)
        self.assert_text_parts(message, chart_symbol="TEST")

    def test_large_daily_sector_summary_is_split_without_lost_counts(self):
        source = [
            {"symbol": f"TEST{index}", "sector": f"Sector {index:03d}", "alert_type": "EP"}
            for index in range(150)
        ]
        message = "\n".join(performance.sector_summary(source))
        alerts.send(message)
        self.assertGreater(self.post.call_count, 2)
        self.assert_text_parts(message)

    def test_missing_credentials_print_every_chunk_without_network(self):
        message = "A" * 8000
        with patch.object(settings, "TELEGRAM_BOT_TOKEN", ""), patch("builtins.print") as output:
            alerts.send(message, chart_symbol="TEST")
        self.post.assert_not_called()
        printed = [call.args[0] for call in output.call_args_list]
        self.assertEqual("".join(printed[:-1]), message)
        self.assertTrue(all(utf16_length(part) <= 3900 for part in printed[:-1]))
        self.assertIn("Open TradingView Chart:", printed[-1])

    def test_photo_caption_boundary_and_long_text_fallback(self):
        for caption, fits in (("A" * 1024, True), ("A" * 1025, False),
                              ("\U0001f7e2" * 512, True), ("\U0001f7e2" * 513, False),
                              ("\U0001f7e2" * 5000, False)):
            with self.subTest(length=len(caption), fits=fits):
                self.post.reset_mock()
                alerts.send_photo(b"image", caption=caption, chart_symbol="TEST")
                photos = [call for call in self.post.call_args_list if call.args[0] == "sendPhoto"]
                self.assertEqual(len(photos), 2)
                for call in photos:
                    data = call.kwargs["data"]
                    self.assertEqual(data["caption"], caption if fits else "")
                    self.assertLessEqual(utf16_length(data["caption"]), 1024)
                if fits:
                    self.assertEqual(self.post.call_count, 2)
                else:
                    self.assert_text_parts(caption, chart_symbol="TEST")

    def test_failed_photo_falls_back_to_text(self):
        self.post.side_effect = lambda method, *args, **kwargs: method != "sendPhoto"
        with patch("builtins.print"):
            alerts.send_photo(b"image", caption="Sector: Machinery", chart_symbol="TEST")
        self.assert_text_parts("Sector: Machinery", chart_symbol="TEST")

    def test_send_text_reports_failure_and_still_attempts_remaining_parts(self):
        self.post.side_effect = [False, True]
        self.assertFalse(alerts._send_text("123", ["first", "second"], chart_symbol="TEST"))
        self.assertEqual(self.post.call_count, 2)


if __name__ == "__main__":
    unittest.main()
