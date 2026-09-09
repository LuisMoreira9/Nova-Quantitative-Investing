"""Offline regression checks for TWS execution times and public fill payloads."""

import os
import unittest
from unittest.mock import patch

import pandas as pd

from dashboard.timestamps import ibkr_execution_timestamp
from dashboard.strategy_attribution import _parse_execution_timestamp
from dashboard.supabase_publisher import public_executions


class ExecutionTimestampTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"IBKR_TWS_TIMEZONE": "Europe/Lisbon"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_summer_fill_is_not_mistaken_for_utc(self):
        self.assertEqual(
            ibkr_execution_timestamp("20260908  09:18:50").isoformat(),
            "2026-09-08T08:18:50+00:00",
        )

    def test_winter_fill_has_no_summer_offset(self):
        self.assertEqual(
            ibkr_execution_timestamp("20260108  09:18:50").isoformat(),
            "2026-01-08T09:18:50+00:00",
        )

    def test_aware_iso_preserves_instant_without_double_correction(self):
        for text in ("2026-09-08T09:18:50+01:00", "2026-09-08T08:18:50Z"):
            with self.subTest(text=text):
                result = ibkr_execution_timestamp(text)
                self.assertEqual(result.isoformat(), "2026-09-08T08:18:50+00:00")
                self.assertEqual(ibkr_execution_timestamp(result.isoformat()), result)

    def test_naive_iso_uses_configured_tws_timezone(self):
        with patch.dict(os.environ, {"IBKR_TWS_TIMEZONE": "America/New_York"}):
            self.assertEqual(
                ibkr_execution_timestamp("2026-09-08T09:18:50").isoformat(),
                "2026-09-08T13:18:50+00:00",
            )

    def test_default_timezone(self):
        os.environ.pop("IBKR_TWS_TIMEZONE", None)
        self.assertEqual(
            ibkr_execution_timestamp("20260908 09:18:50").isoformat(),
            "2026-09-08T08:18:50+00:00",
        )

    def test_invalid_values_are_missing_in_attribution(self):
        for text in (None, "", "not-a-date", "20260230 09:18:50"):
            with self.subTest(text=text):
                self.assertIsNone(ibkr_execution_timestamp(text))
                self.assertTrue(pd.isna(_parse_execution_timestamp(text)))

    def test_publisher_and_attribution_agree_and_invalid_fill_is_skipped(self):
        fill = {
            "source": "Nova ledger", "status": "filled", "execution_id": "test-fill",
            "submitted_at": "20260908  09:18:50", "filled_quantity": 1,
            "filled_avg_price": 100, "side": "SLD", "symbol": "SIE",
        }
        rows = public_executions({"executions": [fill, {**fill, "submitted_at": "bad"}]})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["executed_at"], "2026-09-08T08:18:50+00:00")
        self.assertEqual(rows[0]["executed_at"], _parse_execution_timestamp(fill["submitted_at"]).isoformat())
        self.assertEqual(rows[0]["side"], "sell")
        self.assertNotEqual(rows[0]["id"], fill["execution_id"])


if __name__ == "__main__":
    unittest.main()
