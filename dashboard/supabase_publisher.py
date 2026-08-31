"""Publish browser-safe IBKR paper data to Supabase over outbound HTTPS only."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from time import sleep
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dashboard.portfolio_api import _load_snapshot


def public_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return the current browser-facing portfolio state without broker IDs."""

    account = snapshot.get("account", {})
    positions = [
        {
            key: position.get(key)
            for key in (
                "symbol", "market", "exchange", "currency", "side", "quantity", "current_price",
                "market_value", "unrealized_pl", "native_market_value", "native_market_value_base",
                "fx_to_base", "country", "region", "sector",
            )
        }
        for position in snapshot.get("positions", [])
    ]
    return {
        "schema_version": 2,
        "loaded_at": snapshot.get("loaded_at"),
        "base_currency": snapshot.get("base_currency"),
        "account": {key: account.get(key) for key in ("equity", "cash", "buying_power")},
        "history": snapshot.get("history", []),
        "positions": positions,
        "geographic_exposure": snapshot.get("geographic_exposure", []),
        "sector_exposure": snapshot.get("sector_exposure", []),
        "strategy_sleeves": snapshot.get("strategy_sleeves", []),
        "strategy_history": snapshot.get("strategy_history", []),
        "fx_hedges": snapshot.get("fx_hedges", []),
    }


def public_executions(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Return durable Nova fills with a non-broker public identifier."""

    rows: list[dict[str, Any]] = []
    for execution in snapshot.get("executions", []):
        if execution.get("source") != "Nova ledger" or str(execution.get("status", "")).lower() != "filled":
            continue
        execution_id = str(execution.get("execution_id") or "").strip()
        executed_at = execution.get("submitted_at")
        quantity = execution.get("filled_quantity", execution.get("quantity"))
        price = execution.get("filled_avg_price")
        side = str(execution.get("side") or "").lower()
        if not execution_id or not executed_at or quantity is None or price is None or side not in {"buy", "sell", "bot", "sld"}:
            continue
        rows.append(
            {
                "id": sha256(execution_id.encode("utf-8")).hexdigest(),
                "executed_at": executed_at,
                "strategy": str(execution.get("strategy") or "Nova"),
                "symbol": str(execution.get("symbol") or "").upper(),
                "exchange": str(execution.get("market") or execution.get("exchange") or ""),
                "currency": str(execution.get("currency") or "").upper(),
                "side": "buy" if side in {"buy", "bot"} else "sell",
                "quantity": abs(float(quantity)),
                "price": float(price),
            }
        )
    return rows


def _settings() -> tuple[str, str]:
    base_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    secret = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not base_url or not secret:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in the host .env.")
    return base_url, secret


def _write(base_url: str, secret: str, table: str, payload: dict[str, Any] | list[dict[str, Any]], *, upsert: bool = False) -> None:
    endpoint = f"{base_url}/rest/v1/{table}"
    if upsert:
        endpoint += "?on_conflict=id"
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "apikey": secret,
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal" if upsert else "return=minimal",
        },
    )
    try:
        with urlopen(request, timeout=20):
            pass
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"Supabase rejected {table} ({exc.code}): {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach Supabase: {exc.reason}") from exc


def publish_snapshot(snapshot: dict[str, Any] | None = None) -> int:
    """Write the latest state and idempotently upsert all observed Nova fills."""

    base_url, secret = _settings()
    source = snapshot or _load_snapshot()
    _write(base_url, secret, "portfolio_snapshots", {"snapshot": public_snapshot(source)})
    executions = public_executions(source)
    if executions:
        _write(base_url, secret, "portfolio_trade_executions", executions, upsert=True)
    return len(executions)


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish Nova paper data to Supabase.")
    parser.add_argument("--watch", action="store_true", help="Keep publishing at the configured interval.")
    args = parser.parse_args()

    from core.main_executor import load_local_env

    load_local_env()
    interval = max(15, int(os.getenv("NOVA_SUPABASE_PUBLISH_SECONDS", "60")))
    while True:
        count = publish_snapshot()
        print(f"Published portfolio snapshot and {count} Nova execution(s) to Supabase.")
        if not args.watch:
            return
        sleep(interval)


if __name__ == "__main__":
    main()
