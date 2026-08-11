"""Guarded, delayed-data momentum-reversal pilot for liquid S&P 500 names.

This is an execution-pipeline demonstration, not investment advice.  It runs
only against the small liquid pilot configured below; querying every S&P 500
constituent once a minute would exceed the intended delayed-data scope.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from typing import Any

from strategies.base import BaseLiveStrategy


class _MomentumReversalStrategy(BaseLiveStrategy):
    """Buy a short-term downside reversal and sell its opposite reversal once."""

    def __init__(self, symbol: str, *, qty: int = 1, threshold: float = 0.001) -> None:
        self.symbol = symbol
        self.qty = qty
        self.threshold = threshold
        self.prices: deque[float] = deque(maxlen=6)
        self.position_open = False
        self.pending_action: str | None = None
        self.cooldown_until: datetime | None = None
        self.trades_today = 0

    def on_bar(self, bar: Any) -> dict[str, Any] | None:
        self.prices.append(float(bar.close))
        now = bar.timestamp
        if len(self.prices) < self.prices.maxlen or self.pending_action or self.trades_today >= 2:
            return None
        if self.cooldown_until and now < self.cooldown_until:
            return None

        # Compare the older three-snapshot momentum with the latest three.
        older = self.prices[2] / self.prices[0] - 1
        recent = self.prices[-1] / self.prices[-3] - 1
        if not self.position_open and older <= -self.threshold and recent >= self.threshold:
            self.pending_action = "BUY"
            return {"symbol": self.symbol, "action": "BUY", "qty": self.qty}
        if self.position_open and older >= self.threshold and recent <= -self.threshold:
            self.pending_action = "SELL"
            return {"symbol": self.symbol, "action": "SELL", "qty": self.qty}
        return None

    def on_order_result(self, action: str, filled: bool, timestamp: datetime) -> None:
        """Advance local state only after the executor confirms a fill."""

        self.pending_action = None
        if filled:
            self.position_open = action == "BUY"
            self.trades_today += 1
            # Prevent an unchanged delayed observation from re-submitting an
            # order every minute. A symbol may make at most one decision per
            # ten-minute delayed-data window.
            self.cooldown_until = timestamp + timedelta(minutes=10)


class Sp500AaplMomentumReversal(_MomentumReversalStrategy):
    def __init__(self) -> None:
        super().__init__("AAPL")


class Sp500MsftMomentumReversal(_MomentumReversalStrategy):
    def __init__(self) -> None:
        super().__init__("MSFT")


class Sp500NvdaMomentumReversal(_MomentumReversalStrategy):
    def __init__(self) -> None:
        super().__init__("NVDA")


class Sp500AmznMomentumReversal(_MomentumReversalStrategy):
    def __init__(self) -> None:
        super().__init__("AMZN")


class Sp500GooglMomentumReversal(_MomentumReversalStrategy):
    def __init__(self) -> None:
        super().__init__("GOOGL")


class Sp500MetaMomentumReversal(_MomentumReversalStrategy):
    def __init__(self) -> None:
        super().__init__("META")
