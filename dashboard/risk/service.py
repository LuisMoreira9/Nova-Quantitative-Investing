"""Host-side VaR orchestration: background prices, recalc per 60s cycle, public payload."""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dashboard.risk import (
    BASE_CURRENCY,
    CONFIDENCE,
    HORIZON,
    METHODOLOGY_VERSION,
    QUANTILE_CONVENTION,
    REQUIRED_SCENARIOS,
    WORST_WINDOW,
)
from dashboard.risk.history import (
    align_close_series,
    align_max_available,
    compute_fx_returns,
    cutoff_today_utc,
    fetch_daily_closes,
)
from dashboard.risk.mapping import fx_ticker_for, map_position
from dashboard.risk.var_calculation import VarInputs, calculate_historical_var

STALE_VERIFICATION_SECONDS = 180
FETCH_MAX_ATTEMPTS = 3
FETCH_BACKOFF_SECONDS = (2.0, 4.0, 8.0)
# Minimum age of the last completed price fetch before a freshness refresh is
# triggered (avoids refetching every cycle over weekends when no new bars exist).
PRICE_REFRESH_MIN_AGE_SECONDS = 6 * 3600
LOGICAL_PORTFOLIO_ID = "nova-paper-eur"
# Bump when mapping/weighting logic changes so stale cached results are dropped.
RISK_CODE_VERSION = "2"
# One-time bootstrap exception (env NOVA_RISK_BOOTSTRAP_EXCEPTION=1): while 252
# common scenarios are unavailable, use the maximum available window down to
# this floor, labeled as a distinct methodology. Not a plan amendment: once 252
# scenarios exist the output converges back to the plan methodology exactly.
BOOTSTRAP_MIN_SCENARIOS = 120
BOOTSTRAP_VERSION_SUFFIX = "-bootstrap"


def _bootstrap_enabled() -> bool:
    return os.getenv("NOVA_RISK_BOOTSTRAP_EXCEPTION", "0").strip().lower() in {
        "1", "true", "yes", "on", "confirm",
    }


class _RiskUnavailable(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _snapshot_hash(positions: list[dict], cash: dict, equity: Any, base: str) -> str:
    key_parts = []
    for p in sorted(positions, key=lambda r: (str(r.get("symbol")), str(r.get("exchange")), str(r.get("currency")))):
        key_parts.append(
            f"{p.get('symbol')}|{p.get('exchange')}|{p.get('currency')}|{p.get('security_type')}|"
            f"{p.get('quantity')}|{p.get('current_price')}|{p.get('market_value')}|{p.get('con_id', 0)}"
        )
    key_parts.append(f"cash={json.dumps(cash, sort_keys=True, default=str)}")
    key_parts.append(f"equity={equity}|base={base}")
    return hashlib.sha256("\n".join(key_parts).encode("utf-8")).hexdigest()[:32]


def _dataset_version(
    tickers: list[str],
    scenario_dates: list[tuple[str, str]],
    security_returns: dict[str, list[float]],
    fx_returns: dict[str, list[float]],
    closes: dict[str, list[tuple[str, float]]],
) -> str:
    """Content-derived version: any price/return correction invalidates reuse.

    Hashing only ticker names and dates misses in-window price revisions
    (e.g. adjusted-close corrections), which would silently reuse a stale VaR.
    """

    h = hashlib.sha256()
    h.update("|".join(sorted(t.upper() for t in tickers)).encode("utf-8"))
    h.update(("|".join(f"{s}>{e}" for s, e in scenario_dates)).encode("utf-8"))
    for ticker in sorted(security_returns):
        h.update(ticker.encode("utf-8"))
        h.update((",".join(repr(v) for v in security_returns[ticker])).encode("utf-8"))
    for ccy in sorted(fx_returns):
        h.update(ccy.encode("utf-8"))
        h.update((",".join(repr(v) for v in fx_returns[ccy])).encode("utf-8"))
    for ticker in sorted(closes):
        rows = closes[ticker]
        if rows:
            h.update(f"{ticker}:{rows[-1][0]}={rows[-1][1]!r}".encode("utf-8"))
    return h.hexdigest()[:16]


def _collection_or_unavailable(snapshot: dict[str, Any]) -> tuple[list[dict], dict]:
    """Return verified (positions, cash) or raise _RiskUnavailable.

    An explicitly verified empty portfolio (both keys present but empty) is a
    valid zero-risk account. Missing keys, nulls, or wrong types mean the
    broker download failed and must never be reported as zero risk.
    """

    if "positions" not in snapshot or "currency_cash" not in snapshot:
        raise _RiskUnavailable("incomplete_broker_state")
    positions, cash = snapshot["positions"], snapshot["currency_cash"]
    if positions is None or cash is None:
        raise _RiskUnavailable("incomplete_broker_state")
    if hasattr(positions, "to_dict"):
        positions = positions.to_dict(orient="records")
    if not isinstance(positions, list) or not isinstance(cash, dict):
        raise _RiskUnavailable("incomplete_broker_state")
    return list(positions), dict(cash)


def _expected_history_end_iso() -> str:
    """Latest completed daily session: yesterday UTC (today is always excluded)."""

    return (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()


# History older than this many calendar days is downgraded from ready to stale:
# up to ~3 days are explainable by weekends (Friday data on Monday), beyond
# that at least one full trading session is missing.
HISTORY_STALE_DAYS = 4


def _history_age_days(lookback_end: str | None) -> int | None:
    try:
        if not lookback_end:
            return None
        end = datetime.fromisoformat(str(lookback_end)).date()
        today = datetime.now(timezone.utc).date()
        return (today - end).days
    except Exception:
        return None


def _apply_history_staleness(risk: dict[str, Any]) -> dict[str, Any]:
    """Downgrade ready results built on stale market data to explicit stale."""

    if risk.get("status") not in ("ready", "stale"):
        return risk
    age = _history_age_days(risk.get("lookback_end"))
    coverage = dict(risk.get("coverage") or {})
    if age is not None:
        coverage["history_age_days"] = age
        risk["coverage"] = coverage
    if age is not None and age > HISTORY_STALE_DAYS:
        risk["status"] = "stale"
        reasons = list(risk.get("reasons") or [])
        if "stale_history" not in reasons:
            reasons.append("stale_history")
        risk["reasons"] = reasons
    return risk


def _parse_equity(account: dict) -> float | None:
    try:
        value = float(account.get("equity"))
    except Exception:
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return value


def _verification_age_seconds(verification_time: str | None, source_loaded_at: str | None) -> float:
    for candidate in (verification_time, source_loaded_at):
        if not candidate:
            continue
        try:
            dt = datetime.fromisoformat(str(candidate).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt).total_seconds()
        except Exception:
            continue
    return float("inf")


def build_unavailable_risk(
    *,
    source_loaded_at: str | None,
    verification_time: str | None,
    cutoff: str | None,
    observed_at: str | None = None,
    sample_count: int = 0,
    lookback_start: str | None = None,
    lookback_end: str | None = None,
    reasons: tuple[str, ...] = ("missing_mapping",),
    coverage: dict | None = None,
    last_valid: dict | None = None,
) -> dict[str, Any]:
    now = observed_at or _now_iso()
    return {
        "status": "unavailable",
        "portfolio_id": LOGICAL_PORTFOLIO_ID,
        "calculated_at": now,
        "observed_at": now,
        "source_portfolio_time": source_loaded_at,
        "verification_time": verification_time,
        "history_cutoff": cutoff or cutoff_today_utc(),
        "confidence": CONFIDENCE,
        "horizon": HORIZON,
        "sample_count": sample_count,
        "lookback_start": lookback_start,
        "lookback_end": lookback_end,
        "quantile_convention": QUANTILE_CONVENTION,
        "base_currency": BASE_CURRENCY,
        "methodology_version": METHODOLOGY_VERSION,
        "var_fraction": None,
        "var_eur": None,
        "worst_21_fraction": None,
        "coverage": coverage or {"required": REQUIRED_SCENARIOS, "available": sample_count, "reasons": list(reasons)},
        "reasons": list(reasons),
        "last_valid": last_valid,
    }


def build_ready_risk(
    *,
    source_loaded_at: str | None,
    verification_time: str | None,
    cutoff: str,
    observed_at: str | None = None,
    calculated_at: str | None = None,
    sample_count: int,
    lookback_start: str | None,
    lookback_end: str | None,
    var_fraction: float,
    var_eur: float,
    worst_21: float | None,
    covered_assets: int,
    required_assets: int,
    excluded_intervals: int = 0,
    dataset_version: str | None = None,
    refresh_pending: bool = False,
    methodology_version: str = METHODOLOGY_VERSION,
    extra_reasons: tuple[str, ...] = (),
    last_valid: dict | None = None,
) -> dict[str, Any]:
    # Backend stale downgrade: source older than 3 min stays visible but flagged.
    # The website applies the same rule independently with its own clock.
    status = "ready"
    if _verification_age_seconds(verification_time, source_loaded_at) > STALE_VERIFICATION_SECONDS:
        status = "stale"
    now = observed_at or _now_iso()
    return {
        "status": status,
        "portfolio_id": LOGICAL_PORTFOLIO_ID,
        "calculated_at": calculated_at or now,
        "observed_at": now,
        "source_portfolio_time": source_loaded_at,
        "verification_time": verification_time,
        "history_cutoff": cutoff,
        "confidence": CONFIDENCE,
        "horizon": HORIZON,
        "sample_count": sample_count,
        "lookback_start": lookback_start,
        "lookback_end": lookback_end,
        "quantile_convention": QUANTILE_CONVENTION,
        "base_currency": BASE_CURRENCY,
        "methodology_version": methodology_version,
        "var_fraction": var_fraction,
        "var_eur": var_eur,
        "worst_21_fraction": worst_21,
        "worst_window": WORST_WINDOW,
        "coverage": {
            "required": REQUIRED_SCENARIOS,
            "available": sample_count,
            "assets_covered": covered_assets,
            "assets_required": required_assets,
            "excluded_intervals": excluded_intervals,
            "dataset_version": dataset_version,
            "refresh_pending": refresh_pending,
            "reasons": list(extra_reasons),
        },
        "reasons": list(extra_reasons),
        "last_valid": last_valid,
    }


def _tws_price_multiplier(exchange: str, currency: str) -> float:
    """Quote-unit factor for TWS native prices (LSE reports pence as GBP)."""

    if currency.upper() == "GBP" and exchange.upper() in {"LSE", "LSEETF"}:
        return 0.01
    return 1.0


class RiskService:
    """Stateful per-process orchestrator; safe to share across publisher cycles."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._last_holdings_hash: str | None = None
        self._last_risk: dict[str, Any] | None = None
        self._last_dataset_version: str | None = None
        self._last_history_end: str | None = None
        self._last_needed_tickers: list[str] = []
        self._last_checked_at: str | None = None
        self._last_valid: dict[str, Any] | None = None
        self._target_hash: str | None = None
        self._price_cache: dict[str, list[tuple[str, float]]] = {}
        self._fetching = False
        self._last_fetch_complete_at = 0.0
        self._db_path = db_path
        self._load_persisted_state()

    # -- persistence (best-effort; never breaks calculation) -----------------

    def _db(self):  # sqlite3.Connection | None
        try:
            from dashboard.risk import store as risk_store

            if self._db_path is not None:
                return risk_store.connect(self._db_path)
            return risk_store.connect()
        except Exception:
            return None

    def _load_persisted_state(self) -> None:
        conn = self._db()
        if conn is None:
            return
        try:
            from dashboard.risk import store as risk_store

            self._price_cache = {k.upper(): v for k, v in risk_store.load_all_prices(conn).items()}
            latest_valid = risk_store.load_last_valid(conn)
            if latest_valid is None:
                latest_valid = risk_store.load_latest_valid(conn)
            if latest_valid is not None:
                self._last_valid = latest_valid
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _persist_prices(self, data: dict[str, list[tuple[str, float]]]) -> None:
        conn = self._db()
        if conn is None:
            return
        try:
            from dashboard.risk import store as risk_store

            risk_store.replace_price_window(conn, data)
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _save_audit(self, snapshot_hash: str, entry: dict[str, Any]) -> None:
        conn = self._db()
        if conn is None:
            return
        try:
            from dashboard.risk import store as risk_store

            risk_store.save_audit(conn, snapshot_hash, entry)
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # -- inputs ---------------------------------------------------------------

    def snapshot_inputs(self, snapshot: dict[str, Any]) -> tuple[str, list[dict], dict, float | None, str]:
        positions, cash = _collection_or_unavailable(snapshot)
        account = snapshot.get("account", {}) or {}
        base = str(snapshot.get("base_currency", BASE_CURRENCY)).upper()
        equity = _parse_equity(account)
        digest = _snapshot_hash(list(positions), dict(cash), account.get("equity"), base)
        return digest, list(positions), dict(cash), equity, base

    def ensure_prices_async(self, tickers: list[str], target_hash: str) -> None:
        with self._lock:
            if self._fetching or not tickers:
                return
            self._target_hash = target_hash
            self._fetching = True
        thread = threading.Thread(
            target=self._fetch_worker, args=(sorted(set(tickers)), target_hash), daemon=True
        )
        thread.start()

    def _fetch_worker(self, tickers: list[str], target_hash: str) -> None:
        try:
            data: dict[str, list[tuple[str, float]]] = {}
            fx_tickers = [t for t in tickers if t.endswith("=X")]
            eq_tickers = [t for t in tickers if not t.endswith("=X")]
            for group in (eq_tickers, fx_tickers):
                if not group:
                    continue
                last_exc: Exception | None = None
                for attempt in range(FETCH_MAX_ATTEMPTS):
                    try:
                        chunk = fetch_daily_closes(group, period="2y")
                        # Discard if target moved on.
                        with self._lock:
                            if self._target_hash != target_hash:
                                return
                        data.update(chunk)
                        last_exc = None
                        break
                    except Exception as exc:  # bounded retries with backoff
                        last_exc = exc
                        time.sleep(FETCH_BACKOFF_SECONDS[min(attempt, len(FETCH_BACKOFF_SECONDS) - 1)])
                if last_exc is not None:
                    return
            with self._lock:
                if self._target_hash != target_hash:
                    return  # stale result discarded
                # Refresh complete window, never append partial adjustments.
                for ticker, rows in data.items():
                    self._price_cache[ticker.upper()] = rows
                self._last_fetch_complete_at = time.time()
            self._persist_prices(data)
        finally:
            with self._lock:
                self._fetching = False

    # -- verified valuations ---------------------------------------------------

    def _verified_security_eur_mv(
        self, pos: dict[str, Any], closes: dict[str, list[tuple[str, float]]]
    ) -> float:
        """Signed EUR market value from native qty x price and verified FX.

        TWS ``market_value`` is not trusted as EUR: foreign listings must be
        converted through the same verified FX window used for returns, and a
        missing FX series is an explicit unavailable (never an FX=1 default).
        """

        ccy = str(pos.get("currency") or "").upper()
        exchange = str(pos.get("exchange") or "").upper()
        if not ccy:
            raise _RiskUnavailable("incomplete_broker_state")
        try:
            qty = float(pos.get("quantity", 0) or 0)
        except Exception:
            raise _RiskUnavailable("incomplete_broker_state")
        if qty == 0:
            return 0.0
        try:
            price = float(pos.get("current_price"))
        except Exception:
            raise _RiskUnavailable("incomplete_broker_state")
        if not math.isfinite(price) or price <= 0:
            raise _RiskUnavailable("incomplete_broker_state")
        native_mv = qty * price * _tws_price_multiplier(exchange, ccy)
        if not math.isfinite(native_mv):
            raise _RiskUnavailable("incomplete_broker_state")
        if ccy == BASE_CURRENCY:
            return native_mv
        ticker = fx_ticker_for(ccy, BASE_CURRENCY)
        rows = closes.get((ticker or "").upper(), []) if ticker else []
        if not rows:
            raise _RiskUnavailable("unavailable_fx")
        quote = rows[-1][1]  # Yahoo quote: foreign units per EUR
        if not math.isfinite(quote) or quote <= 0:
            raise _RiskUnavailable("unavailable_fx")
        eur_mv = native_mv / quote
        if not math.isfinite(eur_mv):
            raise _RiskUnavailable("unavailable_fx")
        return eur_mv

    # -- main entry -------------------------------------------------------------

    def compute_from_snapshot(
        self,
        snapshot: dict[str, Any],
        verification_time: str | None = None,
    ) -> dict[str, Any]:
        """Synchronous recalc from cached prices; triggers async fetch when cold."""

        source_loaded = snapshot.get("loaded_at")
        if hasattr(source_loaded, "isoformat"):
            source_loaded = source_loaded.isoformat()
        source_loaded = str(source_loaded) if source_loaded else None
        base = str(snapshot.get("base_currency", BASE_CURRENCY)).upper()
        account = snapshot.get("account", {}) or {}
        equity_probe = _parse_equity(account)
        verification_time = verification_time or source_loaded or _now_iso()
        observed_at = _now_iso()
        cutoff = cutoff_today_utc()
        try:
            digest, positions, cash, equity, base = self.snapshot_inputs(snapshot)
        except _RiskUnavailable as exc:
            digest = _snapshot_hash([], {}, account.get("equity"), base)
            return self._finish_unavailable(
                digest, equity_probe, base, source_loaded, verification_time,
                observed_at, cutoff, (exc.reason,),
            )
        cash_upper = {str(k).upper(): v for k, v in cash.items()}

        if equity is None:
            risk = build_unavailable_risk(
                source_loaded_at=source_loaded,
                verification_time=verification_time,
                cutoff=cutoff,
                observed_at=observed_at,
                reasons=("nonpositive_equity",),
                last_valid=self._last_valid,
            )
            self._save_audit(digest, self._audit_entry(digest, None, risk, equity, base))
            return self._remember_holdings(digest, risk, None, None, [])

        if base != BASE_CURRENCY:
            risk = build_unavailable_risk(
                source_loaded_at=source_loaded,
                verification_time=verification_time,
                cutoff=cutoff,
                observed_at=observed_at,
                reasons=("incomplete_broker_state",),
                last_valid=self._last_valid,
            )
            self._save_audit(digest, self._audit_entry(digest, None, risk, equity, base))
            return self._remember_holdings(digest, risk, None, None, [])

        # Map every held security; any gap -> whole-account unavailable.
        mappings: dict[int, Any] = {}
        needed_yf: list[str] = []
        for idx, pos in enumerate(positions):
            try:
                qty = float(pos.get("quantity", 0) or 0)
            except Exception:
                return self._finish_unavailable(
                    digest, equity, base, source_loaded, verification_time, observed_at, cutoff,
                    ("incomplete_broker_state",),
                )
            if qty == 0:
                continue
            mapping, reason = map_position(pos)
            if mapping is None:
                return self._finish_unavailable(
                    digest, equity, base, source_loaded, verification_time, observed_at, cutoff,
                    (reason or "missing_mapping",),
                    coverage={
                        "required": REQUIRED_SCENARIOS,
                        "available": 0,
                        "assets_required": len(positions),
                        "assets_covered": idx,
                        "reasons": [reason or "missing_mapping"],
                    },
                )
            mappings[idx] = mapping
            needed_yf.append(mapping.yfinance_ticker)
            fx_ticker = fx_ticker_for(mapping.fx_currency, BASE_CURRENCY)
            if fx_ticker:
                needed_yf.append(fx_ticker)
        for ccy, bal in cash_upper.items():
            if ccy == BASE_CURRENCY:
                continue
            try:
                if float(bal or 0) == 0.0:
                    continue
            except Exception:
                pass
            fx_ticker = fx_ticker_for(str(ccy), BASE_CURRENCY)
            if fx_ticker:
                needed_yf.append(fx_ticker)
        needed_yf = sorted(set(needed_yf))

        with self._lock:
            cached = {t: list(self._price_cache.get(t.upper(), [])) for t in needed_yf}
        missing = [t for t, rows in cached.items() if not rows]

        # Valid zero-risk fast path: no securities and EUR-only (or zero) cash
        # needs no price history at all.
        if not mappings:
            foreign_nonzero = False
            for ccy, bal in cash_upper.items():
                if ccy == BASE_CURRENCY:
                    continue
                try:
                    nonzero = float(bal or 0) != 0.0
                except Exception:
                    return self._finish_unavailable(
                        digest, equity, base, source_loaded, verification_time, observed_at, cutoff,
                        ("incomplete_broker_state",),
                    )
                if nonzero:
                    foreign_nonzero = True
            if not foreign_nonzero:
                risk = build_ready_risk(
                    source_loaded_at=source_loaded,
                    verification_time=verification_time,
                    cutoff=cutoff,
                    observed_at=observed_at,
                    calculated_at=observed_at,
                    sample_count=0,
                    lookback_start=None,
                    lookback_end=None,
                    var_fraction=0.0,
                    var_eur=0.0,
                    worst_21=0.0,
                    covered_assets=0,
                    required_assets=0,
                    dataset_version="cash-only",
                    last_valid=self._last_valid,
                )
                self._note_valid(risk)
                risk["last_valid"] = self._last_valid
                self._save_audit(digest, self._audit_entry(digest, "cash-only", risk, equity, base))
                return self._remember_holdings(digest, risk, "cash-only", None, needed_yf)

        if missing:
            self.ensure_prices_async(needed_yf, digest)
            # A missing FX series is its own explicit failure (never a default
            # FX of 1); a missing equity series is a history gap.
            missing_equity = [t for t in missing if not t.upper().endswith("=X")]
            reason: str = "insufficient_history" if missing_equity else "unavailable_fx"
            return self._finish_unavailable(
                digest, equity, base, source_loaded, verification_time, observed_at, cutoff,
                (reason,),
                coverage={
                    "required": REQUIRED_SCENARIOS,
                    "available": 0,
                    "assets_required": len(mappings),
                    "assets_covered": 0,
                    "reasons": [reason],
                },
                remember=False,
            )

        # Build close series keyed by yfinance ticker + FX in EUR-per-foreign.
        closes: dict[str, list[tuple[str, float]]] = {}
        for t in needed_yf:
            rows = cached.get(t.upper(), []) or cached.get(t, [])
            if rows:
                closes[t.upper()] = rows
        aligned, reason = align_close_series(closes, REQUIRED_SCENARIOS)
        bootstrap = False
        methodology = METHODOLOGY_VERSION
        if aligned is None and _bootstrap_enabled():
            # One-time exception: maximum available window down to the floor,
            # honestly labeled. Converges to the plan methodology once 252
            # common scenarios exist.
            boot, _ = align_max_available(closes, BOOTSTRAP_MIN_SCENARIOS)
            if boot is not None:
                aligned, reason, bootstrap = boot, None, True
                methodology = METHODOLOGY_VERSION + BOOTSTRAP_VERSION_SUFFIX
        if aligned is None:
            self.ensure_prices_async(needed_yf, digest)
            return self._finish_unavailable(
                digest, equity, base, source_loaded, verification_time, observed_at, cutoff,
                (reason or "insufficient_history",),
                remember=False,
            )
        # Persist the coherent window so restarts keep prices and the audit trail.
        self._persist_prices(closes)

        refresh_pending = False
        if aligned.end_date < _expected_history_end_iso() and not self._fetch_recent():
            self.ensure_prices_async(needed_yf, digest)
            refresh_pending = True

        # Split security vs FX closes; invert FX quotes to EUR-per-foreign.
        # cached FX rows are Yahoo quotes (foreign per EUR, e.g. EURUSD=X);
        # convert to EUR per foreign via 1/quote for compute_fx_returns.
        fx_closes_eur: dict[str, list[tuple[str, float]]] = {}
        sua_currencies = {m.fx_currency.upper() for m in mappings.values()} | set(cash_upper)
        for ccy in sua_currencies:
            if ccy == BASE_CURRENCY:
                continue
            ticker = fx_ticker_for(ccy, BASE_CURRENCY)
            rows = closes.get((ticker or "").upper(), [])
            if not rows and float(cash_upper.get(ccy, 0) or 0) != 0.0:
                return self._finish_unavailable(
                    digest, equity, base, source_loaded, verification_time, observed_at, cutoff,
                    ("unavailable_fx",),
                    remember=False,
                )
            if rows:
                fx_closes_eur[ccy] = [(d, 1.0 / c) for d, c in rows if c]
        fx_returns, fx_reason = compute_fx_returns(fx_closes_eur, list(aligned.scenario_dates))
        if fx_returns is None:
            return self._finish_unavailable(
                digest, equity, base, source_loaded, verification_time, observed_at, cutoff,
                (fx_reason or "unavailable_fx",),
                remember=False,
            )

        # Content-derived dataset version: any in-window price correction (not
        # just new dates) must invalidate reuse of the cached calculation.
        dataset_version = _dataset_version(
            needed_yf, list(aligned.scenario_dates),
            aligned.security_returns, fx_returns, closes,
        )

        # Reuse: same holdings AND same return dataset -> refresh observation
        # metadata (verification/observation times) while keeping the original
        # calculation time and outputs.
        with self._lock:
            reusable = (
                self._last_holdings_hash == digest
                and self._last_dataset_version == dataset_version
                and self._last_risk is not None
            )
            cached_risk = dict(self._last_risk) if reusable and self._last_risk else None
        if cached_risk is not None:
            refreshed = self._refresh_observation(
                cached_risk, source_loaded, verification_time, observed_at
            )
            if refresh_pending and isinstance(refreshed.get("coverage"), dict):
                refreshed["coverage"] = {**refreshed["coverage"], "refresh_pending": True}
            with self._lock:
                self._last_risk = dict(refreshed)
                self._last_history_end = aligned.end_date
                self._last_needed_tickers = needed_yf
                self._last_checked_at = observed_at
            out = dict(refreshed)
            out["last_checked_at"] = observed_at
            return out

        # Weights: verified signed EUR market value / equity (never raw TWS
        # market_value for foreign listings, never a default FX of 1).
        sec_weights: dict[str, float] = {}
        sec_returns: dict[str, list[float]] = {}
        try:
            for idx, mapping in mappings.items():
                eur_mv = self._verified_security_eur_mv(positions[idx], closes)
                key = f"{mapping.yfinance_ticker}#{idx}"
                sec_weights[key] = eur_mv / float(equity)
                local = aligned.security_returns.get(mapping.yfinance_ticker.upper(), [])
                fx_c = mapping.fx_currency.upper()
                if fx_c == BASE_CURRENCY:
                    sec_returns[key] = list(local)
                else:
                    fx_r = fx_returns.get(fx_c, [])
                    sec_returns[key] = [(1 + l) * (1 + f) - 1 for l, f in zip(local, fx_r)]
        except _RiskUnavailable as exc:
            return self._finish_unavailable(
                digest, equity, base, source_loaded, verification_time, observed_at, cutoff,
                (exc.reason,),
                remember=False,
            )

        cash_weights: dict[str, float] = {}
        for ccy, bal in cash_upper.items():
            try:
                native = float(bal or 0)
            except Exception:
                return self._finish_unavailable(
                    digest, equity, base, source_loaded, verification_time, observed_at, cutoff,
                    ("incomplete_broker_state",),
                )
            if ccy == BASE_CURRENCY:
                cash_weights["EUR"] = cash_weights.get("EUR", 0.0) + native / float(equity)
            else:
                # Convert native cash to EUR via latest verified FX (EUR per foreign).
                ticker = fx_ticker_for(str(ccy), BASE_CURRENCY)
                rows = closes.get((ticker or "").upper(), [])
                if not rows:
                    if native != 0.0:
                        return self._finish_unavailable(
                            digest, equity, base, source_loaded, verification_time, observed_at, cutoff,
                            ("unavailable_fx",),
                            remember=False,
                        )
                    continue
                latest_quote = rows[-1][1]
                eur_per_foreign = 1.0 / latest_quote if latest_quote else None
                if eur_per_foreign is None or not math.isfinite(eur_per_foreign):
                    return self._finish_unavailable(
                        digest, equity, base, source_loaded, verification_time, observed_at, cutoff,
                        ("unavailable_fx",),
                        remember=False,
                    )
                cash_weights[ccy] = cash_weights.get(ccy, 0.0) + (native * eur_per_foreign) / float(equity)

        result = calculate_historical_var(
            VarInputs(
                equity=float(equity),
                security_weights=sec_weights,
                security_eur_returns=sec_returns,
                cash_weights=cash_weights,
                fx_returns=fx_returns,
            )
        )
        if result.status != "ready":
            return self._finish_unavailable(
                digest, equity, base, source_loaded, verification_time, observed_at, cutoff,
                tuple(result.reasons) or ("missing_history",),
                sample_count=result.sample_count,
                remember=False,
            )

        assert result.var_fraction is not None and result.var_eur is not None
        risk = build_ready_risk(
            source_loaded_at=source_loaded,
            verification_time=verification_time,
            cutoff=cutoff,
            observed_at=observed_at,
            calculated_at=observed_at,
            sample_count=result.sample_count,
            lookback_start=aligned.start_date,
            lookback_end=aligned.end_date,
            var_fraction=float(result.var_fraction),
            var_eur=float(result.var_eur),
            worst_21=float(result.worst_21_fraction) if result.worst_21_fraction is not None else None,
            covered_assets=len(mappings),
            required_assets=len(mappings),
            excluded_intervals=aligned.excluded_intervals,
            dataset_version=dataset_version,
            refresh_pending=refresh_pending,
            methodology_version=methodology,
            extra_reasons=("bootstrap_exception",) if bootstrap else (),
            last_valid=self._last_valid,
        )
        risk = _apply_history_staleness(risk)
        self._note_valid(risk)
        risk["last_valid"] = self._last_valid
        self._save_audit(
            digest,
            self._audit_entry(
                digest, dataset_version, risk, equity, base,
                extra={"security_weights": sec_weights, "cash_weights": cash_weights},
            ),
        )
        return self._remember_holdings(digest, risk, dataset_version, aligned.end_date, needed_yf)

    # -- reuse + bookkeeping -----------------------------------------------------

    def _refresh_observation(
        self,
        risk: dict[str, Any],
        source_loaded_at: str | None,
        verification_time: str | None,
        observed_at: str,
    ) -> dict[str, Any]:
        """Refresh per-observation metadata on a reused calculation.

        Calculation outputs (VaR, sample, lookback, calculated_at) are kept;
        verification/source/observation times advance so fresh broker snapshots
        are not misreported as stale and each publishing minute yields a new
        observation key.
        """

        out = dict(risk)
        out["source_portfolio_time"] = source_loaded_at
        out["verification_time"] = verification_time
        out["observed_at"] = observed_at
        if out.get("status") in ("ready", "stale"):
            if _verification_age_seconds(verification_time, source_loaded_at) > STALE_VERIFICATION_SECONDS:
                out["status"] = "stale"
            else:
                out["status"] = "ready"
        # Market data keeps ageing while holdings are unchanged; re-evaluate
        # the history downgrade on every reuse, not just at calculation time.
        return _apply_history_staleness(out)

    def _fetch_recent(self) -> bool:
        with self._lock:
            return (time.time() - self._last_fetch_complete_at) < PRICE_REFRESH_MIN_AGE_SECONDS

    def _note_valid(self, risk: dict[str, Any]) -> None:
        with self._lock:
            self._last_valid = {
                "var_fraction": risk["var_fraction"],
                "var_eur": risk["var_eur"],
                "calculated_at": risk["calculated_at"],
                "source_portfolio_time": risk["source_portfolio_time"],
            }
            snapshot = dict(self._last_valid)
        conn = self._db()
        if conn is None:
            return
        try:
            from dashboard.risk import store as risk_store

            risk_store.save_last_valid(conn, snapshot)
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _audit_entry(
        self,
        digest: str,
        dataset_version: str | None,
        risk: dict[str, Any],
        equity: float | None,
        base: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "status": risk.get("status"),
            "snapshot_hash": digest,
            "dataset_version": dataset_version,
            "code_version": RISK_CODE_VERSION,
            "equity": equity,
            "base_currency": base,
            "sample_count": risk.get("sample_count"),
            "lookback_start": risk.get("lookback_start"),
            "lookback_end": risk.get("lookback_end"),
            "var_fraction": risk.get("var_fraction"),
            "var_eur": risk.get("var_eur"),
            "worst_21_fraction": risk.get("worst_21_fraction"),
            "reasons": risk.get("reasons"),
            "calculated_at": risk.get("calculated_at"),
            "observed_at": risk.get("observed_at"),
            "last_valid": self._last_valid,
        }
        if extra:
            entry.update(extra)
        return entry

    def _finish_unavailable(
        self,
        digest: str,
        equity: float | None,
        base: str,
        source_loaded: str | None,
        verification_time: str | None,
        observed_at: str,
        cutoff: str,
        reasons: tuple[str, ...],
        coverage: dict | None = None,
        sample_count: int = 0,
        remember: bool = False,
    ) -> dict[str, Any]:
        # Unavailable results are recomputed (not reused) so that mapping,
        # registry, or broker-state fixes take effect on the next cycle without
        # a restart. Only ready results populate the reuse cache.
        risk = build_unavailable_risk(
            source_loaded_at=source_loaded,
            verification_time=verification_time,
            cutoff=cutoff,
            observed_at=observed_at,
            sample_count=sample_count,
            reasons=reasons,
            coverage=coverage,
            last_valid=self._last_valid,
        )
        self._save_audit(digest, self._audit_entry(digest, None, risk, equity, base))
        if remember:
            return self._remember_holdings(digest, risk, None, None, [])
        with self._lock:
            self._last_checked_at = observed_at
        out = dict(risk)
        out["last_checked_at"] = observed_at
        return out

    def _remember_holdings(
        self,
        digest: str,
        risk: dict[str, Any],
        dataset_version: str | None,
        history_end: str | None,
        needed_tickers: list[str],
    ) -> dict[str, Any]:
        with self._lock:
            self._last_holdings_hash = digest
            self._last_risk = dict(risk)
            self._last_dataset_version = dataset_version
            self._last_history_end = history_end
            self._last_needed_tickers = list(needed_tickers)
            self._last_checked_at = str(risk.get("observed_at") or _now_iso())
            out = dict(risk)
            out["last_checked_at"] = self._last_checked_at
            return out


_SERVICE = RiskService()


def get_service() -> RiskService:
    return _SERVICE
