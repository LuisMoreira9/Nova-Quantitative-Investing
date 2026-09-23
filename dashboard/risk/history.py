"""Historical price acquisition and 252-scenario alignment for VaR.

Network layer (yfinance) is isolated here; pure alignment helpers are offline-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone


REQUIRED_SCENARIOS = 252
FETCH_PERIODS = ("2y", "1y", "730d")


@dataclass(frozen=True)
class AlignedReturns:
    scenario_dates: list[tuple[str, str]]  # (start_iso, end_iso) per scenario, oldest first
    security_returns: dict[str, list[float]]  # yf ticker -> N EUR-ready local returns (pre-FX combine upstream)
    fx_returns: dict[str, list[float]]  # currency -> N FX returns (EUR per unit foreign)
    start_date: str
    end_date: str
    sample_count: int
    excluded_intervals: int


def _parse_date(value: str) -> date:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).date() if "T" in value else date.fromisoformat(value)


def align_close_series(
    closes: dict[str, list[tuple[str, float]]],
    required: int = REQUIRED_SCENARIOS,
) -> tuple[AlignedReturns | None, str | None]:

    """Align multiple date->close series into common consecutive scenarios.

    Rules:
    - No forward-fill, no zero substitution.
    - An interval (prev,end) is kept only if every series has bars on BOTH dates
      AND those dates are consecutive observations in each series' own calendar
      (prevents comparing a multi-session return with a one-session return).
    - Returns oldest-first, up to `required` latest valid scenarios.
    """
    # Build per-ticker date->close and ordered date lists, dropping invalid prices.
    series_dates: dict[str, list[str]] = {}
    series_close: dict[str, dict[str, float]] = {}
    for ticker, rows in closes.items():
        clean: dict[str, float] = {}
        for d, c in rows:
            try:
                iso = _parse_date(d).isoformat()
            except Exception:
                continue
            if c is None or not isinstance(c, (int, float)):
                continue
            import math

            if not math.isfinite(float(c)) or float(c) <= 0:
                continue
            # Last bar wins on duplicate dates.
            clean[iso] = float(c)
        series_close[ticker] = clean
        series_dates[ticker] = sorted(clean.keys())

    if not closes:
        return None, "insufficient_history"
    # Common dates where EVERY series has a bar.
    common = set.intersection(*(set(v) for v in series_dates.values())) if series_dates else set()
    common_sorted = sorted(common)
    if len(common_sorted) < 2:
        return None, "insufficient_history"

    # Keep intervals where prev,end are consecutive in EVERY series' own calendar.
    valid_intervals: list[tuple[str, str]] = []
    for prev, end in zip(common_sorted[:-1], common_sorted[1:]):
        ok = True
        for ticker in closes:
            own = series_dates[ticker]
            try:
                i_prev = own.index(prev)
                i_end = own.index(end)
            except ValueError:
                ok = False
                break
            if i_end != i_prev + 1:
                ok = False  # multi-session gap for this ticker -> exclude
                break
        if ok:
            valid_intervals.append((prev, end))

    excluded = (len(common_sorted) - 1) - len(valid_intervals)
    if len(valid_intervals) < required:
        return None, "insufficient_history"
    chosen = valid_intervals[-required:]

    return _returns_for_intervals(closes, series_close, chosen, excluded), None


def _returns_for_intervals(
    closes: dict[str, list[tuple[str, float]]],
    series_close: dict[str, dict[str, float]],
    chosen: list[tuple[str, str]],
    excluded: int,
) -> AlignedReturns:
    sec_returns: dict[str, list[float]] = {}
    for ticker in closes:
        rets = []
        for prev, end in chosen:
            prev_c = series_close[ticker][prev]
            end_c = series_close[ticker][end]
            rets.append(end_c / prev_c - 1.0)
        sec_returns[ticker] = rets
    return AlignedReturns(
        scenario_dates=chosen,
        security_returns=sec_returns,
        fx_returns={},
        start_date=chosen[0][0],
        end_date=chosen[-1][1],
        sample_count=len(chosen),
        excluded_intervals=excluded,
    )


def align_max_available(
    closes: dict[str, list[tuple[str, float]]],
    minimum: int,
) -> tuple[AlignedReturns | None, str | None]:
    """Align using ALL valid common scenarios, requiring at least `minimum`.

    One-time bootstrap path: identical interval rules to align_close_series
    (common dates, consecutive in every series' own calendar, no fills), but
    keeps the maximum available window instead of the latest `required`
    slice, so the reported sample count is honest.
    """

    base, reason = align_close_series(closes, minimum)
    if base is None:
        return None, reason
    # Same deterministic rules; take every valid interval, not the tail slice.
    full, _ = _all_valid_intervals(closes)
    if full is None or len(full[0]) < minimum:
        return base, None
    valid_intervals, series_close, excluded = full
    return _returns_for_intervals(closes, series_close, valid_intervals, excluded), None


def _all_valid_intervals(
    closes: dict[str, list[tuple[str, float]]],
) -> tuple[tuple[list[tuple[str, str]], dict[str, dict[str, float]], int] | None, str | None]:
    """Shared interval core: valid common scenarios under the no-fill rules."""

    series_dates: dict[str, list[str]] = {}
    series_close: dict[str, dict[str, float]] = {}
    for ticker, rows in closes.items():
        clean: dict[str, float] = {}
        for d, c in rows:
            try:
                iso = _parse_date(d).isoformat()
            except Exception:
                continue
            if c is None or not isinstance(c, (int, float)):
                continue
            import math

            if not math.isfinite(float(c)) or float(c) <= 0:
                continue
            clean[iso] = float(c)
        series_close[ticker] = clean
        series_dates[ticker] = sorted(clean.keys())

    if not closes:
        return None, "insufficient_history"
    common = set.intersection(*(set(v) for v in series_dates.values())) if series_dates else set()
    common_sorted = sorted(common)
    if len(common_sorted) < 2:
        return None, "insufficient_history"

    valid_intervals: list[tuple[str, str]] = []
    for prev, end in zip(common_sorted[:-1], common_sorted[1:]):
        ok = True
        for ticker in closes:
            own = series_dates[ticker]
            try:
                i_prev = own.index(prev)
                i_end = own.index(end)
            except ValueError:
                ok = False
                break
            if i_end != i_prev + 1:
                ok = False
                break
        if ok:
            valid_intervals.append((prev, end))

    excluded = (len(common_sorted) - 1) - len(valid_intervals)
    return (valid_intervals, series_close, excluded), None


def compute_fx_returns(
    fx_closes: dict[str, list[tuple[str, float]]],
    scenario_dates: list[tuple[str, str]],
) -> tuple[dict[str, list[float]] | None, str | None]:
    """Compute FX returns (EUR per unit foreign) for already-chosen scenario dates.

    fx_closes values must already be EUR-per-foreign (inverted from Yahoo quote).
    Same no-fill rule: any missing/invalid bar for a scenario makes FX unavailable
    rather than substituting zero.
    """
    out: dict[str, list[float]] = {}
    for ccy, rows in fx_closes.items():
        if ccy.upper() == "EUR":
            continue
        m: dict[str, float] = {}
        for d, c in rows:
            try:
                iso = _parse_date(d).isoformat()
            except Exception:
                return None, "unavailable_fx"
            import math

            if c is None or not isinstance(c, (int, float)) or not math.isfinite(float(c)) or float(c) <= 0:
                return None, "invalid_price"
            m[iso] = float(c)
        rets = []
        for prev, end in scenario_dates:
            if prev not in m or end not in m:
                return None, "unavailable_fx"
            rets.append(m[end] / m[prev] - 1.0)
        out[ccy.upper()] = rets
    return out, None


def cutoff_today_utc() -> str:
    """Return current UTC date ISO; callers must exclude it (incomplete sessions)."""
    return datetime.now(timezone.utc).date().isoformat()


def fetch_daily_closes(tickers: list[str], period: str = "2y") -> dict[str, list[tuple[str, float]]]:
    """Fetch adjusted daily closes via yfinance, excluding the current UTC date."""
    import yfinance as yf

    if not tickers:
        return {}
    frame = yf.download(
        tickers if len(tickers) > 1 else tickers[0],
        period=period,
        interval="1d",
        auto_adjust=True,
        progress=False,
        timeout=30,
    )
    if frame is None or getattr(frame, "empty", True):
        raise ValueError("Yahoo returned no daily data")
    today = cutoff_today_utc()
    out: dict[str, list[tuple[str, float]]] = {}
    # Normalise single vs multi-ticker frames.
    try:
        import pandas as pd  # type: ignore
    except Exception:
        pd = None  # type: ignore
    for ticker in tickers:
        rows: list[tuple[str, float]] = []
        try:
            if len(tickers) == 1:
                closes = frame["Close"] if "Close" in frame else frame
                # Flatten one-column frame/series.
                if hasattr(closes, "columns"):
                    closes = closes.iloc[:, 0]
                idx = frame.index
                import pandas as _pd  # noqa: F401
                for ts, val in zip(idx, closes):
                    d = _pd.Timestamp(ts).date().isoformat()
                    if d >= today:
                        continue
                    try:
                        rows.append((d, float(val)))
                    except Exception:
                        continue
            else:
                col = None
                # yfinance multi-ticker: columns MultiIndex (Price, Ticker) or (Ticker, Price).
                if hasattr(frame.columns, "nlevels") and frame.columns.nlevels == 2:
                    for key in [(("Close", ticker)), ((ticker, "Close"))]:
                        try:
                            col = frame[key]
                            break
                        except Exception:
                            continue
                    if col is None:
                        # Fallback: case-insensitive ticker match.
                        for lvl0 in frame.columns.get_level_values(0).unique():
                            for lvl1 in frame.columns.get_level_values(1).unique():
                                if str(lvl1).upper() == ticker.upper() and str(lvl0).lower() == "close":
                                    col = frame[(lvl0, lvl1)]
                                    break
                    if col is None:
                        continue
                else:
                    col = frame["Close"] if "Close" in frame else None
                    if col is None:
                        continue
                for ts, val in zip(frame.index, col):
                    import pandas as _pd2

                    d = _pd2.Timestamp(ts).date().isoformat()
                    if d >= today:
                        continue
                    try:
                        import math

                        f = float(val)
                        if math.isfinite(f):
                            rows.append((d, f))
                    except Exception:
                        continue
        except Exception:
            continue
        out[ticker] = rows
    # Refresh the complete window: callers must replace, not append to, cached series.
    return out
