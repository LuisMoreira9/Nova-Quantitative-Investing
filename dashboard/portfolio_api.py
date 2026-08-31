"""Local, read-only portfolio API for the Nova Quant Club website.

Run locally with ``python -m dashboard.portfolio_api`` while paper TWS is
available.  This process never submits, modifies, or cancels an IBKR order.
It deliberately binds to localhost so it is not reachable from the network.
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
from datetime import date, datetime
from pathlib import Path
from threading import Lock, Thread
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
from dashboard.portfolio_classification import enrich_positions, exposure_by


LOGGER = logging.getLogger(__name__)


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

# Keep the website responsive after a paper fill without turning every browser
# refresh into an account query.  The value can be raised on a constrained host
# through NOVA_PORTFOLIO_API_CACHE_SECONDS.
_CACHE_SECONDS = max(1.0, float(os.getenv("NOVA_PORTFOLIO_API_CACHE_SECONDS", "5")))
_snapshot_cache: dict[str, Any] | None = None
_snapshot_cached_at = 0.0
_snapshot_lock = Lock()
_snapshot_refreshing = False


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "scope": "local read-only"}


def _load_snapshot() -> dict[str, Any]:
    """Build one durable API snapshot outside request handling when possible."""

    # Streamlit uses the default read-only client ID offset (+1). Keep the
    # website bridge on a separate connection so its refresh cannot evict the
    # dashboard from TWS (IBKR error 326).
    snapshot = load_paper_account_data(client_id_offset=2)
    positions = enrich_positions(snapshot["positions"])
    return json.loads(
        json.dumps(
            {
                "account": snapshot["account"],
                "base_currency": snapshot["base_currency"],
                "loaded_at": snapshot["loaded_at"],
                "history": _records(snapshot["history"]),
                "positions": _records(positions),
                "geographic_exposure": _records(exposure_by(positions, "region")),
                "sector_exposure": _records(exposure_by(positions, "sector")),
                "strategy_sleeves": _records(snapshot["strategy_sleeves"]),
                "strategy_history": _records(snapshot["strategy_history"]),
            },
            default=_json_value,
        )
    )


def _refresh_snapshot() -> None:
    """Refresh in the background so a slow broker query never blocks the UI."""

    global _snapshot_cache, _snapshot_cached_at, _snapshot_refreshing
    try:
        refreshed = _load_snapshot()
        with _snapshot_lock:
            _snapshot_cache = refreshed
            _snapshot_cached_at = monotonic()
    except Exception as exc:
        # The previous verified snapshot remains available to the website.
        # Do not let a transient TWS response turn into an unhandled thread
        # exception or erase usable portfolio data.
        LOGGER.warning("Background portfolio refresh failed: %s", exc)
    finally:
        with _snapshot_lock:
            _snapshot_refreshing = False


@app.get("/api/portfolio")
def portfolio() -> dict[str, Any]:
    """Return current local TWS data plus the durable Nova strategy ledger."""

    global _snapshot_cache, _snapshot_cached_at, _snapshot_refreshing
    with _snapshot_lock:
        if _snapshot_cache is not None and monotonic() - _snapshot_cached_at < _CACHE_SECONDS:
            return _snapshot_cache
        # First request needs a source-of-truth snapshot. Thereafter return the
        # last verified snapshot immediately and refresh asynchronously.
        if _snapshot_cache is None:
            try:
                _snapshot_cache = _load_snapshot()
                _snapshot_cached_at = monotonic()
                return _snapshot_cache
            except Exception as exc:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Could not load local IBKR paper-account data. Confirm paper TWS is running "
                        "and socket clients are enabled. "
                        f"Details: {exc}"
                    ),
                ) from exc
        if not _snapshot_refreshing:
            _snapshot_refreshing = True
            Thread(target=_refresh_snapshot, name="portfolio-snapshot-refresh", daemon=True).start()
        return _snapshot_cache


if __name__ == "__main__":
    load_local_env()
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("NOVA_PORTFOLIO_API_PORT", "8765")))
