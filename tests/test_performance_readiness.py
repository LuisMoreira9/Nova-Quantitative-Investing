"""Offline regression for the existing CSV accounting boundary; no broker access."""

import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from dashboard.performance_readiness import audit_performance_inputs


NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
TIME = "2026-10-21T10:00:00+00:00"
FILL = {"execution_id": "private-fill-id", "timestamp": TIME, "strategy": "Example",
        "order_ref": "nova-example-1", "symbol": "TEST", "currency": "USD",
        "side": "bot", "quantity": "1", "price": "100"}


class PerformanceReadinessTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, rows):
        with (self.root / name).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def audit(self):
        return audit_performance_inputs(self.root, now=NOW)

    def test_clean_inputs_still_block_returns_and_never_write(self):
        self.write("ibkr_account_history.csv", [{"timestamp": TIME, "equity": "1000"}])
        self.write("nova_strategy_execution_ledger.csv", [FILL, {**FILL, "side": "buy", "price": "100.0"}])
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        result = self.audit()
        self.assertEqual(result["status"], "blocked")
        self.assertIsNone(result["verified_return_pct"])
        self.assertEqual(result["equity_observations"]["status"], "observed")
        self.assertEqual(result["nova_fills"]["valid_unique_rows"], 1)
        self.assertEqual(result["nova_fills"]["duplicate_rows"], 1)
        self.assertEqual(result["nova_fills"]["conflicting_keys"], 0)
        self.assertGreater(len(result["return_blockers"]), 0)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})
        serialized = json.dumps(result, allow_nan=False)
        for private in ("private-fill-id", "nova-example-1", '"1000"', '"TEST"'):
            self.assertNotIn(private, serialized)

    def test_conflicting_fill_is_not_counted_as_valid(self):
        self.write("nova_strategy_execution_ledger.csv", [FILL, {**FILL, "quantity": "2"}])
        result = self.audit()["nova_fills"]
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["conflicting_keys"], 1)
        self.assertEqual(result["valid_unique_rows"], 0)

    def test_bad_time_money_and_missing_cells_are_not_silently_accepted(self):
        self.write("ibkr_account_history.csv", [
            {"timestamp": TIME, "equity": "1000"},
            {"timestamp": "2026-10-21T11:00:00", "equity": "1100"},
            {"timestamp": "bad", "equity": "1000"},
            {"timestamp": "2026-10-23T10:00:00Z", "equity": "1000"},
            *[{"timestamp": TIME, "equity": value} for value in ("", "NaN", "Infinity", "0", "-1")],
        ])
        with (self.root / "ibkr_account_history.csv").open("a") as stream:
            stream.write("2026-10-21T12:00:00Z\n")
        result = self.audit()["equity_observations"]
        self.assertEqual(result["valid_unique_rows"], 1)
        self.assertEqual(result["invalid_rows"], 9)
        self.assertEqual(result["status"], "partial")

    def test_equity_conflicts_use_utc_identity(self):
        self.write("ibkr_account_history.csv", [
            {"timestamp": TIME, "equity": "1000"},
            {"timestamp": "2026-10-21T11:00:00+01:00", "equity": "1100"},
        ])
        self.assertEqual(self.audit()["equity_observations"]["conflicting_keys"], 1)

    def test_missing_empty_and_unreadable_inputs(self):
        self.assertEqual(self.audit()["nova_fills"]["read_error"], "missing_file")
        self.write("nova_strategy_execution_ledger.csv", [{"timestamp": TIME}])
        self.assertEqual(self.audit()["nova_fills"]["read_error"], "missing_columns")
        (self.root / "ibkr_account_history.csv").write_bytes(b"\xff")
        self.assertEqual(self.audit()["equity_observations"]["read_error"], "unreadable_csv")
        (self.root / "ibkr_account_history.csv").write_text("timestamp,equity\n")
        self.assertEqual(self.audit()["equity_observations"]["status"], "unavailable")

    def test_invalid_fill_fields(self):
        bad = [{**FILL, field: value} for field, value in (
            ("execution_id", ""), ("order_ref", "manual"), ("currency", "usd"),
            ("currency", "€UR"), ("side", "unknown"), ("quantity", "0"),
            ("price", "NaN"), ("strategy", ""), ("symbol", ""))]
        self.write("nova_strategy_execution_ledger.csv", bad)
        self.assertEqual(self.audit()["nova_fills"]["invalid_rows"], len(bad))

    def test_duplicate_header_and_malformed_csv(self):
        path = self.root / "ibkr_account_history.csv"
        path.write_text("timestamp,equity,equity\n")
        self.assertEqual(self.audit()["equity_observations"]["read_error"], "duplicate_columns")
        path.write_text('timestamp,equity\n"unterminated,1000\n')
        self.assertEqual(self.audit()["equity_observations"]["read_error"], "unreadable_csv")


if __name__ == "__main__":
    unittest.main()
