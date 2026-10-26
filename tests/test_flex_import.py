"""Synthetic-only regressions for the actual NQC Flex export shape."""

from copy import deepcopy
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

from dashboard.flex_import import BRIDGE_COMPONENTS, FlexError, _instant, read_flex, reconcile_ledger


ACCOUNT = "DU_SYNTHETIC_PRIVATE"


def fixture():
    root = ET.Element("FlexQueryResponse", type="AF", queryName="Synthetic")
    wrapper = ET.SubElement(root, "FlexStatements", count="1")
    statement = ET.SubElement(wrapper, "FlexStatement", accountId=ACCOUNT,
                              fromDate="2026-08-11", toDate="2026-08-11",
                              whenGenerated="2026-08-12;12:00:00 EDT")
    ET.SubElement(statement, "AccountInformation", accountId=ACCOUNT, currency="EUR")
    nav = ET.SubElement(statement, "EquitySummaryInBase")
    ET.SubElement(nav, "EquitySummaryByReportDateInBase", accountId=ACCOUNT,
                  currency="EUR", reportDate="2026-08-10", total="1000")
    ET.SubElement(nav, "EquitySummaryByReportDateInBase", accountId=ACCOUNT,
                  currency="EUR", reportDate="2026-08-11", total="1050")
    bridge = {key: "0" for key in BRIDGE_COMPONENTS}
    ET.SubElement(statement, "ChangeInNAV", **{**bridge, "accountId": ACCOUNT,
                  "currency": "EUR", "fromDate": "2026-08-11", "toDate": "2026-08-11",
                  "startingValue": "1000", "endingValue": "1050", "mtm": "50"})
    for section in ("CashReport", "OpenPositions", "Transfers", "CorporateActions"):
        ET.SubElement(statement, section)
    trades = ET.SubElement(statement, "Trades")
    ET.SubElement(trades, "Trade", accountId=ACCOUNT, ibExecID="PRIVATE-EXECUTION",
                  dateTime="2026-08-11;10:00:00 EDT", reportDate="2026-08-11",
                  tradeDate="2026-08-11", levelOfDetail="EXECUTION", buySell="BUY",
                  quantity="1", tradePrice="100.10", symbol="SYNTHETIC", conid="111",
                  currency="USD", ibCommission="-0.30", ibCommissionCurrency="USD",
                  taxes="0", fxRateToBase="0.90", orderReference="nova-private-1",
                  transactionID="private-transaction", transactionType="ExchTrade",
                  assetCategory="STK", multiplier="1", origTransactionID="0", origOrderID="0")
    cash = ET.SubElement(statement, "CashTransactions")
    ET.SubElement(cash, "CashTransaction", accountId=ACCOUNT, transactionID="PRIVATE-CASH",
                  dateTime="2026-08-10", reportDate="2026-08-11", levelOfDetail="DETAIL",
                  type="Dividends", currency="USD", amount="2.50", fxRateToBase="0.90")
    return root


class FlexImportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "synthetic.xml"
        self.root = fixture()

    def write(self):
        self.path.write_bytes(ET.tostring(self.root, encoding="utf-8"))
        return self.path

    def read(self):
        return read_flex(self.write())

    def ledger(self, **changes):
        row = {"execution_id": "PRIVATE-EXECUTION", "timestamp": "2026-08-11T14:00:00Z",
               "symbol": "SYNTHETIC", "currency": "USD", "side": "bot", "quantity": "1",
               "price": "100.1", "order_ref": "nova-private-1"}
        row.update(changes)
        path = Path(self.temp.name) / "ledger.csv"
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
        return path

    def test_private_records_exact_money_date_only_cash_and_redacted_read_only_report(self):
        imported = self.read()
        trade = imported.trades["PRIVATE-EXECUTION"]
        self.assertEqual(str(trade["commission"]), "-0.30")
        self.assertEqual(str(trade["price"]), "100.10")
        self.assertEqual(trade["executed_at"], datetime(2026, 8, 11, 14, tzinfo=timezone.utc))
        self.assertIsNone(imported.cash["PRIVATE-CASH"]["effective_at"])
        self.assertEqual(imported.cash["PRIVATE-CASH"]["classification"], "performance")
        before = self.path.read_bytes()
        ledger = self.ledger()
        ledger_before = ledger.read_bytes()
        result = reconcile_ledger(imported, ledger)
        self.assertEqual(result["status"], "matched_observed_subset")
        self.assertEqual(result["matched_execution_ids"], 1)
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(ledger_before, ledger.read_bytes())
        report = imported.report()
        self.assertIsNone(report["verified_return_pct"])
        self.assertFalse(report["calendar_coverage_verified"])
        text = json.dumps({**report, "ledger": result}, allow_nan=False)
        for private in (ACCOUNT, "PRIVATE-EXECUTION", "PRIVATE-CASH", "SYNTHETIC", "100.10", "nova-private"):
            self.assertNotIn(private, text)

    def test_identical_duplicates_count_once_and_conflicts_stop(self):
        trades = self.root.find(".//Trades")
        trades.append(deepcopy(trades[0]))
        imported = self.read()
        self.assertEqual(len(imported.trades), 1)
        self.assertEqual(imported.duplicate_rows, 1)
        trades[1].set("tradePrice", "101")
        with self.assertRaisesRegex(FlexError, "conflicting_duplicate:execution"):
            self.read()

    def test_missing_fees_nonfinite_values_and_wrong_details_stop(self):
        for key, value, reason in (
            ("ibCommission", "", "missing_field:ibCommission"),
            ("tradePrice", "NaN", "invalid_number:tradePrice"),
            ("quantity", "-1", "trade_quantity_sign_mismatch"),
            ("fxRateToBase", "0", "invalid_trade_fx_rate"),
            ("ibExecID", "", "missing_field:ibExecID"),
            ("levelOfDetail", "SUMMARY", "requires_execution_detail"),
            ("ibCommissionCurrency", "US$", "invalid_currency"),
        ):
            with self.subTest(key=key):
                self.root = fixture()
                self.root.find(".//Trade").set(key, value)
                with self.assertRaisesRegex(FlexError, reason):
                    self.read()

    def test_mixed_accounts_currencies_and_unknown_child_records_stop(self):
        for tag, key, value, reason in (
            ("Trade", "accountId", "OTHER", "mixed_accounts"),
            ("AccountInformation", "currency", "USD", "nav_bridge_scope_mismatch"),
            ("FlexStatement", "accountId", "", "missing_field:accountId"),
            ("FlexStatements", "count", "2", "statement_count_mismatch"),
        ):
            with self.subTest(tag=tag):
                self.root = fixture()
                self.root.find(".//" + tag).set(key, value)
                with self.assertRaisesRegex(FlexError, reason):
                    self.read()
        self.root = fixture()
        self.root.find(".//Trade").tag = "Order"
        with self.assertRaisesRegex(FlexError, "unexpected_record_type:Trades"):
            self.read()

    def test_timezone_labels_and_both_folds_are_explicit(self):
        self.assertEqual(_instant("2026-11-01;01:30:00 EST").hour, 6)
        self.assertEqual(_instant("2026-11-01;01:30:00 EDT").hour, 5)
        for value in ("2026-12-01;10:00:00 EDT", "2026-03-08;02:30:00 EST",
                      "2026-08-11;10:00:00", "2026-08-11;10:00:00 CET"):
            with self.subTest(value=value), self.assertRaises(FlexError):
                _instant(value)

    def test_funding_is_not_inferred_from_income_and_unknown_types_require_review(self):
        cash = self.root.find(".//CashTransaction")
        cash.set("type", "Deposits & Withdrawals")
        imported = self.read()
        self.assertEqual(imported.report()["cash_classifications"]["external_flow"], 1)
        self.assertIn("funding_transactions_do_not_match_nav_bridge", imported.issues)
        cash.set("type", "Unknown Type")
        self.assertIn("unclassified_cash_activity", self.read().issues)

    def test_bridge_closing_nav_and_chain_mismatches_are_flagged(self):
        self.root.find(".//ChangeInNAV").set("mtm", "40")
        self.assertIn("nav_bridge_does_not_balance", self.read().issues)
        self.root = fixture()
        self.root.find(".//ChangeInNAV").set("endingValue", "1001")
        self.assertIn("closing_nav_mismatch", self.read().issues)
        self.root = fixture()
        wrapper = self.root.find("FlexStatements")
        second = deepcopy(wrapper[0])
        second.set("fromDate", "2026-08-12")
        second.set("toDate", "2026-08-12")
        second.find("Trades").clear()
        second.find("CashTransactions").clear()
        second.find("EquitySummaryInBase").clear()
        ET.SubElement(second.find("EquitySummaryInBase"), "EquitySummaryByReportDateInBase",
                      accountId=ACCOUNT, currency="EUR", reportDate="2026-08-12", total="1050")
        second.find("ChangeInNAV").set("fromDate", "2026-08-12")
        second.find("ChangeInNAV").set("toDate", "2026-08-12")
        wrapper.append(second)
        wrapper.set("count", "2")
        self.assertIn("nav_chain_break", self.read().issues)

    def test_corrections_corporate_actions_and_transfers_are_not_silently_replayed(self):
        self.root.find(".//Trade").set("origTransactionID", "123")
        ET.SubElement(self.root.find(".//CorporateActions"), "CorporateAction", accountId=ACCOUNT)
        ET.SubElement(self.root.find(".//Transfers"), "Transfer", accountId=ACCOUNT)
        result = self.read()
        self.assertTrue({"trade_corrections_require_review", "corporate_actions_require_reconciliation",
                         "transfers_require_classification"} <= result.issues)
        self.assertEqual(result.trades["PRIVATE-EXECUTION"]["original_transaction_id"], "123")
        self.assertIsNone(result.report()["verified_return_pct"])

    def test_ledger_price_timestamp_missing_id_and_outside_period_are_distinguished(self):
        imported = self.read()
        result = reconcile_ledger(imported, self.ledger(price="101", timestamp="2026-08-11T13:00:00Z"))
        self.assertEqual(result["field_mismatches"]["price"], 1)
        self.assertEqual(result["field_mismatches"]["executed_at"], 1)
        self.assertEqual(result["local_minus_statement_timestamp_seconds"], {"-3600": 1})
        result = reconcile_ledger(imported, self.ledger(execution_id="missing"))
        self.assertEqual(result["local_missing_from_statement_period"], 1)
        result = reconcile_ledger(imported, self.ledger(execution_id="future", timestamp="2026-10-26T10:00:00Z"))
        self.assertEqual(result["local_outside_statement_period"], 1)
        self.assertEqual(result["statement_only_executions"], 1)

    def test_signed_sell_and_fee_currency_are_preserved_without_guessing_conversion(self):
        row = self.root.find(".//Trade")
        row.set("buySell", "SELL")
        row.set("quantity", "-2")
        row.set("ibCommissionCurrency", "EUR")
        imported = self.read()
        trade = imported.trades["PRIVATE-EXECUTION"]
        self.assertEqual(str(trade["quantity"]), "2")
        self.assertEqual(trade["side"], "sell")
        self.assertEqual(trade["commission_currency"], "EUR")
        self.assertEqual(str(trade["commission"]), "-0.30")
        self.assertIn("commission_fx_requires_reconciliation", imported.issues)

    def test_unsafe_xml_error_responses_size_limit_and_cli_redaction(self):
        for raw in (b'<!DOCTYPE x [<!ENTITY a "secret">]><x/>', b'<broken',
                    '<FlexQueryResponse/>'.encode("utf-16"), b'<FlexStatementResponse><ErrorMessage>secret</ErrorMessage></FlexStatementResponse>'):
            self.path.write_bytes(raw)
            with self.subTest(raw=raw), self.assertRaises(FlexError):
                read_flex(self.path)
        self.write()
        with patch("dashboard.flex_import.MAX_XML_BYTES", 10), self.assertRaisesRegex(FlexError, "xml_too_large"):
            read_flex(self.path)
        self.path.write_bytes(b'<broken SECRET_ACCOUNT="PRIVATE"')
        process = subprocess.run([sys.executable, "-m", "dashboard.flex_import", str(self.path)],
                                 capture_output=True, text=True, check=False)
        self.assertEqual(process.returncode, 1)
        self.assertEqual(json.loads(process.stdout)["reason"], "malformed_xml")
        self.assertNotIn("PRIVATE", process.stdout + process.stderr)


if __name__ == "__main__":
    unittest.main()
