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
from dashboard.timestamps import ibkr_execution_timestamp


def risk_enabled() -> bool:
    """Rollback switch: set NOVA_RISK_ENABLED=0 to disable risk pub/display input."""

    return os.getenv("NOVA_RISK_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}


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
    # Optional risk object; schema 2 payloads remain readable (risk=None).
    # Raw broker contract IDs (con_id/local_symbol), account IDs, credentials,
    # and internal errors are never included: risk carries only sanitised scalars.
    risk = snapshot.get("risk") if risk_enabled() else None
    if isinstance(risk, dict):
        risk = {k: risk.get(k) for k in (
            "status", "portfolio_id", "calculated_at", "observed_at", "source_portfolio_time",
            "verification_time", "history_cutoff", "confidence", "horizon",
            "sample_count", "lookback_start", "lookback_end", "quantile_convention",
            "base_currency", "methodology_version", "var_fraction", "var_eur",
            "worst_21_fraction", "worst_window", "coverage", "reasons", "last_valid",
            "last_checked_at",
        )}
    return {
        "schema_version": 3,
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
        "risk": risk,
    }


def public_executions(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Return durable Nova fills with a non-broker public identifier."""

    rows: list[dict[str, Any]] = []
    for execution in snapshot.get("executions", []):
        if execution.get("source") != "Nova ledger" or str(execution.get("status", "")).lower() != "filled":
            continue
        execution_id = str(execution.get("execution_id") or "").strip()
        executed_at = ibkr_execution_timestamp(execution.get("submitted_at"))
        quantity = execution.get("filled_quantity", execution.get("quantity"))
        price = execution.get("filled_avg_price")
        side = str(execution.get("side") or "").lower()
        if not execution_id or executed_at is None or quantity is None or price is None or side not in {"buy", "sell", "bot", "sld"}:
            continue
        rows.append(
            {
                "id": sha256(execution_id.encode("utf-8")).hexdigest(),
                "executed_at": executed_at.isoformat(),
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


TERMINAL_METHODOLOGY_VERSION = "terminal-1"

# Fields that must never appear in the public whitelist row. The builder
# constructs the row key-by-key (rather than filtering), but the test suite
# asserts this list stays out even if the bundle shape grows.
TERMINAL_PUBLIC_FORBIDDEN_FIELDS = frozenset({
    "equity", "cash", "buying_power", "balance", "quantity", "holdings",
    "positions", "executions", "allocations", "account",
})


def _parse_observation(value: Any) -> Any | None:
    """Return a timezone-aware UTC datetime for an ISO observation, else None."""

    from datetime import timezone

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        from datetime import datetime

        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _to_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _equity_points(history: Any) -> list[tuple[Any, float]]:
    """Return sorted (observed_at, equity) points with usable values only."""

    points: list[tuple[Any, float]] = []
    if not isinstance(history, list):
        return points
    for row in history:
        if not isinstance(row, dict):
            continue
        observed = _parse_observation(row.get("timestamp"))
        equity = _to_float(row.get("equity"))
        if observed is None or equity is None or equity <= 0:
            continue
        points.append((observed, equity))
    points.sort(key=lambda point: point[0])
    return points


def build_terminal_public_summary(public: dict[str, Any]) -> dict[str, Any] | None:
    """Build the whitelist-only public row; None when observation is unusable.

    The normalized index is rebased to the first observed snapshot in this
    feed, not to inception, and is not flow-adjusted. Verified flow-adjusted
    return is always None until allocation-ledger baselines exist; the row
    says so in ``coverage_notes`` instead of inventing a return.
    """

    if not isinstance(public, dict):
        return None
    observed = _parse_observation(public.get("loaded_at"))
    if observed is None:
        return None
    account = public.get("account", {}) if isinstance(public.get("account"), dict) else {}
    equity = _to_float(account.get("equity"))
    points = _equity_points(public.get("history", []))
    if equity is None or not points:
        return {
            "observed_at": observed.isoformat(),
            "schema_version": 1,
            "status": "partial",
            "methodology_version": TERMINAL_METHODOLOGY_VERSION,
            "designation": "paper trading",
            "normalized_index": None,
            "verified_return_pct": None,
            "drawdown_pct": None,
            "coverage_notes": (
                "Paper trading. Account observation is incomplete: "
                "public percentages are temporarily unavailable."
            )[:2000],
        }
    baseline_observed, baseline_equity = points[0]
    normalized_index = equity / baseline_equity * 100.0
    peak = points[0][1]
    worst_drawdown = 0.0
    for _, value in points:
        if value > peak:
            peak = value
        if peak > 0:
            worst_drawdown = min(worst_drawdown, (value - peak) / peak * 100.0)
    if equity > peak:
        peak = equity
    if peak > 0:
        worst_drawdown = min(worst_drawdown, (equity - peak) / peak * 100.0)
    return {
        "observed_at": observed.isoformat(),
        "schema_version": 1,
        "status": "ready",
        "methodology_version": TERMINAL_METHODOLOGY_VERSION,
        "designation": "paper trading",
        "normalized_index": normalized_index,
        "verified_return_pct": None,
        "drawdown_pct": worst_drawdown,
        "coverage_notes": (
            "Paper trading. Normalized index rebased to the first observed "
            f"snapshot in this feed ({baseline_observed.isoformat()}); not "
            "inception and not flow-adjusted. Drawdown is equity-observation "
            "drawdown, unadjusted for funding. Verified flow-adjusted return "
            "is unavailable until allocation-ledger baselines exist."
        )[:2000],
    }


def build_terminal_member_rows(public: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Build (portfolio_latest, performance_daily) member rows, or Nones."""

    if not isinstance(public, dict):
        return None, None
    observed = _parse_observation(public.get("loaded_at"))
    if observed is None:
        return None, None
    account = public.get("account", {}) if isinstance(public.get("account"), dict) else {}
    equity = _to_float(account.get("equity"))
    summary = build_terminal_public_summary(public)
    latest = {
        "observed_at": observed.isoformat(),
        "schema_version": 1,
        "status": summary["status"] if summary else "partial",
        "methodology_version": TERMINAL_METHODOLOGY_VERSION,
        "bundle": public,
    }
    daily = {
        "scope": "account",
        "day": observed.date().isoformat(),
        "observed_at": observed.isoformat(),
        "status": summary["status"] if summary else "partial",
        "methodology_version": TERMINAL_METHODOLOGY_VERSION,
        "metrics": {
            "normalized_index": summary["normalized_index"] if summary else None,
            "verified_return_pct": None,
            "drawdown_pct": summary["drawdown_pct"] if summary else None,
            "equity": equity,
        },
    }
    return latest, daily


def _minute_bucket(observation_time: str | None) -> str | None:
    """Return UTC minute bucket ISO for idempotent 1/min history keys.

    The bucket derives from the observation time (this publishing minute), not
    the calculation time, so unchanged holdings still produce a new observation
    each minute while recalculated values keep their original timestamps.
    """

    from datetime import datetime, timezone

    if not observation_time:
        return None
    try:
        dt = datetime.fromisoformat(str(observation_time).replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    minute = dt.astimezone(timezone.utc).replace(second=0, microsecond=0)
    return minute.isoformat()


def public_risk_rows(risk: dict[str, Any] | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return (minute_row, daily_row) for risk history tables, or (None, None)."""

    from datetime import datetime, timezone

    if not risk_enabled() or not isinstance(risk, dict):
        return None, None
    calculated_at = risk.get("calculated_at")
    observed_at = risk.get("observed_at") or calculated_at
    bucket = _minute_bucket(observed_at)  # type: ignore[arg-type]
    if not bucket:
        return None, None
    portfolio_id = str(risk.get("portfolio_id") or "nova-paper-eur")
    minute_id = f"{portfolio_id}:{bucket}"
    try:
        risk_date = datetime.fromisoformat(str(observed_at).replace("Z", "+00:00")).astimezone(timezone.utc).date().isoformat()
    except Exception:
        return None, None
    daily_id = f"{portfolio_id}:{risk_date}"
    base_row = {
        "portfolio_id": portfolio_id,
        "calculated_at": calculated_at,
        "observed_at": observed_at,
        "source_portfolio_time": risk.get("source_portfolio_time"),
        "verification_time": risk.get("verification_time"),
        "history_cutoff": risk.get("history_cutoff"),
        "status": risk.get("status"),
        "var_fraction": risk.get("var_fraction"),
        "var_eur": risk.get("var_eur"),
        "worst_21_fraction": risk.get("worst_21_fraction"),
        "sample_count": risk.get("sample_count", 0),
        "lookback_start": risk.get("lookback_start"),
        "lookback_end": risk.get("lookback_end"),
        "methodology_version": risk.get("methodology_version"),
        "base_currency": risk.get("base_currency"),
        "reasons": risk.get("reasons", []),
        "coverage": risk.get("coverage", {}),
    }
    minute_row = {"id": minute_id, "minute_bucket": bucket, **base_row}
    daily_row = {"id": daily_id, "risk_date": risk_date, **base_row}
    return minute_row, daily_row


def _queue_rows(minute_row: dict[str, Any] | None, daily_row: dict[str, Any] | None) -> None:
    """Persist risk observations locally before any network write."""

    try:
        from dashboard.risk import store as risk_store

        conn = risk_store.connect()
    except Exception:
        return
    try:
        # Daily first: oldest-first drains then write the daily upsert before
        # the minute row, so daily state precedes any minute-history pruning.
        for row in (daily_row, minute_row):
            if row is not None:
                try:
                    risk_store.queue_pending(conn, str(row["id"]), row)
                except Exception:
                    pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _flush_queue(
    base_url: str, secret: str, *, budget_seconds: float = 20.0, max_rows: int = 100
) -> int:
    """Write staged observations oldest-first; returns flushed count, never raises.

    The drain runs on the collection loop, so it carries a strict time budget
    and stops at the first transport failure (network down means every later
    row would fail too). Remaining rows stay queued for the next cycle, which
    keeps a Supabase outage from stalling portfolio collection.
    """

    from time import monotonic

    try:
        from dashboard.risk import store as risk_store
    except Exception:
        return 0
    try:
        conn = risk_store.connect()
    except Exception:
        return 0
    flushed = 0
    deadline = monotonic() + max(budget_seconds, 0.0)
    try:
        # Daily before minute pruning: the daily upsert for a given date must
        # precede any minute-history cleanup, and oldest-first ordering means a
        # delayed retry can never overwrite a newer server record within one host.
        for key, payload, _attempts in risk_store.list_pending(conn, limit=max_rows):
            if monotonic() > deadline:
                break
            if len(key.split(":")[-1]) == 10:
                table = "portfolio_risk_daily"
            else:
                table = "portfolio_risk_history"
            try:
                _write(base_url, secret, table, payload, upsert=True)
                risk_store.mark_pending_result(conn, key, True)
                flushed += 1
            except Exception as exc:
                try:
                    risk_store.mark_pending_result(conn, key, False)
                except Exception:
                    pass
                if getattr(exc, "transport_error", False):
                    break
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return flushed


def _drain_pending(base_url: str, secret: str) -> int:
    """Retry queued risk observations idempotently; returns flushed count."""

    return _flush_queue(base_url, secret)


def publish_risk(risk: dict[str, Any] | None, base_url: str, secret: str) -> bool:
    """Publish minute + daily risk observations; queue on outage, never raise."""

    minute_row, daily_row = public_risk_rows(risk)
    if minute_row is None or daily_row is None:
        return False
    # Persist before any network write so an outage cannot lose the observation.
    _queue_rows(minute_row, daily_row)
    try:
        _flush_queue(base_url, secret)
    except Exception:
        pass
    return True


def _settings() -> tuple[str, str]:
    base_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    secret = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not base_url or not secret:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in the host .env.")
    return base_url, secret


def _write(base_url: str, secret: str, table: str, payload: dict[str, Any] | list[dict[str, Any]], *, upsert: bool = False, conflict: str = "id") -> None:
    endpoint = f"{base_url}/rest/v1/{table}"
    if upsert:
        endpoint += f"?on_conflict={conflict}"
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
        err = RuntimeError(f"Supabase rejected {table} ({exc.code}): {detail}")
        err.transport_error = False  # type: ignore[attr-defined]
        raise err from exc
    except URLError as exc:
        err = RuntimeError(f"Could not reach Supabase: {exc.reason}")
        err.transport_error = True  # type: ignore[attr-defined]
        raise err from exc


def publish_snapshot(snapshot: dict[str, Any] | None = None) -> int:
    """Write the latest state and idempotently upsert all observed Nova fills."""

    base_url, secret = _settings()
    source = snapshot or _load_snapshot()
    # Stage the risk observation before any network write: if the portfolio
    # write below fails, the observation is already queued for retry instead
    # of being lost.
    try:
        minute_row, daily_row = public_risk_rows(source.get("risk"))
        _queue_rows(minute_row, daily_row)
    except Exception:
        pass
    terminal_public = public_snapshot(source)
    _write(base_url, secret, "portfolio_snapshots", {"snapshot": terminal_public})
    executions = public_executions(source)
    if executions:
        _write(base_url, secret, "portfolio_trade_executions", executions, upsert=True)
    # Terminal foundations never block portfolio reporting: failures skip the
    # cycle only, and the next cycle republishes from the preserved source.
    try:
        summary = build_terminal_public_summary(terminal_public)
        if summary is not None:
            _write(base_url, secret, "terminal_public_summary", summary)
        latest, daily = build_terminal_member_rows(terminal_public)
        if latest is not None:
            _write(base_url, secret, "terminal_portfolio_latest", latest)
        if daily is not None:
            _write(base_url, secret, "terminal_performance_daily", daily, upsert=True, conflict="scope,day")
    except Exception as exc:
        print(f"Terminal publish skipped this cycle: {exc}")
    # Risk never blocks portfolio reporting: failures stay queued locally.
    try:
        _flush_queue(base_url, secret)
    except Exception:
        pass
    return len(executions)


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish Nova paper data to Supabase.")
    parser.add_argument("--watch", action="store_true", help="Keep publishing at the configured interval.")
    args = parser.parse_args()

    from core.main_executor import load_local_env

    load_local_env()
    interval = max(15, int(os.getenv("NOVA_SUPABASE_PUBLISH_SECONDS", "60")))
    while True:
        try:
            count = publish_snapshot()
        except Exception as exc:
            # Transient Supabase/network failures must not stop the watch loop;
            # the risk observation was staged before the failed write and will
            # be retried from the local queue on the next cycle.
            print(f"Publish failed, will retry next cycle: {exc}")
            if not args.watch:
                raise
            sleep(interval)
            continue
        print(f"Published portfolio snapshot and {count} Nova execution(s) to Supabase.")
        if not args.watch:
            return
        sleep(interval)


if __name__ == "__main__":
    main()
