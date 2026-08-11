"""External FX pricing for the execution risk gateway.

Equity prices and timestamps are carried on each strategy signal from the
shared yfinance scan.  This provider supplies only the occasional FX
conversion needed to express an order in the EUR risk budget; it never opens
an IBKR market-data subscription.
"""

from __future__ import annotations

from time import monotonic

import yfinance as yf


class YahooFxPriceProvider:
    """Small in-memory FX cache; values mean one foreign unit in base currency."""

    def __init__(self, cache_seconds: int = 60) -> None:
        self.cache_seconds = cache_seconds
        self._cache: dict[tuple[str, str], tuple[float, float]] = {}

    def get_fx_rate_to_base(self, currency: str, base_currency: str) -> float:
        currency, base_currency = currency.upper(), base_currency.upper()
        if currency == base_currency:
            return 1.0
        key = (currency, base_currency)
        cached = self._cache.get(key)
        if cached and monotonic() - cached[0] < self.cache_seconds:
            return cached[1]

        # For EUR risk and a USD listing, EURUSD=X is USD per EUR; invert it
        # to obtain EUR per USD. This generalises to the common European/Asian
        # pairs used by the club's native listings.
        ticker = f"{base_currency}{currency}=X"
        frame = yf.download(ticker, period="1d", interval="1m", progress=False, auto_adjust=True, timeout=15)
        if frame.empty:
            raise ValueError(f"Yahoo returned no FX data for {ticker}")
        close = frame["Close"].dropna()
        if close.empty:
            raise ValueError(f"Yahoo returned no usable FX close for {ticker}")
        latest = close.iloc[-1]
        # yfinance returns either a Series or a one-column DataFrame depending
        # on its version and whether it retained a MultiIndex.
        if hasattr(latest, "iloc"):
            latest = latest.iloc[0]
        quote = float(latest)
        if quote <= 0:
            raise ValueError(f"Yahoo FX quote for {ticker} must be positive")
        rate = 1 / quote
        self._cache[key] = (monotonic(), rate)
        return rate
