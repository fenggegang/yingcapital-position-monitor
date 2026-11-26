import tempfile
import unittest
from email.utils import formatdate
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from yingcapital_monitor.app import _fetch_operations
from yingcapital_monitor.binance import BinanceCooldown, BinanceError
from yingcapital_monitor.config import Settings


class CooldownTests(unittest.TestCase):
    def test_all_traders_share_deadline_across_runs(self):
        with tempfile.TemporaryDirectory() as root, patch("time.time", return_value=1000):
            def settings(portfolio):
                return Settings(portfolio, "test", "UTC", Path(root) / f"{portfolio}.json")

            limited = HTTPError("https://example.invalid", 429, "", {"Retry-After": "90"}, None)
            with patch("urllib.request.urlopen", side_effect=limited) as request:
                with self.assertRaises(BinanceCooldown):
                    _fetch_operations(settings("1"))
                with self.assertRaisesRegex(BinanceError, "冷却中"):
                    _fetch_operations(settings("2"))
                self.assertEqual(request.call_count, 1)
            with patch("time.time", return_value=1091), patch(
                "yingcapital_monitor.app.BinanceClient.fetch_operations", return_value="ok"
            ):
                self.assertEqual(_fetch_operations(settings("2")), "ok")

    def test_http_date_and_missing_retry_after(self):
        with patch("time.time", return_value=1000):
            self.assertEqual(BinanceCooldown(429, formatdate(5000, usegmt=True)).until, 5000)
            self.assertEqual(BinanceCooldown(418, None).until, 4600)
            self.assertEqual(BinanceCooldown(429, "invalid").until, 1900)
