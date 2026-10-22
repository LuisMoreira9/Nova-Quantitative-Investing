"""Offline audit of observed CSVs, not a broker reconciliation or return calculator.

Uses only the standard library and never imports the broker/publisher adapters.
Output contains counts and coverage dates, never raw fills or account balances.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path


def _timestamp(text: str) -> datetime:
    value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp requires an explicit timezone")
    return value.astimezone(timezone.utc)


def _positive(text: str) -> Decimal:
    value = Decimal(text)
    if not value.is_finite() or value <= 0:
        raise ValueError("Expected a finite positive value")
    return value


def _audit_file(path: Path, *, fills: bool, now: datetime) -> dict:
    required = (
        {"execution_id", "timestamp", "strategy", "order_ref", "symbol", "currency", "side", "quantity", "price"}
        if fills else {"timestamp", "equity"}
    )
    report = {
        "status": "unavailable", "rows": 0, "valid_unique_rows": 0,
        "invalid_rows": 0, "duplicate_rows": 0, "conflicting_keys": 0,
        "first_observed_at": None, "last_observed_at": None,
        "missing_columns": [], "read_error": None,
    }
    seen: dict[object, tuple] = {}
    conflicts: set = set()
    observed: set[datetime] = set()
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            fields = reader.fieldnames or []
            report["missing_columns"] = sorted(required - set(fields))
            if report["missing_columns"]:
                report["read_error"] = "missing_columns"
                return report
            if len(fields) != len(set(fields)):
                report["read_error"] = "duplicate_columns"
                return report
            for row in reader:
                report["rows"] += 1
                try:
                    if None in row or any(row.get(field) is None for field in fields):
                        raise ValueError("Malformed CSV row")
                    timestamp = _timestamp(row["timestamp"].strip())
                    if timestamp > now:
                        raise ValueError("Future timestamp")
                    if fills:
                        execution_id = row["execution_id"].strip()
                        strategy, order_ref = row["strategy"].strip(), row["order_ref"].strip()
                        symbol, currency = row["symbol"].strip(), row["currency"].strip()
                        side = {"bot": "buy", "sld": "sell", "buy": "buy", "sell": "sell"}.get(row["side"].strip().lower())
                        if (not execution_id or not strategy or not symbol or not order_ref.startswith("nova-")
                                or len(currency) != 3 or not currency.isascii() or not currency.isalpha()
                                or currency != currency.upper() or side is None):
                            raise ValueError("Invalid fill identity or attribution")
                        key = execution_id
                        values = (timestamp, strategy, order_ref, symbol, currency, side,
                                  _positive(row["quantity"]), _positive(row["price"]), row.get("exchange", "").strip())
                    else:
                        key, values = timestamp, (_positive(row["equity"]),)
                    if key in seen:
                        report["duplicate_rows"] += 1
                        if seen[key] != values:
                            conflicts.add(key)
                    else:
                        seen[key] = values
                    observed.add(timestamp)
                except (ValueError, InvalidOperation, OverflowError):
                    report["invalid_rows"] += 1
    except FileNotFoundError:
        report["read_error"] = "missing_file"
    except (OSError, UnicodeError, csv.Error):
        report["read_error"] = "unreadable_csv"
    report["conflicting_keys"] = len(conflicts)
    report["valid_unique_rows"] = len(seen) - len(conflicts)
    if observed:
        report["first_observed_at"] = min(observed).isoformat()
        report["last_observed_at"] = max(observed).isoformat()
        report["status"] = (
            "partial" if report["invalid_rows"] or conflicts or report["read_error"] else "observed"
        )
    return report


def audit_performance_inputs(data_dir: Path, *, now: datetime | None = None) -> dict:
    """Inspect the current legacy input contract; clean rows cannot prove completeness.

    Additional columns/files never promote this audit to ready. A future importer
    must verify broker statement coverage and reconciliation explicitly.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Audit time requires an explicit timezone")
    observations = _audit_file(data_dir / "ibkr_account_history.csv", fills=False, now=now)
    fills = _audit_file(data_dir / "nova_strategy_execution_ledger.csv", fills=True, now=now)
    blockers = [
        "account_currency_and_identity_not_recorded_in_equity_history",
        "funding_history_not_collected_or_reconciled",
        "commission_and_fee_history_not_collected_or_reconciled",
        "broker_statement_coverage_not_reconciled",
        "opening_positions_and_cash_not_reconciled",
        "strategy_capital_and_transfer_baselines_missing",
        "strategy_marks_lack_individual_source_times",
    ]
    for name, report in (("equity_observations", observations), ("nova_fills", fills)):
        if report["status"] != "observed":
            blockers.append(f"{name}_missing_or_invalid")
    return {
        "schema_version": 1, "audit_at": now.astimezone(timezone.utc).isoformat(),
        "scope": "local_observed_csvs_only", "status": "blocked",
        "verified_return_pct": None,
        "equity_observations": observations, "nova_fills": fills,
        "return_blockers": blockers,
        "limitations": [
            "Observed dates are not proof of continuous collection or complete broker history.",
            "No funding, fees or strategy capital are inferred from changes in equity or gross marks.",
            "This audit does not inspect new accounting formats, reconcile statements or calculate returns.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path, help="Local reporting CSV directory (read only)")
    args = parser.parse_args()
    print(json.dumps(audit_performance_inputs(args.data_dir), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
