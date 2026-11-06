from datetime import datetime, timezone
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError
from email_auth import _expiry_text


class EmailTimeTests(unittest.TestCase):
    def test_expiry_uses_athens_winter_and_summer_clock(self):
        self.assertEqual(_expiry_text(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()), '01/01/2026 02:00 (ώρα Ελλάδας)')
        self.assertEqual(_expiry_text(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp()), '01/07/2026 03:00 (ώρα Ελλάδας)')

    def test_windows_timezone_fallback_still_formats_local_time(self):
        # Patch only the constructor while keeping the real TZif reader available.
        from zoneinfo import ZoneInfo
        class MissingSystemZone:
            def __new__(cls, *args):
                raise ZoneInfoNotFoundError
            from_file = staticmethod(ZoneInfo.from_file)
        with patch('email_auth.ZoneInfo', MissingSystemZone):
            self.assertIn('02:00', _expiry_text(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()))
