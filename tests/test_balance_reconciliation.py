"""Synthetic balance, position and corporate-action reconciliation checks."""

from datetime import date
from decimal import Decimal
import json
import unittest

from dashboard.flex_import import FlexImport, reconcile_balances


D1 = date(2026, 8, 11)
D2 = date(2026, 8, 12)
ZERO = Decimal("0")


def money(value: str) -> Decimal:
    return Decimal(value)


def cash_row(day, level, currency, starting, ending, **components):
    row = {
        "level": level,
        "startingCash": money(starting),
        "endingCash": money(ending),
    }
    row.update({key: money(value) for key, value in components.items()})
    return (day, level, currency), row


def position(quantity, mark, value, *, conid="111", currency="EUR", fx="1", category="STK", side="Long"):
    return {
        "quantity": money(quantity), "mark": money(mark), "multiplier": money("1"),
        "fx_to_base": money(fx), "value": money(value), "category": category,
        "currency": currency, "side": side, "sub_category": "COMMON",
    }


def trade(day, side, quantity, price, *, conid="111", currency="EUR", commission="0"):
    return {
        "report_date": day, "currency": currency, "side": side,
        "quantity": money(quantity), "price": money(price), "multiplier": money("1"),
        "commission": money(commission), "commission_currency": currency, "conid": conid,
    }


def balanced():
    imported = FlexImport("DU_SYNTHETIC", "EUR", "synthetic-digest")
    imported.days = {D1: {}, D2: {}}
    imported.equity = {
        D1: {"total": money("1000"), "cash": money("1000"), "stock": ZERO},
        D2: {"total": money("1000"), "cash": money("900"), "stock": money("100")},
    }
    imported.cash_report = dict([
        cash_row(D1, "BaseCurrency", "EUR", "1000", "1000"),
        cash_row(D1, "Currency", "EUR", "1000", "1000"),
        cash_row(D2, "BaseCurrency", "EUR", "1000", "900", netTradesPurchases="-100"),
        cash_row(D2, "Currency", "EUR", "1000", "900", netTradesPurchases="-100"),
    ])
    imported.positions = {(D2, "111", "EUR"): position("1", "100", "100")}
    imported.trades = {"e1": trade(D2, "buy", "1", "100")}
    return imported


class BalanceReconciliationTest(unittest.TestCase):
    def test_balanced_cash_positions_and_nav_components(self):
        imported = balanced()
        result = reconcile_balances(imported)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["cash"]["nav_cash_exact"], 2)
        self.assertEqual(result["cash"]["nav_cash_mismatch_days"], [])
        self.assertEqual(result["cash"]["trade_component_mismatch_days"], [])
        self.assertEqual(result["positions"]["quantity_mismatch_keys"], 0)
        self.assertEqual(result["positions"]["nav_component_matched_days"], 1)
        self.assertEqual(imported.issues, set())

    def test_short_position_and_sale_proceeds(self):
        imported = FlexImport("DU_SYNTHETIC", "EUR", "synthetic-digest")
        imported.days = {D1: {}, D2: {}}
        imported.equity = {
            D1: {"total": money("1000"), "cash": money("1000"), "stock": ZERO},
            D2: {"total": money("1000"), "cash": money("1100"), "stock": money("-100")},
        }
        imported.cash_report = dict([
            cash_row(D1, "BaseCurrency", "EUR", "1000", "1000"),
            cash_row(D1, "Currency", "EUR", "1000", "1000"),
            cash_row(D2, "BaseCurrency", "EUR", "1000", "1100", netTradesSales="100"),
            cash_row(D2, "Currency", "EUR", "1000", "1100", netTradesSales="100"),
        ])
        imported.positions = {(D2, "333", "EUR"): position("-2", "50", "-100", conid="333", side="Short")}
        imported.trades = {"e1": trade(D2, "sell", "2", "50", conid="333")}
        result = reconcile_balances(imported)
        self.assertEqual(result["positions"]["quantity_mismatch_keys"], 0)
        self.assertEqual(result["positions"]["nav_component_matched_days"], 1)
        self.assertEqual(result["cash"]["trade_component_mismatch_days"], [])
        self.assertEqual(imported.issues, set())

    def test_multi_currency_position_uses_fx_to_base(self):
        imported = FlexImport("DU_SYNTHETIC", "EUR", "synthetic-digest")
        imported.days = {D1: {}, D2: {}}
        imported.equity = {
            D1: {"total": money("90"), "cash": money("90"), "stock": ZERO},
            D2: {"total": money("90"), "cash": ZERO, "stock": money("90")},
        }
        imported.cash_report = dict([
            cash_row(D1, "BaseCurrency", "EUR", "90", "90"),
            cash_row(D1, "Currency", "USD", "100", "100"),
            cash_row(D2, "BaseCurrency", "EUR", "90", "0", netTradesPurchases="-90"),
            cash_row(D2, "Currency", "USD", "100", "0", netTradesPurchases="-100"),
        ])
        imported.positions = {
            (D2, "444", "USD"): position("10", "10", "100", conid="444", currency="USD", fx="0.9")
        }
        imported.trades = {"e1": trade(D2, "buy", "10", "10", conid="444", currency="USD")}
        result = reconcile_balances(imported)
        self.assertEqual(result["positions"]["nav_component_mismatch_days"], [])
        self.assertEqual(result["cash"]["native_continuity_breaks"], {"USD": 0})
        self.assertEqual(imported.issues, set())

    def test_corporate_action_quantity_and_cash_effect(self):
        imported = FlexImport("DU_SYNTHETIC", "EUR", "synthetic-digest")
        imported.days = {D1: {}, D2: {}}
        imported.equity = {
            D1: {"total": money("1000"), "cash": money("900"), "stock": money("100")},
            D2: {"total": money("1015"), "cash": money("900"), "stock": money("115")},
        }
        imported.cash_report = dict([
            cash_row(D1, "BaseCurrency", "EUR", "900", "900"),
            cash_row(D1, "Currency", "EUR", "900", "900"),
            cash_row(D2, "BaseCurrency", "EUR", "900", "900"),
            cash_row(D2, "Currency", "EUR", "900", "900"),
        ])
        imported.positions = {
            (D1, "111", "EUR"): position("1", "100", "100"),
            (D2, "111", "EUR"): position("1", "100", "100"),
            (D2, "222", "EUR"): position("1", "15", "15", conid="222"),
        }
        imported.corporate_action_rows = {"a1": {
            "date": D2, "conid": "222", "currency": "EUR", "quantity": money("1"),
            "amount": money("0"), "proceeds": money("0"), "value": money("15"),
            "fx_to_base": money("1"), "multiplier": money("1"), "action_id": "a1",
        }}
        result = reconcile_balances(imported)
        self.assertEqual(result["positions"]["quantity_mismatch_keys"], 0)
        self.assertEqual(result["corporate_actions"]["quantity_reconciled"], 1)
        self.assertEqual(result["corporate_actions"]["quantity_mismatches"], 0)
        self.assertEqual(imported.issues, set())

    def test_mismatches_are_reported_without_amounts(self):
        imported = balanced()
        imported.positions[(D1, "111", "EUR")] = position("1", "100", "100")
        imported.equity[D1] = {"total": money("1000"), "cash": money("900"), "stock": money("100")}
        imported.cash_report[(D1, "BaseCurrency", "EUR")]["startingCash"] = money("900")
        imported.cash_report[(D1, "BaseCurrency", "EUR")]["endingCash"] = money("900")
        imported.cash_report[(D1, "Currency", "EUR")]["startingCash"] = money("900")
        imported.cash_report[(D1, "Currency", "EUR")]["endingCash"] = money("900")
        imported.cash_report[(D2, "BaseCurrency", "EUR")]["endingCash"] = money("899")
        imported.positions[(D2, "111", "EUR")]["quantity"] = money("3")
        imported.positions[(D2, "111", "EUR")]["value"] = money("300")
        imported.corporate_action_rows = {"a1": {
            "date": D2, "conid": "222", "currency": "EUR", "quantity": money("2"),
            "amount": money("0"), "proceeds": money("0"), "value": money("0"),
            "fx_to_base": money("1"), "multiplier": money("1"), "action_id": "a1",
        }}
        result = reconcile_balances(imported)
        self.assertTrue({
            "cash_balance_nav_mismatch", "position_quantity_mismatch",
            "corporate_action_quantity_mismatch",
        } <= imported.issues)
        self.assertEqual(result["cash"]["nav_cash_mismatch_days"], ["2026-08-12"])
        self.assertEqual(result["positions"]["quantity_mismatch_days"], ["2026-08-12"])
        self.assertEqual(result["corporate_actions"]["quantity_mismatches"], 1)
        text = json.dumps(result, allow_nan=False)
        for private in ("111", "222", "1000", "900", "899"):
            self.assertNotIn(private, text)

    def test_commission_income_and_funding_component_mismatches(self):
        imported = balanced()
        imported.trades["e1"]["commission"] = money("-1")
        imported.cash_report[(D2, "Currency", "EUR")]["commissions"] = money("0")
        imported.cash_report[(D2, "Currency", "EUR")]["dividends"] = money("5")
        imported.cash_report[(D2, "Currency", "EUR")]["depositWithdrawals"] = money("10")
        result = reconcile_balances(imported)
        self.assertTrue({
            "cash_commission_component_mismatch", "cash_income_component_mismatch",
            "cash_funding_component_mismatch",
        } <= imported.issues)
        self.assertEqual(len(result["cash"]["commission_component_mismatch_days"]), 1)
        self.assertEqual(len(result["cash"]["income_component_mismatch_days"]), 1)
        self.assertEqual(len(result["cash"]["funding_component_mismatch_days"]), 1)


if __name__ == "__main__":
    unittest.main()
