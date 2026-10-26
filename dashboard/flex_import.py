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
    trades: dict = field(default_factory=dict)
    cash: dict = field(default_factory=dict)
    issues: set = field(default_factory=set)
    duplicate_rows: int = 0
    corporate_actions: int = 0
    transfers: int = 0

    def report(self) -> dict:
        return {
            "schema_version": 1, "source_sha256": self.source_sha256,
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
            "validation_scope": ["nav_bridge", "execution_records", "cash_transaction_records"],
            "cash_classifications": {
                kind: sum(row["classification"] == kind for row in self.cash.values())
                for kind in ("external_flow", "performance", "review")
            },
            "issues": sorted(self.issues), "verified_return_pct": None,
            "return_blockers": [
                "paper_account_and_opening_baseline_require_acceptance",
                "full_position_cash_and_corporate_action_reconciliation_pending",
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
            result.duplicate_rows += _upsert(result.nav, day, {"total": _number(row, "total")}, "nav")
            if day == end:
                closing = True
                if abs(result.nav[day]["total"] - bridge_values["endingValue"]) > ROUNDING:
                    result.issues.add("closing_nav_mismatch")
        if not closing:
            raise FlexError("missing_closing_nav")
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
                "executed_at": executed, "trade_date": trade_day, "report_date": report_day,
                "symbol": _required(row, "symbol"), "conid": _required(row, "conid"),
                "currency": _currency(row), "side": side.lower(), "quantity": abs(quantity), "price": price,
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
        result.corporate_actions += len(statement.findall("./CorporateActions/CorporateAction"))
        result.transfers += len(statement.findall("./Transfers/Transfer"))
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
    if result.corporate_actions:
        result.issues.add("corporate_actions_require_reconciliation")
    if result.transfers:
        result.issues.add("transfers_require_classification")
    return result


def reconcile_ledger(imported: FlexImport, path: Path) -> dict:
    """Compare original local fills by execution ID; never overwrite either source."""
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
            value = {"executed_at": timestamp, "symbol": _required(row, "symbol"), "currency": _currency(row),
                     "side": side, "quantity": _number(row, "quantity"), "price": _number(row, "price"),
                     "order_ref": _required(row, "order_ref")}
            if side is None or value["quantity"] <= 0 or value["price"] <= 0:
                raise FlexError("invalid_ledger_fill")
            duplicate_rows += _upsert(seen, _required(row, "execution_id"), value, "ledger_execution")
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
