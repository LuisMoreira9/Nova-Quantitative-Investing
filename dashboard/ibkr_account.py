"""Read-only IBKR paper-account adapter used by the Nova dashboard."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any

import pandas as pd

from core.ibkr_adapter import IBKRClient
from core.main_executor import ibkr_connection_settings
from core.yahoo_price_provider import YahooFxPriceProvider
from dashboard.strategy_attribution import (
    append_pnl_snapshot,
    marked_strategy_pnl,
    sync_execution_ledger,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HISTORY_PATH = PROJECT_ROOT / "data" / "ibkr_account_history.csv"

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
        executions = client.get_today_executions()
        orders = _add_exchange_labels(_frame([*client.get_open_orders(), *executions]))
        strategy_ledger = sync_execution_ledger(executions)
        strategy_sleeves = marked_strategy_pnl(strategy_ledger, base_currency)
        strategy_history = append_pnl_snapshot(strategy_sleeves)
        history = _append_equity_snapshot(account)
    finally:
        client.close()
    return {
        "account": account,
        "history": history,
        "positions": positions,
        "orders": orders,
        "fx_hedges": _frame(hedge_rows),
        "strategy_sleeves": strategy_sleeves,
        "strategy_history": strategy_history,
        "currency_cash": currency_cash,
        "base_currency": base_currency,
        "loaded_at": datetime.now(timezone.utc),
    }
