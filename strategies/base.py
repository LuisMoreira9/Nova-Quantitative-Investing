"""Shared contract for Nova's live paper-trading strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseLiveStrategy(ABC):
    """A strategy that reacts to broker bars without submitting orders.

    The executor owns broker credentials and order submission.  A strategy only
    exposes its configured Nova instrument ID as ``symbol`` and returns a proposed signal, or
    ``None``, from :meth:`on_bar`.
    """

    symbol: str

    @abstractmethod
    def on_bar(self, bar: Any) -> dict[str, Any] | None:
        """Return ``None`` or ``{'symbol', 'action', 'qty'}`` for one bar."""
