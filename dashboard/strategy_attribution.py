"""Local, read-only attribution for Nova-tagged IBKR executions.

TWS owns the account-level equity figure.  This module never attempts to
split that figure: it maintains a separate, auditable cash-and-positions sleeve
for each Nova order reference and reports its marked *gross P&L*.  Manual or
untagged IBKR activity therefore remains in total account equity only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

import pandas as pd

from core.sp500_yfinance_executor import fetch_closes
from core.stoxx_europe_600 import approved_universe
from core.yahoo_price_provider import YahooFxPriceProvider


ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = ROOT / "data" / "nova_strategy_execution_ledger.csv"
HISTORY_PATH = ROOT / "data" / "nova_strategy_pnl_history.csv"
LEDGER_COLUMNS = [
    "execution_id", "timestamp", "strategy", "order_ref", "symbol",
    "currency", "side", "quantity", "price",
]


def strategy_from_order_ref(order_ref: str | None) -> str:
    """Return a stable display name for Nova's compact IBKR order reference."""

    value = str(order_ref or "")
    if not value.startswith("nova-"):
        return "External / untagged"
    compact = value.split("-", 2)[1] if value.count("-") >= 2 else "Nova"
    known = {
        "Sp500YfinanceMomentu": "S&P 500 momentum reversal",
        "EuropeYfinanceMoment": "Europe momentum reversal",
        "PaperOrderSmokeTest": "Paper order smoke test",
        "DashboardPositionDem": "Dashboard position demo",
        "FxHedge": "FX hedge",
    }
    return known.get(compact, compact)


def _execution_id(row: dict[str, Any]) -> str:
    supplied = str(row.get("execution_id") or "").strip()
    if supplied:
        return supplied
    immutable = "|".join(str(row.get(key, "")) for key in ("submitted_at", "client_order_id", "symbol", "side", "filled_quantity", "filled_avg_price"))
    return sha256(immutable.encode("utf-8")).hexdigest()


def sync_execution_ledger(executions: list[dict[str, Any]]) -> pd.DataFrame:
    """Upsert Nova executions observed from TWS into an append-only local ledger."""

    incoming: list[dict[str, Any]] = []
    for row in executions:
        order_ref = str(row.get("client_order_id") or "")
        if not order_ref.startswith("nova-"):
            continue
        quantity = row.get("filled_quantity", row.get("quantity"))
        price = row.get("filled_avg_price")
        if quantity is None or price is None:
            continue
        incoming.append(
            {
                "execution_id": _execution_id(row),
                "timestamp": str(row.get("submitted_at") or ""),
                "strategy": strategy_from_order_ref(order_ref),
                "order_ref": order_ref,
                "symbol": str(row.get("symbol") or "").upper(),
                "currency": str(row.get("currency") or "").upper(),
                "side": str(row.get("side") or "").lower(),
                "quantity": float(quantity),
                "price": float(price),
            }
        )
    existing = pd.read_csv(LEDGER_PATH) if LEDGER_PATH.exists() else pd.DataFrame(columns=LEDGER_COLUMNS)
    if incoming:
        combined = pd.concat([existing, pd.DataFrame(incoming)], ignore_index=True)
        combined = combined.drop_duplicates(subset="execution_id", keep="last")
    else:
        combined = existing
    if combined.empty:
        return pd.DataFrame(columns=LEDGER_COLUMNS)
    combined = combined.reindex(columns=LEDGER_COLUMNS)
    combined["timestamp"] = pd.to_datetime(combined["timestamp"], utc=True, errors="coerce")
    combined = combined.dropna(subset=["timestamp", "symbol", "currency", "quantity", "price"])
    combined = combined.sort_values(["timestamp", "execution_id"]).reset_index(drop=True)
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    combined.assign(timestamp=combined["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S%z")).to_csv(LEDGER_PATH, index=False)
    return combined


def _ticker_for(symbol: str) -> str:
    row = approved_universe().get(symbol)
    return row.yfinance_ticker if row is not None else symbol


def _latest_marks(symbols: set[str]) -> dict[str, float]:
    """Fetch external one-minute marks without consuming IBKR quote lines."""

    tickers = {_ticker_for(symbol) for symbol in symbols}
    closes = fetch_closes(sorted(tickers)) if tickers else {}
    return {
        symbol: float(closes[_ticker_for(symbol)].iloc[-1])
        for symbol in symbols
        if _ticker_for(symbol) in closes and not closes[_ticker_for(symbol)].empty
    }


def marked_strategy_pnl(ledger: pd.DataFrame, base_currency: str) -> pd.DataFrame:
    """Replay fills into per-strategy cash/position sleeves and mark them externally."""

    columns = ["strategy", "currency", "cash_native", "position_native", "gross_pnl_native", "fx_to_base", "gross_pnl_base", "open_symbols", "mark_status"]
    if ledger.empty:
        return pd.DataFrame(columns=columns)
    working = ledger.copy()
    working["quantity"] = pd.to_numeric(working["quantity"], errors="coerce")
    working["price"] = pd.to_numeric(working["price"], errors="coerce")
    working = working.dropna(subset=["quantity", "price"])
    quantities: dict[tuple[str, str, str], float] = {}
    cash: dict[tuple[str, str], float] = {}
    for row in working.itertuples(index=False):
        sleeve = (row.strategy, row.currency)
        position_key = (row.strategy, row.currency, row.symbol)
        signed_quantity = float(row.quantity) if row.side == "bot" or row.side == "buy" else -float(row.quantity)
        quantities[position_key] = quantities.get(position_key, 0.0) + signed_quantity
        cash[sleeve] = cash.get(sleeve, 0.0) - signed_quantity * float(row.price)

    open_symbols = {symbol for (_, _, symbol), quantity in quantities.items() if abs(quantity) > 1e-9}
    marks = _latest_marks(open_symbols)
    fx = YahooFxPriceProvider()
    rows: list[dict[str, Any]] = []
    for sleeve, cash_value in sorted(cash.items()):
        strategy, currency = sleeve
        holdings = {symbol: quantity for (owner, denomination, symbol), quantity in quantities.items() if (owner, denomination) == sleeve and abs(quantity) > 1e-9}
        unmarked = sorted(symbol for symbol in holdings if symbol not in marks)
        position_value = sum(quantity * marks[symbol] for symbol, quantity in holdings.items() if symbol in marks)
        gross_pnl_native = cash_value + position_value
        try:
            fx_to_base = fx.get_fx_rate_to_base(currency, base_currency)
            gross_pnl_base: float | None = gross_pnl_native * fx_to_base if not unmarked else None
        except Exception:
            fx_to_base, gross_pnl_base = None, None
        rows.append(
            {
                "strategy": strategy,
                "currency": currency,
                "cash_native": cash_value,
                "position_native": position_value,
                "gross_pnl_native": gross_pnl_native,
                "fx_to_base": fx_to_base,
                "gross_pnl_base": gross_pnl_base,
                "open_symbols": ", ".join(sorted(holdings)),
                "mark_status": "Marked from Yahoo" if not unmarked and fx_to_base is not None else f"Unmarked: {', '.join(unmarked) or 'FX unavailable'}",
            }
        )
    return pd.DataFrame(rows, columns=columns)


def append_pnl_snapshot(sleeves: pd.DataFrame) -> pd.DataFrame:
    """Store at most one marked-P&L point per minute for each strategy."""

    now = datetime.now(timezone.utc)
    history = pd.read_csv(HISTORY_PATH) if HISTORY_PATH.exists() else pd.DataFrame(columns=["timestamp", "strategy", "gross_pnl_base"])
    if not history.empty:
        history["timestamp"] = pd.to_datetime(history["timestamp"], utc=True, errors="coerce")
    latest = history["timestamp"].max() if not history.empty else pd.NaT
    if pd.isna(latest) or (now - latest).total_seconds() >= 55:
        new_rows = sleeves.loc[sleeves["gross_pnl_base"].notna(), ["strategy", "gross_pnl_base"]].copy()
        new_rows["timestamp"] = now
        history = pd.concat([history, new_rows[["timestamp", "strategy", "gross_pnl_base"]]], ignore_index=True)
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        history.assign(timestamp=pd.to_datetime(history["timestamp"], utc=True).dt.strftime("%Y-%m-%dT%H:%M:%S%z")).to_csv(HISTORY_PATH, index=False)
    return history
