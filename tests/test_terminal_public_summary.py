"""Offline checks for the terminal whitelist summary and member rows."""

import unittest

from dashboard.supabase_publisher import (
    TERMINAL_PUBLIC_FORBIDDEN_FIELDS,
    build_terminal_member_rows,
    build_terminal_public_summary,
)


def _public(**over):
    base = {
        "schema_version": 3,
        "loaded_at": "2026-09-29T08:26:47+00:00",
        "base_currency": "EUR",
        "account": {"equity": "110.0", "cash": "10.0", "buying_power": "200.0"},
        "history": [
            {"timestamp": "2026-09-29T08:20:00+00:00", "equity": 100.0},
            {"timestamp": "2026-09-29T08:26:47+00:00", "equity": 110.0},
        ],
        "positions": [{"symbol": "SPY", "con_id": 123, "local_symbol": "SPY"}],
    }
    base.update(over)
    return base


class TerminalPublicSummaryTest(unittest.TestCase):
    def test_ready_row_rebases_to_first_observation(self):
        row = build_terminal_public_summary(_public())
        assert row is not None
        self.assertEqual(row["status"], "ready")
        self.assertEqual(row["designation"], "paper trading")
        self.assertAlmostEqual(row["normalized_index"], 110.0)
        self.assertIsNone(row["verified_return_pct"])
        self.assertAlmostEqual(row["drawdown_pct"], 0.0)
        for forbidden in TERMINAL_PUBLIC_FORBIDDEN_FIELDS:
            self.assertNotIn(forbidden, row)
        self.assertIn("not flow-adjusted", row["coverage_notes"])
        self.assertIn("unadjusted for funding", row["coverage_notes"])

    def test_drawdown_reports_peak_to_trough(self):
        public = _public(history=[
            {"timestamp": "2026-09-29T08:20:00+00:00", "equity": 100.0},
            {"timestamp": "2026-09-29T08:21:00+00:00", "equity": 90.0},
            {"timestamp": "2026-09-29T08:26:47+00:00", "equity": 95.0},
        ])
        public["account"] = {"equity": "95.0"}
        row = build_terminal_public_summary(public)
        assert row is not None
        self.assertAlmostEqual(row["normalized_index"], 95.0)
        self.assertAlmostEqual(row["drawdown_pct"], -10.0)

    def test_single_point_is_flat(self):
        public = _public(history=[{"timestamp": "2026-09-29T08:26:47+00:00", "equity": 100.0}])
        public["account"] = {"equity": "100.0"}
        row = build_terminal_public_summary(public)
        assert row is not None
        self.assertAlmostEqual(row["normalized_index"], 100.0)
        self.assertAlmostEqual(row["drawdown_pct"], 0.0)

    def test_missing_observation_skips_publish(self):
        self.assertIsNone(build_terminal_public_summary(_public(loaded_at=None)))
        self.assertIsNone(build_terminal_public_summary(_public(loaded_at="not-a-time")))

    def test_incomplete_account_is_partial_not_invented(self):
        row = build_terminal_public_summary(_public(account={}, history=[]))
        assert row is not None
        self.assertEqual(row["status"], "partial")
        self.assertIsNone(row["normalized_index"])
        self.assertIsNone(row["verified_return_pct"])
        self.assertIsNone(row["drawdown_pct"])

    def test_member_rows_carry_bundle_and_daily_key(self):
        latest, daily = build_terminal_member_rows(_public())
        assert latest is not None and daily is not None
        self.assertEqual(latest["bundle"]["account"]["equity"], "110.0")
        self.assertEqual(daily["scope"], "account")
        self.assertEqual(daily["day"], "2026-09-29")
        self.assertAlmostEqual(daily["metrics"]["normalized_index"], 110.0)
        self.assertIsNone(daily["metrics"]["verified_return_pct"])

    def test_member_rows_missing_without_observation(self):
        self.assertEqual(build_terminal_member_rows(_public(loaded_at="bad")), (None, None))


if __name__ == "__main__":
    unittest.main()
