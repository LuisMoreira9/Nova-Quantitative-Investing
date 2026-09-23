"""Pure historical-simulation VaR calculation with no network or database I/O."""

from __future__ import annotations

import math
from dataclasses import dataclass


METHODOLOGY_VERSION = "hs-1d-99-n252-v1"
CONFIDENCE = 0.99
HORIZON = "1d"
QUANTILE_CONVENTION = "nearest-rank-ceil-0.99"
BASE_CURRENCY = "EUR"
REQUIRED_SCENARIOS = 252
WORST_WINDOW = 21

# Stable public reason codes (never include internal errors or broker IDs).
REASON_NONPOSITIVE_EQUITY = "nonpositive_equity"
REASON_NO_SCENARIOS = "insufficient_history"
REASON_MISSING_RETURN = "missing_history"
REASON_INVALID_RETURN = "invalid_price"
REASON_INVALID_EXPOSURE = "incomplete_broker_state"
REASON_UNSUPPORTED_INSTRUMENT = "unsupported_instrument"
REASON_MISSING_FX = "unavailable_fx"
REASON_MISSING_MAPPING = "missing_mapping"


@dataclass(frozen=True)
class VarInputs:
    """Aligned inputs for one whole-account calculation."""

    equity: float
    # Signed MV in EUR / equity per security (shorts negative).
    security_weights: dict[str, float]
    # Aligned EUR returns per security: id -> list of N scenario returns.
    security_eur_returns: dict[str, list[float]]
    # Signed EUR value / equity per cash currency (EUR cash may be present with zero returns).
    cash_weights: dict[str, float]
    # Aligned FX returns (EUR per unit foreign currency) per currency, EUR omitted or zeros.
    fx_returns: dict[str, list[float]]


@dataclass(frozen=True)
class VarResult:
    status: str  # "ready" or "unavailable"
    var_fraction: float | None
    var_eur: float | None
    worst_21_fraction: float | None
    sample_count: int
    reasons: tuple[str, ...]


def _is_finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def quantile_index(sample_count: int, confidence: float = CONFIDENCE) -> int:
    """Return 0-based index for ceil(confidence*N) nearest-rank convention."""
    if sample_count <= 0:
        raise ValueError("sample_count must be positive")
    rank = math.ceil(confidence * sample_count)  # 1-indexed
    rank = min(max(rank, 1), sample_count)
    return rank - 1


def eur_return(local_return: float, fx_return: float) -> float:
    """Combine local adjusted return and FX return into EUR terms."""
    return (1.0 + local_return) * (1.0 + fx_return) - 1.0


def calculate_historical_var(inputs: VarInputs) -> VarResult:
    """Compute 1-day 99% historical VaR from aligned exposures and returns."""
    equity = inputs.equity
    if not _is_finite_number(equity) or float(equity) <= 0:
        return VarResult("unavailable", None, None, None, 0, (REASON_NONPOSITIVE_EQUITY,))

    sec_ids = list(inputs.security_weights.keys())
    # Unsupported instruments are rejected upstream; a sentinel weight path
    # keeps the pure layer closed to partial estimates too.
    for weight in list(inputs.security_weights.values()) + list(inputs.cash_weights.values()):
        if not _is_finite_number(weight):
            return VarResult("unavailable", None, None, None, 0, (REASON_INVALID_EXPOSURE,))

    # Determine N from security returns; cash FX must align when present.
    counts: set[int] = set()
    for sid in sec_ids:
        series = inputs.security_eur_returns.get(sid)
        if series is None:
            return VarResult("unavailable", None, None, None, 0, (REASON_MISSING_RETURN,))
        counts.add(len(series))
    for ccy, series in inputs.fx_returns.items():
        if ccy.upper() == BASE_CURRENCY:
            continue
        if series is None:
            return VarResult("unavailable", None, None, None, 0, (REASON_MISSING_FX,))
        counts.add(len(series))
    # Cash weights without an FX series: EUR cash is valid zero; foreign cash missing FX is unavailable.
    for ccy in inputs.cash_weights:
        if ccy.upper() == BASE_CURRENCY:
            continue
        if ccy not in inputs.fx_returns:
            # Zero foreign cash weight needs no FX series; nonzero does.
            if float(inputs.cash_weights[ccy]) != 0.0:
                return VarResult("unavailable", None, None, None, 0, (REASON_MISSING_FX,))

    if not counts:
        # No securities and no FX series: pure EUR cash portfolio -> valid zero.
        # Any nonzero foreign cash without FX was already rejected above.
        return VarResult("ready", 0.0, 0.0, 0.0, 0, ())

    if len(counts) != 1:
        return VarResult("unavailable", None, None, None, 0, (REASON_MISSING_RETURN,))
    n = next(iter(counts))
    if n == 0:
        return VarResult("unavailable", None, None, None, 0, (REASON_NO_SCENARIOS,))

    # Validate every return is finite; None/NaN/inf never becomes zero.
    for sid in sec_ids:
        for ret in inputs.security_eur_returns[sid]:
            if not _is_finite_number(ret):
                return VarResult("unavailable", None, None, None, 0, (REASON_INVALID_RETURN,))
    for ccy, series in inputs.fx_returns.items():
        if ccy.upper() == BASE_CURRENCY:
            continue
        for ret in series:
            if not _is_finite_number(ret):
                return VarResult("unavailable", None, None, None, 0, (REASON_INVALID_RETURN,))

    sec_weights = {sid: float(inputs.security_weights[sid]) for sid in sec_ids}
    cash_weights = {ccy.upper(): float(w) for ccy, w in inputs.cash_weights.items()}
    fx_series = {ccy.upper(): inputs.fx_returns[ccy] for ccy in inputs.fx_returns if ccy.upper() != BASE_CURRENCY}

    portfolio_returns: list[float] = []
    for i in range(n):
        total = 0.0
        for sid in sec_ids:
            total += sec_weights[sid] * float(inputs.security_eur_returns[sid][i])
        for ccy, weight in cash_weights.items():
            if ccy == BASE_CURRENCY or weight == 0.0:
                continue
            total += weight * float(fx_series[ccy][i])
        if not math.isfinite(total):
            return VarResult("unavailable", None, None, None, 0, (REASON_INVALID_RETURN,))
        portfolio_returns.append(total)

    losses = sorted(-r for r in portfolio_returns)
    selected = losses[quantile_index(n)]
    var_fraction = max(0.0, selected)
    var_eur = var_fraction * float(equity)

    tail = portfolio_returns[-min(WORST_WINDOW, n):]
    worst_21 = max(-r for r in tail) if tail else None

    return VarResult("ready", var_fraction, var_eur, worst_21, n, ())
