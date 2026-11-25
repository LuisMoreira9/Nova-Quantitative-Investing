"""Synthetic-only regressions for the private execution staging ledger."""

from copy import deepcopy
import csv
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from xml.etree import ElementTree as ET

from dashboard.accounting_ledger import stage_ledger
from dashboard.flex_import import FlexError
from tests.test_flex_import import ACCOUNT, fixture


class AccountingLedgerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.xml = self.directory / "synthetic.xml"
        self.ledger = self.directory / "ledger.csv"
        self.output = self.directory / "accounting.sqlite"
        self.root = fixture()
        self.xml.write_bytes(ET.tostring(self.root, encoding="utf-8"))

    def write_ledger(self, rows):
        fields = ("execution_id", "timestamp", "strategy", "order_ref", "symbol",
                  "currency", "side", "quantity", "price")
        with self.ledger.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def matching_row(self, **changes):
        row = {
            "execution_id": "PRIVATE-EXECUTION", "timestamp": "2026-08-11T15:00:00Z",
            "strategy": "Synthetic strategy", "order_ref": "nova-private-1",
            "symbol": "SYNTHETIC", "currency": "USD", "side": "bot",
            "quantity": "1", "price": "100.1",
        }
        row.update(changes)
        return row

    def query(self, sql, parameters=()):
        connection = sqlite3.connect(self.output)
        try:
            return connection.execute(sql, parameters).fetchall()
        finally:
            connection.close()

    def metadata(self):
        return dict(self.query("SELECT key, value FROM metadata"))

    def test_stages_the_matched_fill_with_timestamp_provenance(self):
        self.write_ledger([self.matching_row()])
        summary = stage_ledger(self.xml, self.ledger, self.output)

        self.assertEqual(summary["matched_executions"], 1)
        self.assertEqual(summary["statement_only_executions"], 0)
        self.assertEqual(summary["timestamp_offset_counts"], {"3600": 1})
        self.assertEqual(self.metadata()["opening_baseline_accepted"], "false")
        self.assertEqual(
            self.metadata()["source_sha256"],
            sha256(self.xml.read_bytes()).hexdigest(),
        )
        self.assertNotIn(ACCOUNT, json.dumps(self.metadata()))

        row = self.query(
            "SELECT record_origin, statement_time_raw, statement_time_utc, "
            "legacy_time_raw, legacy_time_utc, time_delta_seconds, review_reason "
            "FROM executions WHERE execution_id = ?",
            ("PRIVATE-EXECUTION",),
        )[0]
        self.assertEqual(row[0], "statement_and_ledger")
        self.assertEqual(row[1], "2026-08-11;10:00:00 EDT")
        self.assertEqual(row[2], "2026-08-11T14:00:00+00:00")
        self.assertEqual(row[3], "2026-08-11T15:00:00Z")
        self.assertEqual(row[4], "2026-08-11T15:00:00+00:00")
        self.assertEqual(row[5], 3600)
        self.assertEqual(row[6], "timestamp_offset")

    def test_preserves_statement_only_and_ledger_only_fills(self):
        trades = self.root.find(".//Trades")
        extra = deepcopy(trades[0])
        extra.set("ibExecID", "PRIVATE-EXECUTION-2")
        extra.set("dateTime", "2026-08-11;11:00:00 EDT")
        extra.set("transactionID", "private-transaction-2")
        trades.append(extra)
        self.xml.write_bytes(ET.tostring(self.root, encoding="utf-8"))
        self.write_ledger([
            self.matching_row(),
            self.matching_row(
                execution_id="PRIVATE-LEDGER-ONLY",
                timestamp="2026-10-26T10:00:00Z",
                order_ref="nova-private-2",
            ),
        ])
        summary = stage_ledger(self.xml, self.ledger, self.output)

        self.assertEqual(summary["statement_executions"], 2)
        self.assertEqual(summary["observed_executions"], 2)
        self.assertEqual(summary["matched_executions"], 1)
        self.assertEqual(summary["statement_only_executions"], 1)
        self.assertEqual(summary["ledger_only_executions"], 1)
        rows = dict(self.query("SELECT execution_id, review_reason FROM executions"))
        self.assertEqual(rows["PRIVATE-EXECUTION-2"], "statement_only")
        self.assertEqual(rows["PRIVATE-LEDGER-ONLY"], "ledger_only_outside_period")

    def test_flags_field_mismatches_and_rebuilds_idempotently(self):
        self.write_ledger([self.matching_row(price="101", timestamp="2026-08-11T14:00:00Z")])
        stage_ledger(self.xml, self.ledger, self.output)
        first = self.query("SELECT * FROM executions ORDER BY execution_id")
        first_metadata = self.metadata()
        stage_ledger(self.xml, self.ledger, self.output)

        self.assertEqual(first, self.query("SELECT * FROM executions ORDER BY execution_id"))
        self.assertEqual(first_metadata, self.metadata())
        row = self.query(
            "SELECT field_mismatches, review_reason FROM executions WHERE execution_id = ?",
            ("PRIVATE-EXECUTION",),
        )[0]
        self.assertEqual(row, ("price", "field_mismatch"))
        self.assertEqual(self.metadata()["field_mismatch_counts"], '{"price": 1}')
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_inputs_are_not_modified(self):
        self.write_ledger([self.matching_row()])
        xml_before = self.xml.read_bytes()
        ledger_before = self.ledger.read_bytes()
        stage_ledger(self.xml, self.ledger, self.output)
        self.assertEqual(xml_before, self.xml.read_bytes())
        self.assertEqual(ledger_before, self.ledger.read_bytes())

    def test_rejects_a_directory_output(self):
        self.write_ledger([self.matching_row()])
        with self.assertRaisesRegex(FlexError, "output_is_directory"):
            stage_ledger(self.xml, self.ledger, self.directory)

    def test_cli_reports_only_redacted_counts(self):
        self.write_ledger([self.matching_row()])
        completed = subprocess.run(
            [sys.executable, "-m", "dashboard.accounting_ledger", str(self.xml),
             "--ledger", str(self.ledger), "--output", str(self.output)],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, check=True,
        )
        report = json.loads(completed.stdout)
        self.assertEqual(report["status"], "staged")
        self.assertEqual(report["statement_executions"], 1)
        for private in (ACCOUNT, "PRIVATE-EXECUTION", "SYNTHETIC", "100.1", "nova-private"):
            self.assertNotIn(private, completed.stdout)


if __name__ == "__main__":
    unittest.main()
