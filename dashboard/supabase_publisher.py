"""Publish a sanitised, read-only portfolio snapshot to Supabase.

This module only makes an outbound HTTPS request.  It never opens an inbound
port, exposes TWS, or submits/changes/cancels an IBKR order.
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dashboard.portfolio_api import _load_snapshot


def public_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return the minimum browser-facing shape; omit broker/order identifiers."""

    account = snapshot.get("account", {})
    positions = [
        {
            key: position.get(key)
            for key in (
                "symbol", "market", "exchange", "currency", "quantity", "current_price", "market_value",
                "unrealized_pl", "native_market_value_base", "country", "region", "sector",
            )
        }
        for position in snapshot.get("positions", [])
    ]
    return {
        "schema_version": 1,
        "loaded_at": snapshot.get("loaded_at"),
        "base_currency": snapshot.get("base_currency"),
        "account": {key: account.get(key) for key in ("equity", "cash", "buying_power")},
        "history": snapshot.get("history", []),
        "positions": positions,
        "geographic_exposure": snapshot.get("geographic_exposure", []),
        "sector_exposure": snapshot.get("sector_exposure", []),
        "strategy_sleeves": snapshot.get("strategy_sleeves", []),
        "strategy_history": snapshot.get("strategy_history", []),
    }


def publish_snapshot(snapshot: dict[str, Any] | None = None) -> None:
    """Write a snapshot using the host-only Supabase secret key."""

    base_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    secret = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not base_url or not secret:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in the host .env.")

    payload = json.dumps({"snapshot": public_snapshot(snapshot or _load_snapshot())}).encode("utf-8")
    request = Request(
        f"{base_url}/rest/v1/portfolio_snapshots",
        data=payload,
        method="POST",
        headers={
            "apikey": secret,
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
    )
    try:
        with urlopen(request, timeout=20):
            pass
    except HTTPError as exc:
        raise RuntimeError(f"Supabase rejected portfolio snapshot ({exc.code}): {exc.read().decode('utf-8', 'replace')}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach Supabase: {exc.reason}") from exc


if __name__ == "__main__":
    from core.main_executor import load_local_env

    load_local_env()
    publish_snapshot()
    print("Published sanitised portfolio snapshot to Supabase.")
