"""Read-only adapter from Alpaca paper-account responses to dashboard frames."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any

import pandas as pd

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest, GetPortfolioHistoryRequest


class PaperAccountDataError(RuntimeError):
    """Raised when paper-account data cannot safely be loaded for display."""


def paper_client() -> TradingClient:
    """Build a paper-only Alpaca client from the local environment."""

    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise PaperAccountDataError("Missing ALPACA_API_KEY or ALPACA_SECRET_KEY in .env.")
    return TradingClient(api_key, secret_key, paper=True)


def _value(item: Any, name: str, default: Any = None) -> Any:
    """Read either an Alpaca model attribute or a dict key."""

    return getattr(item, name, item.get(name, default) if isinstance(item, dict) else default)


def portfolio_history_frame(history: Any) -> pd.DataFrame:
    """Convert Alpaca's portfolio-history model into a chronological frame."""

    frame = pd.DataFrame(
        {
            "timestamp": _value(history, "timestamp", []),
            "equity": _value(history, "equity", []),
            "profit_loss": _value(history, "profit_loss", []),
            "profit_loss_pct": _value(history, "profit_loss_pct", []),
        }
    )
    if frame.empty:
        return frame
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="s", utc=True, errors="coerce")
    for column in ["equity", "profit_loss", "profit_loss_pct"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["timestamp", "equity"]).sort_values("timestamp").reset_index(drop=True)


def positions_frame(positions: list[Any]) -> pd.DataFrame:
    """Normalize Alpaca open positions for table and exposure views."""

    rows = []
    for position in positions:
        rows.append(
            {
                "symbol": _value(position, "symbol"),
                "quantity": _value(position, "qty"),
                "side": _value(position, "side"),
                "market_value": _value(position, "market_value"),
                "cost_basis": _value(position, "cost_basis"),
                "average_entry_price": _value(position, "avg_entry_price"),
                "current_price": _value(position, "current_price"),
                "unrealized_pl": _value(position, "unrealized_pl"),
                "unrealized_plpc": _value(position, "unrealized_plpc"),
            }
        )
    frame = pd.DataFrame(rows)
    for column in ["quantity", "market_value", "cost_basis", "average_entry_price", "current_price", "unrealized_pl", "unrealized_plpc"]:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def _strategy_id(client_order_id: Any) -> str:
    value = str(client_order_id or "")
    if not value.startswith("nova-"):
        return "External / untagged"
    parts = value.split("-", 2)
    return parts[1] if len(parts) == 3 else "Nova"


def orders_frame(orders: list[Any]) -> pd.DataFrame:
    """Normalize recent Alpaca orders, including Nova's strategy identifier."""

    rows = []
    for order in orders:
        client_order_id = _value(order, "client_order_id")
        rows.append(
            {
                "submitted_at": _value(order, "submitted_at"),
                "symbol": _value(order, "symbol"),
                "side": str(_value(order, "side", "")).lower(),
                "status": str(_value(order, "status", "")).lower(),
                "quantity": _value(order, "qty"),
                "filled_quantity": _value(order, "filled_qty"),
                "filled_avg_price": _value(order, "filled_avg_price"),
                "type": str(_value(order, "type", "")).lower(),
                "strategy": _strategy_id(client_order_id),
                "client_order_id": client_order_id,
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["submitted_at"] = pd.to_datetime(frame["submitted_at"], utc=True, errors="coerce")
    for column in ["quantity", "filled_quantity", "filled_avg_price"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.sort_values("submitted_at", ascending=False, na_position="last").reset_index(drop=True)


def load_paper_account_data(period: str = "1A") -> dict[str, Any]:
    """Load account, equity history, open positions, and recent orders from Alpaca."""

    client = paper_client()
    history_request = GetPortfolioHistoryRequest(period=period, timeframe="1D", extended_hours=False)
    history = client.get_portfolio_history(history_request)
    orders_request = GetOrdersRequest(status=QueryOrderStatus.ALL, limit=500, nested=True)
    return {
        "account": client.get_account(),
        "history": portfolio_history_frame(history),
        "positions": positions_frame(client.get_all_positions()),
        "orders": orders_frame(client.get_orders(filter=orders_request)),
        "loaded_at": datetime.now(timezone.utc),
    }
