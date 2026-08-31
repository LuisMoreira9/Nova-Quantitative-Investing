"""Read browser-safe Nova portfolio data from Supabase for hosted dashboards."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd


def _settings() -> tuple[str, str]:
    base_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_PUBLISHABLE_KEY") or os.getenv("PUBLIC_SUPABASE_PUBLISHABLE_KEY", "")
    if not base_url or not key:
        raise RuntimeError(
            "Hosted dashboard needs SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY (or PUBLIC_SUPABASE_PUBLISHABLE_KEY)."
        )
    return base_url, key


def _read_json(base_url: str, key: str, path: str) -> Any:
    request = Request(
        f"{base_url}/rest/v1/{path}",
        headers={"apikey": key, "Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"Supabase read failed ({exc.code}): {exc.read().decode('utf-8', 'replace')}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach Supabase: {exc.reason}") from exc


def _frame(value: object) -> pd.DataFrame:
    return pd.DataFrame(value if isinstance(value, list) else [])


def load_hosted_dashboard_data() -> dict[str, Any]:
    """Build the Streamlit data shape from the latest hosted snapshot and fills."""

    base_url, key = _settings()
    snapshots = _read_json(
        base_url,
        key,
        "portfolio_snapshots?select=snapshot,published_at&order=published_at.desc&limit=1",
    )
    if not snapshots or not snapshots[0].get("snapshot"):
        raise RuntimeError("No hosted portfolio snapshot has been published yet.")
    snapshot = snapshots[0]["snapshot"]
    executions = _read_json(
        base_url,
        key,
        "portfolio_trade_executions?select=id,executed_at,strategy,symbol,exchange,currency,side,quantity,price&order=executed_at.desc&limit=1000",
    )
    order_history = _frame(executions).rename(
        columns={
            "id": "public_execution_id",
            "executed_at": "submitted_at",
            "price": "filled_avg_price",
        }
    )
    if not order_history.empty:
        order_history["status"] = "filled"
        order_history["filled_quantity"] = order_history["quantity"]
        order_history["type"] = "execution"
        order_history["source"] = "Supabase trade ledger"
        order_history["market"] = order_history["exchange"]

    loaded_at = pd.to_datetime(snapshot.get("loaded_at"), utc=True, errors="coerce")
    if pd.isna(loaded_at):
        loaded_at = datetime.now(timezone.utc)
    return {
        "account": snapshot.get("account", {}),
        "history": _frame(snapshot.get("history")),
        "positions": _frame(snapshot.get("positions")),
        "orders": pd.DataFrame(),
        "order_history": order_history,
        "fx_hedges": _frame(snapshot.get("fx_hedges")),
        "strategy_sleeves": _frame(snapshot.get("strategy_sleeves")),
        "strategy_history": _frame(snapshot.get("strategy_history")),
        "currency_cash": {},
        "base_currency": str(snapshot.get("base_currency") or "EUR"),
        "loaded_at": loaded_at,
    }
