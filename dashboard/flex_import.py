"""Read the club's daily Activity Flex XML export locally; never publish or trade.

Supported contract: NQC Accounting XML, UTF-8, ISO dates, timezone-tagged times,
daily statements, execution-level trades and detail-level cash transactions.
Only the redacted reconciliation report is printed. No return is calculated.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

from dashboard.performance_readiness import _timestamp


MAX_XML_BYTES = 64 * 1024 * 1024
ROUNDING = Decimal("0.02")
# Mark-to-market bridge: realized/changeInUnrealized are alternative views,
# and twr is a percentage, so none of these is an additive NAV component.
BRIDGE_COMPONENTS = (
    "mtm", "mtmAtPaxos", "costAdjustments", "transferredPnlAdjustments",
    "depositsWithdrawals", "carbonCredits", "donations", "internalCashTransfers",
    "paxosTransfers", "excessFundSweep", "assetTransfers", "debitCardActivity",
    "billPay", "grantActivity", "dividends", "withholdingTax", "withholding871m",
    "withholdingTaxCollected", "changeInDividendAccruals", "changeInLiteSurchargeAccruals",
    "changeInCGTWithholdingAccruals", "interest", "changeInInterestAccruals",
    "changeInIncentiveCouponAccruals", "brokerFees", "changeInBrokerFeeAccruals",
    "advisorFees", "clientFees", "otherFees", "otherIncome", "feesReceivables",
    "commissions", "commissionsAtPaxos", "referralFee", "commissionCreditsRedemption",
    "commissionReceivables", "forexCommissions", "transactionTax", "taxReceivables",
    "salesTax", "billableSalesTax", "softDollars", "netFxTrading", "fxTranslation",
    "linkingAdjustments", "other", "corporateActionProceeds",
)
PERFORMANCE_CASH_TYPES = {
    "Dividends", "Payment In Lieu Of Dividends", "Withholding Tax", "871(m) Withholding",
    "Broker Interest Paid", "Broker Interest Received", "Broker Fees", "Other Fees",
    "Advisor Fees", "Bond Interest Paid", "Bond Interest Received", "Commission Adjustments",
}
CASH_FLOW_FIELDS = (
    "netTradesPurchases", "netTradesSales", "commissions", "dividends", "paymentInLieu",
    "brokerInterest", "bondInterest", "withholdingTax", "brokerFees", "otherFees",
    "advisorFees", "clientFees", "depositWithdrawals", "internalTransfers", "other",
    "transactionTax", "salesTax", "referralFee", "otherIncome", "accountTransfers",
    "debitCardActivity", "billPay", "carbonCredits", "donations", "excessFundSweep",
    "paxosTransfers", "ipoSubscription", "insuredDepositInterest",
    "commissionCreditsRedemption", "feesReceivables", "taxReceivables",
    "linkingAdjustments", "withholding871m", "withholdingCollectedTax", "cfdCharges",
    "slbNetSecuritiesLentActivity", "realizedForexVm", "fxTranslationGainLoss",
)
CASH_BALANCE_FIELDS = ("startingCash", "endingCash", "endingSettledCash")
POSITION_NAV_COMPONENTS = {"STK": "stock", "WAR": "stock", "OPT": "options", "FUND": "funds", "BOND": "bonds"}
POSITION_VALUE_TOLERANCE = Decimal("0.01")
CASH_NAV_TOLERANCE = Decimal("0.001")


class FlexError(ValueError):
    """Safe reason codes only: never put source attribute values in errors."""


def _required(row: ET.Element | dict, key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise FlexError(f"missing_field:{key}")
    return value.strip()


def _number(row: ET.Element | dict, key: str) -> Decimal:
    try:
        value = Decimal(_required(row, key))
        if not value.is_finite() or abs(value) > Decimal("1e30"):
            raise InvalidOperation
        return value
    except InvalidOperation:
        raise FlexError(f"invalid_number:{key}") from None


def _date(value: str) -> date:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise FlexError("invalid_iso_date")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise FlexError("invalid_iso_date") from None


def _instant(value: str) -> datetime:
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2});(\d{2}:\d{2}:\d{2}) (EDT|EST|UTC|GMT)", value)
    if not match:
        raise FlexError("unsupported_timestamp_format")
    day, clock, label = match.groups()
    try:
        wall = datetime.fromisoformat(f"{day}T{clock}")
        offset = {"EDT": -4, "EST": -5, "UTC": 0, "GMT": 0}[label]
        instant = wall.replace(tzinfo=timezone(timedelta(hours=offset)))
        if label in {"EDT", "EST"}:
            local = instant.astimezone(ZoneInfo("America/New_York"))
            if local.replace(tzinfo=None) != wall or local.tzname() != label:
                raise FlexError("timezone_label_mismatch")
        return instant.astimezone(timezone.utc)
    except ValueError as exc:
        if isinstance(exc, FlexError):
            raise
        raise FlexError("invalid_timestamp") from None


def _currency(row: ET.Element | dict, key: str = "currency") -> str:
    value = _required(row, key)
    if not re.fullmatch(r"[A-Z]{3}", value):
        raise FlexError(f"invalid_currency:{key}")
    return value


def _upsert(rows: dict, key: object, value: dict, kind: str) -> bool:
    if key in rows:
        if rows[key] != value:
            raise FlexError(f"conflicting_duplicate:{kind}")
        return True
    rows[key] = value
    return False


@dataclass(repr=False)
class FlexImport:
    # Private in-memory records. Do not serialize this object into public APIs.
    account_id: str
    base_currency: str
    source_sha256: str
    days: dict = field(default_factory=dict)
    nav: dict = field(default_factory=dict)
    equity: dict = field(default_factory=dict)
    cash_report: dict = field(default_factory=dict)
    positions: dict = field(default_factory=dict)
    trades: dict = field(default_factory=dict)
    cash: dict = field(default_factory=dict)
    corporate_action_rows: dict = field(default_factory=dict)
    reconciliation: dict = field(default_factory=dict)
    issues: set = field(default_factory=set)
    duplicate_rows: int = 0
    corporate_actions: int = 0
    transfers: int = 0

    def report(self) -> dict:
        return {
            "schema_version": 2, "source_sha256": self.source_sha256,
            "scope": "local_statement_validation_only",
            "status": "needs_review" if self.issues else "validated_core_records",
            "account_designation": "not_verified_from_xml",
            "base_currency": self.base_currency,
            "period_start": min(self.days).isoformat(), "period_end": max(self.days).isoformat(),
            "daily_statements": len(self.days), "nav_observations": len(self.nav),
            "executions": len(self.trades), "cash_transactions": len(self.cash),
            "equivalent_duplicate_rows": self.duplicate_rows,
            "corporate_actions": self.corporate_actions, "transfers": self.transfers,
            "calendar_coverage_verified": False,
            "validation_scope": [
                "nav_bridge", "execution_records", "cash_transaction_records",
                "cash_balances", "position_quantities", "position_values", "corporate_actions",
            ],
            "balance_reconciliation": self.reconciliation,
            "cash_classifications": {
                kind: sum(row["classification"] == kind for row in self.cash.values())
                for kind in ("external_flow", "performance", "review")
            },
            "issues": sorted(self.issues), "verified_return_pct": None,
            "return_blockers": [
                "paper_account_and_opening_baseline_require_acceptance",
                "cash_and_position_discrepancies_require_review",
                "strategy_capital_and_transfer_baselines_missing",
            ],
        }


def read_flex(path: Path) -> FlexImport:
    # Bound allocation and forbid DTD/entity and alternate-encoding bypasses.
    with path.open("rb") as stream:
        data = stream.read(MAX_XML_BYTES + 1)
    if len(data) > MAX_XML_BYTES:
        raise FlexError("xml_too_large")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError:
        raise FlexError("xml_requires_utf8") from None
    if "\x00" in text or "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise FlexError("unsafe_xml_declaration")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise FlexError("malformed_xml") from None
    if root.tag != "FlexQueryResponse" or root.get("type") != "AF":
        raise FlexError("not_activity_flex_xml")
    wrappers = root.findall("FlexStatements")
    if len(wrappers) != 1:
        raise FlexError("invalid_statement_wrapper")
    statements = wrappers[0].findall("FlexStatement")
    if not statements or wrappers[0].get("count") != str(len(statements)):
        raise FlexError("statement_count_mismatch")
    account = _required(statements[0], "accountId")
    info = statements[0].find("AccountInformation")
    if info is None:
        raise FlexError("missing_account_information")
    result = FlexImport(account, _currency(info), sha256(data).hexdigest())
    for statement in statements:
        if _required(statement, "accountId") != account:
            raise FlexError("mixed_accounts")
        for element in statement.iter():
            if element.get("accountId") not in (None, account):
                raise FlexError("mixed_accounts")
        start = _date(_required(statement, "fromDate"))
        end = _date(_required(statement, "toDate"))
        if start != end or start in result.days:
            raise FlexError("requires_unique_daily_statements")
        _instant(_required(statement, "whenGenerated"))
        infos = statement.findall("AccountInformation")
        if (len(infos) != 1 or _required(infos[0], "accountId") != account
                or _currency(infos[0]) != result.base_currency):
            raise FlexError("mixed_or_missing_base_currency")
        for section in ("EquitySummaryInBase", "CashReport", "OpenPositions", "Trades", "CashTransactions", "Transfers", "CorporateActions"):
            if len(statement.findall(section)) != 1:
                raise FlexError(f"missing_or_duplicate_section:{section}")
        bridge_rows = statement.findall("ChangeInNAV")
        if len(bridge_rows) != 1:
            raise FlexError("missing_or_duplicate_nav_bridge")
        bridge = bridge_rows[0]
        if (_required(bridge, "accountId") != account or _currency(bridge) != result.base_currency or _date(_required(bridge, "fromDate")) != start
                or _date(_required(bridge, "toDate")) != end):
            raise FlexError("nav_bridge_scope_mismatch")
        bridge_values = {key: _number(bridge, key) for key in (*BRIDGE_COMPONENTS, "startingValue", "endingValue")}
        residual = bridge_values["endingValue"] - bridge_values["startingValue"] - sum(bridge_values[k] for k in BRIDGE_COMPONENTS)
        if abs(residual) > ROUNDING:
            result.issues.add("nav_bridge_does_not_balance")
        result.days[start] = bridge_values
        nav_rows = statement.findall("./EquitySummaryInBase/EquitySummaryByReportDateInBase")
        for section, expected in (("EquitySummaryInBase", "EquitySummaryByReportDateInBase"), ("Trades", "Trade"), ("CashTransactions", "CashTransaction")):
            if any(child.tag != expected for child in statement.find(section)):
                raise FlexError(f"unexpected_record_type:{section}")
        closing = False
        for row in nav_rows:
            day = _date(_required(row, "reportDate"))
            if _required(row, "accountId") != account or _currency(row) != result.base_currency or day > end:
                raise FlexError("nav_scope_mismatch")
            equity = {
                "total": _number(row, "total"),
                **{key: _number(row, key) for key in ("cash", "stock", "options", "funds", "bonds")
                   if row.get(key) not in (None, "")},
            }
            result.duplicate_rows += _upsert(result.equity, day, equity, "equity_summary")
            result.duplicate_rows += _upsert(result.nav, day, {"total": equity["total"]}, "nav")
            if day == end:
                closing = True
                if abs(equity["total"] - bridge_values["endingValue"]) > ROUNDING:
                    result.issues.add("closing_nav_mismatch")
        if not closing:
            raise FlexError("missing_closing_nav")
        cash_rows = statement.findall("./CashReport/CashReportCurrency")
        for row in cash_rows:
            if _required(row, "accountId") != account:
                raise FlexError("mixed_accounts")
            if _date(_required(row, "fromDate")) != start or _date(_required(row, "toDate")) != end:
                raise FlexError("cash_report_date_mismatch")
            level = _required(row, "levelOfDetail")
            if level == "BaseCurrency":
                if row.get("currency") != "BASE_SUMMARY":
                    raise FlexError("cash_report_base_currency_mismatch")
                currency = result.base_currency
            elif level == "Currency":
                currency = _currency(row)
            else:
                raise FlexError("unsupported_cash_report_detail")
            balances = {key: _number(row, key) for key in CASH_BALANCE_FIELDS if row.get(key) not in (None, "")}
            balances.update({key: _number(row, key) for key in CASH_FLOW_FIELDS if row.get(key) not in (None, "")})
            balances["level"] = level
            result.duplicate_rows += _upsert(
                result.cash_report, (start, level, currency), balances, "cash_report"
            )
        position_rows = statement.findall("./OpenPositions/OpenPosition")
        for row in position_rows:
            if _required(row, "accountId") != account:
                raise FlexError("mixed_accounts")
            if _date(_required(row, "reportDate")) != end or row.get("levelOfDetail") != "SUMMARY":
                raise FlexError("position_detail_or_report_date_mismatch")
            category = _required(row, "assetCategory")
            if category not in POSITION_NAV_COMPONENTS:
                result.issues.add("unsupported_position_category")
            quantity = _number(row, "position")
            multiplier = _number(row, "multiplier")
            mark = _number(row, "markPrice")
            fx = _number(row, "fxRateToBase")
            value = _number(row, "positionValue")
            if quantity == 0 or multiplier <= 0 or fx <= 0 or mark < 0:
                raise FlexError("invalid_position_values")
            if (quantity > 0) != (value >= 0):
                result.issues.add("position_value_sign_mismatch")
            if abs(value - quantity * mark * multiplier) > max(ROUNDING, abs(value) * Decimal("0.001")):
                result.issues.add("position_mark_value_mismatch")
            position = {
                "quantity": quantity, "mark": mark, "multiplier": multiplier,
                "fx_to_base": fx, "value": value, "category": category,
                "currency": _currency(row), "side": _required(row, "side"),
                "sub_category": row.get("subCategory", "").strip(),
            }
            result.duplicate_rows += _upsert(
                result.positions, (start, _required(row, "conid"), position["currency"]),
                position, "position",
            )
        for row in statement.findall("./CorporateActions/CorporateAction"):
            if _required(row, "accountId") != account:
                raise FlexError("mixed_accounts")
            if _date(_required(row, "reportDate")) != end or row.get("levelOfDetail") != "DETAIL":
                raise FlexError("corporate_action_detail_or_report_date_mismatch")
            action = {
                "date": end, "effective_at": _instant(_required(row, "dateTime")),
                "type": _required(row, "type"), "conid": _required(row, "conid"),
                "currency": _currency(row), "quantity": _number(row, "quantity"),
                "amount": _number(row, "amount"), "proceeds": _number(row, "proceeds"),
                "value": _number(row, "value"), "fx_to_base": _number(row, "fxRateToBase"),
                "multiplier": _number(row, "multiplier"), "action_id": _required(row, "actionID"),
            }
            if action["fx_to_base"] <= 0 or action["multiplier"] <= 0:
                raise FlexError("invalid_corporate_action_values")
            result.duplicate_rows += _upsert(
                result.corporate_action_rows, action["action_id"], action, "corporate_action"
            )
        for row in statement.findall("./Trades/Trade"):
            _required(row, "accountId")
            if row.get("levelOfDetail") != "EXECUTION":
                raise FlexError("requires_execution_detail")
            executed = _instant(_required(row, "dateTime"))
            report_day, trade_day = _date(_required(row, "reportDate")), _date(_required(row, "tradeDate"))
            if report_day != end or trade_day > report_day:
                raise FlexError("trade_date_outside_statement")
            side, quantity, price = _required(row, "buySell"), _number(row, "quantity"), _number(row, "tradePrice")
            if side not in {"BUY", "SELL"} or quantity == 0 or price <= 0:
                raise FlexError("invalid_trade_values")
            if (side == "BUY") != (quantity > 0):
                raise FlexError("trade_quantity_sign_mismatch")
            if (row.get("transactionType") != "ExchTrade" or any(row.get(k, "") not in {"", "0"} for k in ("origTradeID", "origTransactionID", "origOrderID"))
                    or {"Ca", "Co"} & set(row.get("notes", "").split(";"))):
                result.issues.add("trade_corrections_require_review")
            if row.get("assetCategory") != "STK" or _number(row, "multiplier") != 1:
                result.issues.add("unsupported_trade_instrument")
            execution = {
                "executed_at": executed, "executed_at_raw": _required(row, "dateTime"),
                "trade_date": trade_day, "report_date": report_day,
                "symbol": _required(row, "symbol"), "conid": _required(row, "conid"),
                "currency": _currency(row), "side": side.lower(), "quantity": abs(quantity), "price": price,
                "multiplier": _number(row, "multiplier"),
                "commission": _number(row, "ibCommission"), "commission_currency": _currency(row, "ibCommissionCurrency"),
                "taxes": _number(row, "taxes"), "fx_to_base": _number(row, "fxRateToBase"),
                "order_ref": row.get("orderReference", "").strip(),
                "transaction_id": _required(row, "transactionID"),
                "transaction_type": row.get("transactionType"), "notes": row.get("notes", ""),
                "original_trade_id": row.get("origTradeID", ""),
                "original_transaction_id": row.get("origTransactionID", ""),
                "original_order_id": row.get("origOrderID", ""),
            }
            if execution["fx_to_base"] <= 0:
                raise FlexError("invalid_trade_fx_rate")
            if execution["commission_currency"] != execution["currency"]:
                result.issues.add("commission_fx_requires_reconciliation")
            result.duplicate_rows += _upsert(result.trades, _required(row, "ibExecID"), execution, "execution")
        for row in statement.findall("./CashTransactions/CashTransaction"):
            _required(row, "accountId")
            if row.get("levelOfDetail") != "DETAIL" or _date(_required(row, "reportDate")) != end:
                raise FlexError("cash_detail_or_report_date_mismatch")
            effective = _required(row, "dateTime")
            instant = _instant(effective) if ";" in effective else None
            effective_date = _date(effective.split(";", 1)[0])
            kind = _required(row, "type")
            classification = "external_flow" if kind == "Deposits & Withdrawals" else "performance" if kind in PERFORMANCE_CASH_TYPES else "review"
            if classification == "review":
                result.issues.add("unclassified_cash_activity")
            cash = {
                "effective_date": effective_date, "effective_at": instant, "report_date": end,
                "type": kind, "classification": classification, "currency": _currency(row),
                "amount": _number(row, "amount"), "fx_to_base": _number(row, "fxRateToBase"),
                "symbol": row.get("symbol", "").strip(), "conid": row.get("conid", "").strip(),
                "trade_id": row.get("tradeID", "").strip(), "action_id": row.get("actionID", "").strip(),
                "code": row.get("code", "").strip(),
            }
            if cash["fx_to_base"] <= 0:
                raise FlexError("invalid_cash_fx_rate")
            # Date-only cash rows deliberately keep effective_at=None, not midnight UTC.
            result.duplicate_rows += _upsert(result.cash, _required(row, "transactionID"), cash, "cash_transaction")
        result.transfers += len(statement.findall("./Transfers/Transfer"))
    result.corporate_actions = len(result.corporate_action_rows)
    previous = None
    for day, bridge in sorted(result.days.items()):
        if previous is not None and abs(bridge["startingValue"] - previous["endingValue"]) > ROUNDING:
            result.issues.add("nav_chain_break")
        previous = bridge
        funding = sum(row["amount"] * row["fx_to_base"] for row in result.cash.values()
                      if row["report_date"] == day and row["classification"] == "external_flow")
        if abs(funding - bridge["depositsWithdrawals"]) > ROUNDING:
            result.issues.add("funding_transactions_do_not_match_nav_bridge")
        if any(bridge[k] for k in ("assetTransfers", "internalCashTransfers", "paxosTransfers", "linkingAdjustments")):
            result.issues.add("transfer_components_require_classification")
    result.reconciliation = reconcile_balances(result)
    if result.transfers:
        result.issues.add("transfers_require_classification")
    return result


def _within(actual: Decimal, expected: Decimal, relative: Decimal = Decimal("0")) -> bool:
    return abs(actual - expected) <= max(ROUNDING, abs(expected) * relative)


def reconcile_balances(imported: FlexImport) -> dict:
    """Reconcile cash balances, holdings and corporate actions without publishing amounts."""
    if not imported.cash_report or not imported.positions:
        return {"status": "unavailable", "reason": "cash_report_or_open_positions_missing"}

    cash_base = {
        day: row for (day, level, _currency_code), row in imported.cash_report.items()
        if level == "BaseCurrency"
    }
    cash_native = {
        (day, currency): row
        for (day, level, currency), row in imported.cash_report.items()
        if level == "Currency"
    }
    statement_days = sorted(imported.days)
    statement_gaps = [
        later.isoformat() for earlier, later in zip(statement_days, statement_days[1:])
        if (later - earlier).days > 4
    ]

    def continuity(series):
        breaks = []
        missing = []
        previous = None
        for day, row in series:
            start = row.get("startingCash")
            end = row.get("endingCash")
            if start is None or end is None:
                missing.append(day.isoformat())
                continue
            if previous is not None and not _within(start, previous):
                breaks.append(day.isoformat())
            previous = end
        return breaks, missing

    base_breaks, base_missing = continuity(sorted(cash_base.items()))
    if base_breaks or base_missing:
        imported.issues.add("cash_base_continuity_break")
    native_breaks: dict[str, int] = {}
    native_missing: list[str] = []
    for currency in sorted({currency for _, currency in cash_native}):
        series = sorted((day, row) for (day, row_currency), row in cash_native.items()
                        if row_currency == currency)
        breaks, missing = continuity(series)
        native_breaks[currency] = len(breaks)
        native_missing.extend(f"{day}:{currency}" for day in missing)
    if any(native_breaks.values()) or native_missing:
        imported.issues.add("cash_native_continuity_break")
    if statement_gaps:
        imported.issues.add("statement_calendar_gap")

    nav_cash_checks = nav_cash_exact = nav_cash_close = 0
    nav_cash_mismatch: list[str] = []
    for day, row in sorted(cash_base.items()):
        ending = row.get("endingCash")
        nav_cash = imported.equity.get(day, {}).get("cash")
        if ending is None or nav_cash is None:
            continue
        nav_cash_checks += 1
        if abs(ending - nav_cash) <= ROUNDING:
            nav_cash_exact += 1
        elif _within(ending, nav_cash, CASH_NAV_TOLERANCE):
            nav_cash_close += 1
        else:
            nav_cash_mismatch.append(day.isoformat())
    if nav_cash_mismatch:
        imported.issues.add("cash_balance_nav_mismatch")

    trade_proceeds: dict[tuple, Decimal] = {}
    trade_commissions: dict[tuple, Decimal] = {}
    for trade in imported.trades.values():
        key = (trade["report_date"], trade["currency"])
        sign = Decimal(-1) if trade["side"] == "buy" else Decimal(1)
        trade_proceeds[key] = trade_proceeds.get(key, Decimal(0)) + (
            sign * trade["quantity"] * trade["price"] * trade["multiplier"]
        )
        if trade["commission_currency"] == trade["currency"]:
            trade_commissions[key] = trade_commissions.get(key, Decimal(0)) + trade["commission"]

    def compare_components(field: str, actuals: dict[tuple, Decimal], label: str) -> list[str]:
        mismatches = []
        for key, row in sorted(cash_native.items()):
            expected = row.get(field)
            actual = actuals.get(key, Decimal(0))
            if expected is None or (expected == 0 and actual == 0):
                continue
            if not _within(actual, expected, CASH_NAV_TOLERANCE):
                mismatches.append(f"{key[0].isoformat()}:{key[1]}")
        if mismatches:
            imported.issues.add(label)
        return mismatches

    # IBKR reports purchase proceeds as negative and sale proceeds as positive.
    trade_component_mismatches = []
    trade_component_checks = 0
    for key, row in sorted(cash_native.items()):
        expected = row.get("netTradesPurchases", Decimal(0)) + row.get("netTradesSales", Decimal(0))
        actual = trade_proceeds.get(key, Decimal(0))
        if expected == 0 and actual == 0:
            continue
        trade_component_checks += 1
        if not _within(actual, expected, CASH_NAV_TOLERANCE):
            trade_component_mismatches.append(f"{key[0].isoformat()}:{key[1]}")
    if trade_component_mismatches:
        imported.issues.add("cash_trade_component_mismatch")

    commission_mismatches = compare_components(
        "commissions", trade_commissions, "cash_commission_component_mismatch"
    )
    income_actuals: dict[tuple, Decimal] = {}
    for cash_row in imported.cash.values():
        field = {
            "Dividends": "dividends",
            "Payment In Lieu Of Dividends": "paymentInLieu",
            "Broker Interest Paid": "brokerInterest",
            "Broker Interest Received": "brokerInterest",
            "Bond Interest Paid": "bondInterest",
            "Bond Interest Received": "bondInterest",
        }.get(cash_row["type"])
        if field:
            key = (cash_row["report_date"], cash_row["currency"])
            income_actuals[key] = income_actuals.get(key, Decimal(0)) + cash_row["amount"]
    income_mismatches = []
    for key, row in sorted(cash_native.items()):
        expected = sum((row.get(field, Decimal(0)) for field in
                        ("dividends", "paymentInLieu", "brokerInterest", "bondInterest")), Decimal(0))
        actual = income_actuals.get(key, Decimal(0))
        if expected == 0 and actual == 0:
            continue
        if not _within(actual, expected, CASH_NAV_TOLERANCE):
            income_mismatches.append(f"{key[0].isoformat()}:{key[1]}")
    if income_mismatches:
        imported.issues.add("cash_income_component_mismatch")

    funding_actuals: dict[tuple, Decimal] = {}
    for cash_row in imported.cash.values():
        if cash_row["classification"] == "external_flow":
            key = (cash_row["report_date"], cash_row["currency"])
            funding_actuals[key] = funding_actuals.get(key, Decimal(0)) + cash_row["amount"]
    funding_mismatches = compare_components(
        "depositWithdrawals", funding_actuals, "cash_funding_component_mismatch"
    )

    movement_residuals = []
    for key, row in sorted(cash_native.items()):
        start = row.get("startingCash")
        end = row.get("endingCash")
        if start is None or end is None:
            continue
        known = sum((row.get(field, Decimal(0)) for field in CASH_FLOW_FIELDS), Decimal(0))
        if not _within(end - start, known, CASH_NAV_TOLERANCE):
            movement_residuals.append(f"{key[0].isoformat()}:{key[1]}")
    if movement_residuals:
        imported.issues.add("cash_movement_components_require_review")

    position_days = sorted({day for (day, _, _) in imported.positions})
    closing: dict[date, dict[tuple, Decimal]] = {day: {} for day in position_days}
    mark_value_mismatches = 0
    for (day, conid, currency), position in imported.positions.items():
        closing[day][(conid, currency)] = position["quantity"]
        if not _within(
            position["value"], position["quantity"] * position["mark"] * position["multiplier"],
            Decimal("0.001"),
        ):
            mark_value_mismatches += 1
    if mark_value_mismatches:
        imported.issues.add("position_mark_value_mismatch")

    trade_deltas: dict[date, dict[tuple, Decimal]] = {}
    for trade in imported.trades.values():
        day = trade["report_date"]
        key = (trade["conid"], trade["currency"])
        sign = Decimal(1) if trade["side"] == "buy" else Decimal(-1)
        day_trades = trade_deltas.setdefault(day, {})
        day_trades[key] = day_trades.get(key, Decimal(0)) + sign * trade["quantity"]
    ca_deltas: dict[date, dict[tuple, Decimal]] = {}
    for action in imported.corporate_action_rows.values():
        key = (action["conid"], action["currency"])
        day_actions = ca_deltas.setdefault(action["date"], {})
        day_actions[key] = day_actions.get(key, Decimal(0)) + action["quantity"]

    quantity_mismatch_days = []
    quantity_mismatch_keys = 0
    empty_position_days = [day.isoformat() for day in position_days if not closing[day]]
    if empty_position_days:
        imported.issues.add("position_snapshot_missing")
    for previous_day, day in zip(position_days, position_days[1:]):
        expected = dict(closing[previous_day])
        for key, quantity in trade_deltas.get(day, {}).items():
            expected[key] = expected.get(key, Decimal(0)) + quantity
        for key, quantity in ca_deltas.get(day, {}).items():
            expected[key] = expected.get(key, Decimal(0)) + quantity
        keys = set(expected) | set(closing[day])
        differences = sum(
            expected.get(key, Decimal(0)) != closing[day].get(key, Decimal(0)) for key in keys
        )
        if differences:
            quantity_mismatch_days.append(day.isoformat())
            quantity_mismatch_keys += differences
    if quantity_mismatch_days:
        imported.issues.add("position_quantity_mismatch")

    ca_quantity_mismatches = 0
    ca_quantity_reconciled = 0
    # Reconcile each corporate action against the observed change after same-day trades.
    grouped_actions: dict[tuple, Decimal] = {}
    for action in imported.corporate_action_rows.values():
        key = (action["date"], action["conid"], action["currency"])
        grouped_actions[key] = grouped_actions.get(key, Decimal(0)) + action["quantity"]
    for (day, conid, currency), expected_quantity in sorted(grouped_actions.items()):
        if day not in closing:
            ca_quantity_mismatches += 1
            continue
        index = position_days.index(day)
        previous = closing[position_days[index - 1]].get((conid, currency), Decimal(0)) if index else Decimal(0)
        observed = (
            closing[day].get((conid, currency), Decimal(0))
            - previous - trade_deltas.get(day, {}).get((conid, currency), Decimal(0))
        )
        if observed == expected_quantity:
            ca_quantity_reconciled += 1
        else:
            ca_quantity_mismatches += 1
    if ca_quantity_mismatches:
        imported.issues.add("corporate_action_quantity_mismatch")

    ca_cash_reconciled = 0
    ca_cash_unmatched = 0
    for action in imported.corporate_action_rows.values():
        effect = action["amount"] + action["proceeds"]
        if effect == 0:
            continue
        matched = any(
            cash_row["report_date"] == action["date"]
            and cash_row["currency"] == action["currency"]
            and (cash_row["action_id"] == action["action_id"] or cash_row["conid"] == action["conid"])
            for cash_row in imported.cash.values()
        )
        if matched:
            ca_cash_reconciled += 1
        else:
            ca_cash_unmatched += 1
    if ca_cash_unmatched:
        imported.issues.add("corporate_action_cash_effect_requires_review")

    values_by_day_component: dict[tuple, Decimal] = {}
    for (day, _, _), position in imported.positions.items():
        component = POSITION_NAV_COMPONENTS.get(position["category"])
        if component is None:
            continue
        key = (day, component)
        values_by_day_component[key] = (
            values_by_day_component.get(key, Decimal(0)) + position["value"] * position["fx_to_base"]
        )
    nav_component_checks = nav_component_matched_days = 0
    nav_component_mismatch_days = []
    for day in position_days:
        equity = imported.equity.get(day, {})
        checks = matches = 0
        for component in set(POSITION_NAV_COMPONENTS.values()):
            expected = equity.get(component)
            if expected is None:
                continue
            checks += 1
            actual = values_by_day_component.get((day, component), Decimal(0))
            if _within(actual, expected, POSITION_VALUE_TOLERANCE):
                matches += 1
        if checks:
            nav_component_checks += checks
            if checks == matches:
                nav_component_matched_days += 1
            else:
                nav_component_mismatch_days.append(day.isoformat())
    if nav_component_mismatch_days:
        imported.issues.add("position_value_nav_mismatch")

    return {
        "status": "available",
        "tolerances": {
            "money": str(ROUNDING),
            "nav_relative": str(CASH_NAV_TOLERANCE),
            "position_value_relative": str(POSITION_VALUE_TOLERANCE),
        },
        "cash": {
            "base_days": len(cash_base), "native_currency_rows": len(cash_native),
            "statement_gaps": statement_gaps,
            "base_continuity_breaks": len(base_breaks),
            "base_missing_balances": base_missing,
            "native_continuity_breaks": native_breaks,
            "native_missing_balances": native_missing,
            "nav_cash_checks": nav_cash_checks, "nav_cash_exact": nav_cash_exact,
            "nav_cash_within_tolerance": nav_cash_close,
            "nav_cash_mismatch_days": nav_cash_mismatch,
            "trade_component_checks": trade_component_checks,
            "trade_component_mismatch_days": trade_component_mismatches,
            "commission_component_mismatch_days": commission_mismatches,
            "income_component_mismatch_days": income_mismatches,
            "funding_component_mismatch_days": funding_mismatches,
            "movement_residual_days": movement_residuals,
        },
        "positions": {
            "baseline_day": position_days[0].isoformat() if position_days else None,
            "position_days": len(position_days),
            "transitions": max(0, len(position_days) - 1),
            "quantity_mismatch_days": quantity_mismatch_days,
            "quantity_mismatch_keys": quantity_mismatch_keys,
            "empty_position_days": empty_position_days,
            "mark_value_mismatch_rows": mark_value_mismatches,
            "nav_component_checks": nav_component_checks,
            "nav_component_matched_days": nav_component_matched_days,
            "nav_component_mismatch_days": nav_component_mismatch_days,
        },
        "corporate_actions": {
            "rows": len(imported.corporate_action_rows),
            "quantity_reconciled": ca_quantity_reconciled,
            "quantity_mismatches": ca_quantity_mismatches,
            "cash_effect_reconciled": ca_cash_reconciled,
            "cash_effect_unmatched": ca_cash_unmatched,
        },
    }


def read_observed_ledger(path: Path) -> tuple[dict, int]:
    """Read the legacy observed fill CSV without modifying it.

    Validation matches the reconciliation path: explicit timestamps, unique
    non-empty headers, positive quantities/prices and execution-ID conflicts stop
    the read. The returned records stay private and in memory.
    """
    seen: dict = {}
    duplicate_rows = 0
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        fields = reader.fieldnames or []
        required = {"execution_id", "timestamp", "symbol", "currency", "side", "quantity", "price", "order_ref"}
        if not required <= set(fields) or len(set(fields)) != len(fields):
            raise FlexError("invalid_ledger_header")
        for row in reader:
            if None in row or any(row.get(k) is None for k in fields):
                raise FlexError("malformed_ledger_row")
            try:
                timestamp = _timestamp(_required(row, "timestamp"))
            except ValueError:
                raise FlexError("invalid_ledger_timestamp") from None
            side = {"bot": "buy", "buy": "buy", "sld": "sell", "sell": "sell"}.get(_required(row, "side").lower())
            value = {"executed_at": timestamp, "timestamp_raw": _required(row, "timestamp"),
                     "symbol": _required(row, "symbol"), "currency": _currency(row),
                     "side": side, "quantity": _number(row, "quantity"), "price": _number(row, "price"),
                     "order_ref": _required(row, "order_ref"), "strategy": row.get("strategy", "").strip()}
            if side is None or value["quantity"] <= 0 or value["price"] <= 0:
                raise FlexError("invalid_ledger_fill")
            duplicate_rows += _upsert(seen, _required(row, "execution_id"), value, "ledger_execution")
    return seen, duplicate_rows


def reconcile_ledger(imported: FlexImport, path: Path) -> dict:
    """Compare original local fills by execution ID; never overwrite either source."""
    seen, duplicate_rows = read_observed_ledger(path)
    matched = set(seen) & imported.trades.keys()
    mismatch_counts = {k: 0 for k in ("symbol", "currency", "side", "quantity", "price", "order_ref", "executed_at")}
    timestamp_offsets: dict[str, int] = {}
    for key in matched:
        for field_name in mismatch_counts:
            mismatch_counts[field_name] += seen[key][field_name] != imported.trades[key][field_name]
        offset = (seen[key]["executed_at"] - imported.trades[key]["executed_at"]).total_seconds()
        if offset:
            label = str(int(offset)) if offset.is_integer() else str(offset)
            timestamp_offsets[label] = timestamp_offsets.get(label, 0) + 1
    absent_in_period = outside_period = 0
    for key in set(seen) - imported.trades.keys():
        # The XML export uses US Eastern trade days; make boundary treatment explicit.
        local_day = seen[key]["executed_at"].astimezone(ZoneInfo("America/New_York")).date()
        if min(imported.days) <= local_day <= max(imported.days):
            absent_in_period += 1
        else:
            outside_period += 1
    return {
        "status": "needs_review" if absent_in_period or any(mismatch_counts.values()) else "matched_observed_subset",
        "local_unique_executions": len(seen), "equivalent_duplicate_rows": duplicate_rows,
        "matched_execution_ids": len(matched), "field_mismatches": mismatch_counts,
        "local_minus_statement_timestamp_seconds": dict(sorted(timestamp_offsets.items())),
        "local_missing_from_statement_period": absent_in_period, "local_outside_statement_period": outside_period,
        "statement_only_executions": len(imported.trades.keys() - seen.keys()),
        "scope": "observed_nova_ledger_subset_not_full_account_reconciliation",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xml", type=Path)
    parser.add_argument("--ledger", type=Path, help="Optional local Nova CSV to compare read-only")
    args = parser.parse_args()
    try:
        imported = read_flex(args.xml)
        report = imported.report()
        if args.ledger:
            report["ledger_reconciliation"] = reconcile_ledger(imported, args.ledger)
            if report["ledger_reconciliation"]["status"] == "needs_review":
                report["status"] = "needs_review"
        print(json.dumps(report, indent=2, allow_nan=False))
    except (FlexError, OSError, UnicodeError, csv.Error) as exc:
        reason = str(exc) if isinstance(exc, FlexError) else "unreadable_input"
        print(json.dumps({"status": "invalid", "reason": reason, "verified_return_pct": None}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
