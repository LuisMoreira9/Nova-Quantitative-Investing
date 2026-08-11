"""Example student strategy for Nova Quant Club.

Student strategy files should focus only on research logic:
    - read the incoming market bar;
    - update any local indicators/state;
    - return either ``None`` or a plain trade signal.

They should not know about Alpaca API keys, order submission, or risk limits.
Those responsibilities belong to ``core/main_executor.py`` and
``core/risk_gateway.py``.

The required interface for Stage 1 is:
    ``on_bar(bar) -> dict | None``

The returned signal must use this shape:
    ``{"symbol": "AAPL", "action": "BUY", "qty": 10}``
"""

from __future__ import annotations

from collections import deque
from statistics import mean
from typing import Any

from strategies.base import BaseLiveStrategy


class MeanReversionStrategy(BaseLiveStrategy):
    """Tiny mean-reversion example using the executor's ``on_bar`` contract.

    This is intentionally simple so new members can understand the flow. It is
    not meant to be a profitable strategy. The goal is to demonstrate how a
    strategy stores prices, calculates an indicator, and emits a standardized
    signal that the risk gateway can review.
    """

    def __init__(self, symbol: str = "AAPL", window: int = 20, threshold: float = 0.02, qty: int = 10) -> None:
        # The executor uses ``strategy.symbol`` to decide which market bars this
        # strategy should receive.
        self.symbol = symbol.upper()

        # ``window`` is the number of recent closes used for the moving average.
        # A deque automatically drops the oldest close once it reaches maxlen.
        self.window = window
        self.prices: deque[float] = deque(maxlen=window)

        # ``threshold`` is the distance from the moving average needed before
        # the strategy emits a BUY or SELL signal.
        self.threshold = threshold

        # ``qty`` is proposed order size. The risk gateway can still reject it
        # if it violates config/risk_profile.json.
        self.qty = qty

    def on_bar(self, bar: Any) -> dict[str, Any] | None:
        """Process one Alpaca bar and maybe return a trade signal.

        ``bar`` is supplied by Alpaca's stock data stream. In normal use it has
        fields such as ``symbol``, ``open``, ``high``, ``low``, ``close``, and
        ``volume``. This strategy only needs ``bar.close``.
        """

        close = float(bar.close)
        self.prices.append(close)

        # Wait until the moving-average window is full. Returning None means
        # "do nothing"; the executor will not call the risk gateway.
        if len(self.prices) < self.window:
            return None

        moving_average = mean(self.prices)
        lower_band = moving_average * (1 - self.threshold)
        upper_band = moving_average * (1 + self.threshold)

        # If price is meaningfully below the recent average, propose a paper BUY.
        if close < lower_band:
            return {"symbol": self.symbol, "action": "BUY", "qty": self.qty}

        # If price is meaningfully above the recent average, propose a paper SELL.
        if close > upper_band:
            return {"symbol": self.symbol, "action": "SELL", "qty": self.qty}

        return None
