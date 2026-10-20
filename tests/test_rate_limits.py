import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from threading import Lock
from unittest.mock import Mock, patch

from scanner import upstox, universe
from config import settings


class Clock:
    def __init__(self):
        self.now = 10000.0
        self.lock = Lock()

    def monotonic(self):
        with self.lock:
            return self.now

    def sleep(self, seconds):
        with self.lock:
            self.now += seconds


class UpstoxRateLimitTests(unittest.TestCase):
    def test_sustained_requests_stay_within_every_sliding_budget(self):
        clock = Clock()
        self.assertEqual(settings.UPSTOX_REQUESTS_PER_SECOND, 8)
        self.assertEqual(settings.UPSTOX_REQUESTS_PER_MINUTE, 480)
        self.assertEqual(settings.UPSTOX_REQUESTS_PER_30_MINUTES, 1950)
        limiter = upstox.RateLimiter(settings.UPSTOX_REQUESTS_PER_MINUTE)
        starts = []
        with (patch.object(upstox.time, "monotonic", clock.monotonic),
              patch.object(upstox.time, "sleep", clock.sleep)):
            for _ in range(4001):
                limiter.wait()
                starts.append(clock.now)
        self.assertAlmostEqual(limiter.interval, 1800 / 1950)
        for seconds, limit in ((1, 8), (60, 480), (1800, 1950)):
            left = 0
            maximum = 0
            for right, timestamp in enumerate(starts):
                # Ignore accumulated floating-point error at the window boundary.
                while starts[left] <= timestamp - seconds + 1e-8:
                    left += 1
                maximum = max(maximum, right - left + 1)
            self.assertLessEqual(maximum, limit, (seconds, maximum))
        self.assertAlmostEqual(starts[-1] - starts[0], 4000 * 1800 / 1950, places=6)

    def test_parallel_workers_share_admission_and_cooldown(self):
        clock = Clock()
        limiter = upstox.RateLimiter(450, 8, 1800)
        with (patch.object(upstox.time, "monotonic", clock.monotonic),
              patch.object(upstox.time, "sleep", clock.sleep)):
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda _: limiter.wait(), range(100)))
            self.assertGreaterEqual(limiter.next_time, 10100.0)
            limiter.cooldown(90)
            blocked_until = limiter.blocked_until
            limiter.wait()
            self.assertGreaterEqual(clock.now, blocked_until)

    def test_all_three_budgets_affect_spacing_and_invalid_values_fail(self):
        self.assertEqual(upstox.RateLimiter(60, 8, 1800).interval, 1)
        self.assertEqual(upstox.RateLimiter(450, 0.5, 1800).interval, 2)
        self.assertEqual(upstox.RateLimiter(450, 8, 900).interval, 2)
        for minute, second, halfhour in ((501, 8, 1800), (450, 11, 1800),
                                        (450, 8, 2001), (0, 8, 1800)):
            with self.subTest(budgets=(minute, second, halfhour)):
                with self.assertRaises(ValueError):
                    upstox.RateLimiter(minute, second, halfhour)

    def test_retry_after_is_not_capped_and_retry_consumes_another_slot(self):
        response = Mock(status_code=429, headers={"Retry-After": "90"})
        success = Mock(status_code=200)
        success.json.return_value = {"status": "success"}
        limiter = Mock()
        with (patch.object(upstox, "rate_limiter", limiter),
              patch.object(upstox, "headers", return_value={}),
              patch.object(upstox.requests, "get", side_effect=[response, success]) as get,
              patch("builtins.print")):
            payload = upstox._request_json("https://example.invalid", retries=1)
        self.assertEqual(payload, {"status": "success"})
        self.assertEqual(limiter.wait.call_count, 2)
        limiter.cooldown.assert_called_once_with(90.0)
        self.assertEqual(get.call_count, 2)

    def test_final_429_still_sets_shared_cooldown_and_surfaces_error(self):
        response = Mock(status_code=429, headers={"Retry-After": "75"})
        with (patch.object(upstox, "rate_limiter") as limiter,
              patch.object(upstox, "headers", return_value={}),
              patch.object(upstox.requests, "get", return_value=response),
              patch("builtins.print")):
            with self.assertRaisesRegex(RuntimeError, "HTTP 429"):
                upstox._request_json("https://example.invalid", retries=0)
        limiter.cooldown.assert_called_once_with(75)

    def test_retry_after_http_date_and_invalid_values(self):
        with patch.object(upstox, "datetime") as now:
            now.now.return_value = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
            self.assertEqual(upstox._retry_delay("Sun, 18 Oct 2026 12:02:00 GMT"), 120)
        for invalid in ("bad", "-5", "nan", "inf"):
            with self.subTest(value=invalid), patch("builtins.print") as log:
                self.assertEqual(upstox._retry_delay(invalid), 2)
                log.assert_called_once()

    def test_ipo_pages_use_shared_request_path(self):
        payloads = [
            {"data": [{"symbol": "TEST", "listing_date": "2026-01-10"}],
             "meta_data": {"page": {"total_pages": 2}}},
            {"data": [], "meta_data": {"page": {"total_pages": 2}}},
        ]
        with patch.object(upstox, "_request_json", side_effect=payloads) as request:
            listings = universe._listed_ipo_dates()
            self.assertEqual(listings, {"TEST": "2026-01-10"})
            self.assertEqual(
                universe.recent_ipo_listing_dates(listings, date(2026, 10, 20)),
                {"TEST": "2026-01-10"},
            )
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[0].kwargs["params"]["page_number"], 1)
        self.assertEqual(request.call_args_list[1].kwargs["params"]["page_number"], 2)
        self.assertEqual(request.call_args.kwargs["retries"], 2)


if __name__ == "__main__":
    unittest.main()
