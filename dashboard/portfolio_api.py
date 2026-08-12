"""Local, read-only portfolio API for the Nova Quant Club website.

Run locally with ``python -m dashboard.portfolio_api`` while paper TWS is
available.  This process never submits, modifies, or cancels an IBKR order.
It deliberately binds to localhost so it is not reachable from the network.
"""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import date, datetime
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Any

import pandas as pd
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.main_executor import load_local_env
from dashboard.ibkr_account import load_paper_account_data


def _json_value(value: Any) -> Any:
    """Return a JSON-safe primitive, using null for missing numeric values."""

    if value is None or value is pd.NaT:
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    return [
        {str(key): _json_value(value) for key, value in row.items()}
        for row in frame.to_dict(orient="records")
    ]


app = FastAPI(title="Nova local paper-portfolio API", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ],
    allow_methods=["GET"],
    allow_headers=[],
)

_CACHE_SECONDS = 15.0
_snapshot_cache: dict[str, Any] | None = None
_snapshot_cached_at = 0.0
_snapshot_lock = Lock()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "scope": "local read-only"}


@app.get("/api/portfolio")
def portfolio() -> dict[str, Any]:
    """Return current local TWS data plus the durable Nova strategy ledger."""

    global _snapshot_cache, _snapshot_cached_at
    with _snapshot_lock:
        if _snapshot_cache is not None and monotonic() - _snapshot_cached_at < _CACHE_SECONDS:
            return _snapshot_cache
        try:
            snapshot = load_paper_account_data()
        except Exception as exc:
            # Preserve the last verified local state across a brief TWS/Yahoo
            # delay instead of making the website blank during an update.
            if _snapshot_cache is not None:
                return _snapshot_cache
            raise HTTPException(
                status_code=503,
                detail=(
                    "Could not load local IBKR paper-account data. Confirm paper TWS is running "
                    "and socket clients are enabled. "
                    f"Details: {exc}"
                ),
            ) from exc

        _snapshot_cache = json.loads(
            json.dumps(
                {
                    "account": snapshot["account"],
                    "base_currency": snapshot["base_currency"],
                    "loaded_at": snapshot["loaded_at"],
                    "history": _records(snapshot["history"]),
                    "positions": _records(snapshot["positions"]),
                    "strategy_sleeves": _records(snapshot["strategy_sleeves"]),
                    "strategy_history": _records(snapshot["strategy_history"]),
                },
                default=_json_value,
            )
        )
        _snapshot_cached_at = monotonic()
        return _snapshot_cache


if __name__ == "__main__":
    load_local_env()
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("NOVA_PORTFOLIO_API_PORT", "8765")))
