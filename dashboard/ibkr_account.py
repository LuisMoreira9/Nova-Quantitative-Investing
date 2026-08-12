"""Read-only IBKR paper-account adapter used by the Nova dashboard."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
from time import monotonic
from typing import Any

import pandas as pd

from core.ibkr_adapter import IBKRClient
from core.main_executor import ibkr_connection_settings
from core.yahoo_price_provider import YahooFxPriceProvider
from dashboard.strategy_attribution import (
    append_pnl_snapshot,
    execution_history_frame,
    marked_strategy_pnl,
    sync_execution_ledger,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HISTORY_PATH = PROJECT_ROOT / "data" / "ibkr_account_history.csv"
STRATEGY_MARK_CACHE_SECONDS = 300
_strategy_sleeves_cache: pd.DataFrame | None = None
_strategy_sleeves_cached_at = 0.0

EXCHANGE_LABELS = {
    "AEB": "Euronext Amsterdam",
    "IBIS": "Xetra",
    "SBF": "Euronext Paris",
    "BM": "BME Madrid",
    "TSEJ": "Tokyo Stock Exchange",
    "NASDAQ": "NASDAQ",
    "NYSE": "NYSE",
    "ARCA": "NYSE Arca",
    "SMART": "IBKR SMART",
    "UNAVAILABLE": "Historical venue unavailable",
}


def _frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    for column in ["quantity", "market_value", "cost_basis", "average_entry_price", "current_price", "unrealized_pl", "unrealized_plpc", "filled_quantity", "filled_avg_price"]:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def _add_exchange_labels(frame: pd.DataFrame) -> pd.DataFrame:
    """Make native venue visible without losing IBKR's exchange code."""

    if frame.empty or "exchange" not in frame:
        return frame
    frame = frame.copy()
    frame["exchange"] = frame["exchange"].fillna("Unknown").astype(str).str.upper()
    frame["market"] = frame["exchange"].map(EXCHANGE_LABELS).fillna(frame["exchange"])
    return frame


def _append_equity_snapshot(account: dict[str, str]) -> pd.DataFrame:
    """Persist account equity on dashboard refresh for a broker-owned NAV chart."""

    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    row = pd.DataFrame([{"timestamp": now.isoformat(), "equity": float(account["equity"])}])
    if HISTORY_PATH.exists():
        history = pd.read_csv(HISTORY_PATH)
        timestamps = pd.to_datetime(history["timestamp"], utc=True, errors="coerce")
        # Avoid writing duplicate points when Streamlit reruns rapidly.
        if not timestamps.empty and (now - timestamps.iloc[-1]).total_seconds() < 55:
            return history.assign(timestamp=timestamps)
        history = pd.concat([history, row], ignore_index=True)
    else:
        history = row
    history.to_csv(HISTORY_PATH, index=False)
    history["timestamp"] = pd.to_datetime(history["timestamp"], utc=True, errors="coerce")
    return history


def _strategy_sleeves_for_dashboard(ledger: pd.DataFrame, base_currency: str) -> tuple[pd.DataFrame, bool]:
    """Limit Yahoo marking work without delaying account/order snapshots.

    The broker data in the dashboard must be current on every refresh.  The
    per-strategy gross-P&L view is analytical and requires external Yahoo
    marks for every open Nova sleeve, which can take tens of seconds for a
    large paper portfolio.  Reuse its most recent marks for a short period so
    the read-only TWS account, positions, and execution table remain prompt.
    """

    global _strategy_sleeves_cache, _strategy_sleeves_cached_at
    if _strategy_sleeves_cache is not None and monotonic() - _strategy_sleeves_cached_at < STRATEGY_MARK_CACHE_SECONDS:
        return _strategy_sleeves_cache.copy(), False
    sleeves = marked_strategy_pnl(ledger, base_currency)
    _strategy_sleeves_cache = sleeves.copy()
    _strategy_sleeves_cached_at = monotonic()
    return sleeves, True


def load_paper_account_data() -> dict[str, Any]:
    """Read current account data from TWS without placing or altering orders."""

    host, port, client_id = ibkr_connection_settings()
    client = IBKRClient(host, port, client_id + 1)
    client.connect_and_start()
    try:
        account = client.get_account()
        portfolio, currency_cash = client.get_portfolio_and_cash()
        positions = _add_exchange_labels(_frame(portfolio))
        base_currency = os.getenv("NOVA_BASE_CURRENCY", "EUR").upper()
        # The dashboard must not consume an IBKR quote line.  The hedge is an
        # analytical display, so obtain its conversion from the same external
        # FX source the risk gateway uses.
        fx_provider = YahooFxPriceProvider()
        hedge_rows: list[dict[str, Any]] = []
        if not positions.empty:
            positions["native_market_value"] = positions["quantity"] * positions["current_price"]
            positions["fx_to_base"] = 1.0
            for currency in sorted(set(positions.get("currency", pd.Series(dtype=str)).dropna().astype(str).str.upper())):
                if currency == base_currency:
                    continue
                try:
                    fx_to_base = fx_provider.get_fx_rate_to_base(currency, base_currency)
                    positions.loc[positions["currency"].str.upper() == currency, "fx_to_base"] = fx_to_base
                except Exception as exc:
                    hedge_rows.append(
                        {
                            "currency": currency,
                            "status": "FX quote unavailable",
                            "detail": str(exc),
                        }
                    )
            positions["native_market_value_base"] = positions["native_market_value"] * positions["fx_to_base"]
            for currency, group in positions.groupby("currency", dropna=True):
                currency = str(currency).upper()
                if currency == base_currency:
                    continue
                stock_native_value = float(group["native_market_value"].sum())
                currency_cash_value = float(currency_cash.get(currency, 0.0))
                net_native_value = stock_native_value + currency_cash_value
                base_value = net_native_value * float(group["fx_to_base"].iloc[0])
                hedge_rows.append(
                    {
                        "currency": currency,
                        "status": "Net exposure",
                        "stock_native_exposure": stock_native_value,
                        "cash_native_balance": currency_cash_value,
                        "net_native_exposure": net_native_value,
                        "net_base_exposure": base_value,
                        "detail": "Net stock exposure plus native cash balance; no simulated or actual FX order is included.",
                    }
                )
        open_order_rows = client.get_open_orders()
        executions = client.get_today_executions()
        orders = _add_exchange_labels(_frame([*open_order_rows, *executions]))
        strategy_ledger = sync_execution_ledger(executions)
        order_history = execution_history_frame(strategy_ledger)
        open_orders = _add_exchange_labels(_frame(open_order_rows))
        if not open_orders.empty:
            open_orders = open_orders.copy()
            open_orders["source"] = "TWS open order"
            order_history = pd.concat([order_history, open_orders], ignore_index=True, sort=False)
        strategy_sleeves, strategy_marks_refreshed = _strategy_sleeves_for_dashboard(strategy_ledger, base_currency)
        strategy_history = append_pnl_snapshot(strategy_sleeves, append=strategy_marks_refreshed)
        history = _append_equity_snapshot(account)
    finally:
        client.close()
    return {
        "account": account,
        "history": history,
        "positions": positions,
        "orders": orders,
        "order_history": _add_exchange_labels(order_history),
        "fx_hedges": _frame(hedge_rows),
        "strategy_sleeves": strategy_sleeves,
        "strategy_marks_refreshed": strategy_marks_refreshed,
        "strategy_history": strategy_history,
        "currency_cash": currency_cash,
        "base_currency": base_currency,
        "loaded_at": datetime.now(timezone.utc),
    }
