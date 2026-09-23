"""Risk package: pure VaR calculation plus host-side acquisition and caching."""

from dashboard.risk.var_calculation import (
    BASE_CURRENCY,
    CONFIDENCE,
    HORIZON,
    METHODOLOGY_VERSION,
    QUANTILE_CONVENTION,
    REQUIRED_SCENARIOS,
    WORST_WINDOW,
)

__all__ = [
    "BASE_CURRENCY",
    "CONFIDENCE",
    "HORIZON",
    "METHODOLOGY_VERSION",
    "QUANTILE_CONVENTION",
    "REQUIRED_SCENARIOS",
    "WORST_WINDOW",
]
