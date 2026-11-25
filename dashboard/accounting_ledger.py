"""Stage broker Flex executions and timestamp provenance in a private ledger.

The Activity Flex XML and the legacy observed CSV are read-only inputs. The
SQLite output contains private fills, so keep it outside Git; stdout reports only
counts, dates, digests and reason codes. This module does not calculate returns,
publish anything or accept an opening/strategy-capital baseline.
"""

from __future__ import annotations

import argparse
from datetime import timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from zoneinfo import ZoneInfo

from dashboard.flex_import import FlexError, read_flex, read_observed_ledger


SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE executions (
    execution_id TEXT PRIMARY KEY,
    record_origin TEXT NOT NULL,
    symbol TEXT,
    conid TEXT,
    currency TEXT,
    side TEXT,
    quantity TEXT,
    price TEXT,
    multiplier TEXT,
    commission TEXT,
    commission_currency TEXT,
    taxes TEXT,
    fx_to_base TEXT,
    order_ref TEXT,
    transaction_id TEXT,
    trade_date TEXT,
    report_date TEXT,
    statement_time_raw TEXT,
    statement_time_utc TEXT,
    legacy_time_raw TEXT,
    legacy_time_utc TEXT,
    legacy_strategy TEXT,
    legacy_symbol TEXT,
    legacy_currency TEXT,
    legacy_side TEXT,
    legacy_quantity TEXT,
    legacy_price TEXT,
    legacy_order_ref TEXT,
    time_delta_seconds INTEGER,
    field_mismatches TEXT NOT NULL DEFAULT '',
    review_reason TEXT NOT NULL
);
CREATE INDEX executions_review_reason ON executions(review_reason);
"""

COLUMNS = (
    "execution_id", "record_origin", "symbol", "conid", "currency", "side",
    "quantity", "price", "multiplier", "commission", "commission_currency",
    "taxes", "fx_to_base", "order_ref", "transaction_id", "trade_date",
    "report_date", "statement_time_raw", "statement_time_utc",
    "legacy_time_raw", "legacy_time_utc", "legacy_strategy", "legacy_symbol",
    "legacy_currency", "legacy_side", "legacy_quantity", "legacy_price",
    "legacy_order_ref", "time_delta_seconds", "field_mismatches", "review_reason",
)
COMPARED_FIELDS = ("symbol", "currency", "side", "quantity", "price", "order_ref")


def _plain(value: Decimal) -> str:
    return format(value, "f")


def _utc(value) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _delta_seconds(statement, legacy) -> int:
    seconds = (legacy - statement).total_seconds()
    if not seconds.is_integer():
        raise FlexError("non_integral_timestamp_delta")
    return int(seconds)


def _row_for_statement(imported, execution_id: str, legacy: dict | None) -> dict:
    trade = imported.trades[execution_id]
    row = {
        "execution_id": execution_id,
        "record_origin": "statement_and_ledger" if legacy else "statement",
        "symbol": trade["symbol"],
        "conid": trade["conid"],
        "currency": trade["currency"],
        "side": trade["side"],
        "quantity": _plain(trade["quantity"]),
        "price": _plain(trade["price"]),
        "multiplier": _plain(trade["multiplier"]),
        "commission": _plain(trade["commission"]),
        "commission_currency": trade["commission_currency"],
        "taxes": _plain(trade["taxes"]),
        "fx_to_base": _plain(trade["fx_to_base"]),
        "order_ref": trade["order_ref"],
        "transaction_id": trade["transaction_id"],
        "trade_date": trade["trade_date"].isoformat(),
        "report_date": trade["report_date"].isoformat(),
        "statement_time_raw": trade["executed_at_raw"],
        "statement_time_utc": _utc(trade["executed_at"]),
        "legacy_time_raw": None,
        "legacy_time_utc": None,
        "legacy_strategy": None,
        "legacy_symbol": None,
        "legacy_currency": None,
        "legacy_side": None,
        "legacy_quantity": None,
        "legacy_price": None,
        "legacy_order_ref": None,
        "time_delta_seconds": None,
        "field_mismatches": "",
        "review_reason": "statement_only",
    }
    if legacy is None:
        return row
    mismatches = sorted(field for field in COMPARED_FIELDS if legacy[field] != trade[field])
    delta = _delta_seconds(trade["executed_at"], legacy["executed_at"])
    reasons = []
    if mismatches:
        reasons.append("field_mismatch")
    if delta:
        reasons.append("timestamp_offset")
    row.update({
        "legacy_time_raw": legacy["timestamp_raw"],
        "legacy_time_utc": _utc(legacy["executed_at"]),
        "legacy_strategy": legacy["strategy"] or None,
        "legacy_symbol": legacy["symbol"],
        "legacy_currency": legacy["currency"],
        "legacy_side": legacy["side"],
        "legacy_quantity": _plain(legacy["quantity"]),
        "legacy_price": _plain(legacy["price"]),
        "legacy_order_ref": legacy["order_ref"],
        "time_delta_seconds": delta,
        "field_mismatches": ",".join(mismatches),
        "review_reason": ";".join(reasons) if reasons else "matched",
    })
    return row


def _row_for_legacy_only(imported, execution_id: str, legacy: dict) -> dict:
    local_day = legacy["executed_at"].astimezone(ZoneInfo("America/New_York")).date()
    in_period = min(imported.days) <= local_day <= max(imported.days)
    return {
        "execution_id": execution_id,
        "record_origin": "ledger",
        "symbol": None,
        "conid": None,
        "currency": None,
        "side": None,
        "quantity": None,
        "price": None,
        "multiplier": None,
        "commission": None,
        "commission_currency": None,
        "taxes": None,
        "fx_to_base": None,
        "order_ref": None,
        "transaction_id": None,
        "trade_date": None,
        "report_date": None,
        "statement_time_raw": None,
        "statement_time_utc": None,
        "legacy_time_raw": legacy["timestamp_raw"],
        "legacy_time_utc": _utc(legacy["executed_at"]),
        "legacy_strategy": legacy["strategy"] or None,
        "legacy_symbol": legacy["symbol"],
        "legacy_currency": legacy["currency"],
        "legacy_side": legacy["side"],
        "legacy_quantity": _plain(legacy["quantity"]),
        "legacy_price": _plain(legacy["price"]),
        "legacy_order_ref": legacy["order_ref"],
        "time_delta_seconds": None,
        "field_mismatches": "",
        "review_reason": "ledger_only_in_period" if in_period else "ledger_only_outside_period",
    }


def stage_ledger(xml_path: Path, ledger_path: Path, output_path: Path) -> dict:
    """Build an atomic private SQLite staging ledger and return a redacted summary."""
    imported = read_flex(xml_path)
    observed, ledger_duplicates = read_observed_ledger(ledger_path)
    ledger_digest = sha256(ledger_path.read_bytes()).hexdigest()
    matched = set(imported.trades) & set(observed)
    rows = [_row_for_statement(imported, key, observed.get(key)) for key in sorted(imported.trades)]
    rows.extend(
        _row_for_legacy_only(imported, key, observed[key])
        for key in sorted(set(observed) - set(imported.trades))
    )

    offsets: dict[str, int] = {}
    mismatch_counts: dict[str, int] = {}
    for row in rows:
        if row["time_delta_seconds"]:
            label = str(row["time_delta_seconds"])
            offsets[label] = offsets.get(label, 0) + 1
        if row["field_mismatches"]:
            for field in row["field_mismatches"].split(","):
                mismatch_counts[field] = mismatch_counts.get(field, 0) + 1

    summary = {
        "schema_version": SCHEMA_VERSION,
        "source_sha256": imported.source_sha256,
        "observed_ledger_sha256": ledger_digest,
        "base_currency": imported.base_currency,
        "period_start": min(imported.days).isoformat(),
        "period_end": max(imported.days).isoformat(),
        "statement_executions": len(imported.trades),
        "observed_executions": len(observed),
        "matched_executions": len(matched),
        "statement_only_executions": len(imported.trades) - len(matched),
        "ledger_only_executions": len(observed) - len(matched),
        "equivalent_statement_duplicate_rows": imported.duplicate_rows,
        "equivalent_observed_ledger_duplicate_rows": ledger_duplicates,
        "timestamp_offset_counts": dict(sorted(offsets.items())),
        "field_mismatch_counts": dict(sorted(mismatch_counts.items())),
    }

    metadata = {key: str(value) for key, value in summary.items() if not isinstance(value, dict)}
    metadata.update({
        "tool": "dashboard.accounting_ledger",
        "scope": "private_execution_staging_only",
        "timestamp_offset_counts": json.dumps(summary["timestamp_offset_counts"], sort_keys=True),
        "field_mismatch_counts": json.dumps(summary["field_mismatch_counts"], sort_keys=True),
        "calendar_coverage_verified": "false",
        "opening_baseline_accepted": "false",
        "strategy_capital_accepted": "false",
    })

    output_path = output_path.expanduser()
    if output_path.exists() and output_path.is_dir():
        raise FlexError("output_is_directory")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent)
    os.close(handle)
    connection = None
    try:
        connection = sqlite3.connect(temporary)
        with connection:
            connection.executescript(SCHEMA)
            connection.executemany(
                f"INSERT INTO executions ({','.join(COLUMNS)}) VALUES ({','.join('?' for _ in COLUMNS)})",
                [tuple(row[column] for column in COLUMNS) for row in rows],
            )
            connection.executemany(
                "INSERT INTO metadata (key, value) VALUES (?, ?)",
                sorted(metadata.items()),
            )
        connection.close()
        connection = None
        os.replace(temporary, output_path)
    except BaseException:
        if connection is not None:
            connection.close()
        Path(temporary).unlink(missing_ok=True)
        raise
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage private Flex executions and timestamp provenance.")
    parser.add_argument("xml", type=Path)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        summary = stage_ledger(args.xml, args.ledger, args.output)
    except FlexError as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, allow_nan=False))
        raise SystemExit(2)
    except (OSError, sqlite3.Error):
        print(json.dumps({"status": "error", "reason": "ledger_write_failed"}, allow_nan=False))
        raise SystemExit(2)
    print(json.dumps({"status": "staged", **summary, "output": str(args.output)},
                     allow_nan=False, sort_keys=True))


if __name__ == "__main__":
    main()
