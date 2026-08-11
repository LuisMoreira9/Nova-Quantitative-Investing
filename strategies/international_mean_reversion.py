"""Small native-listing examples for the initial international universe.

These are deliberately conservative examples, not production investment
advice.  They use the same delayed snapshots as the existing strategy and
therefore should be used only in the TWS simulated account.
"""

from __future__ import annotations

from strategies.mean_reversion import MeanReversionStrategy


class AsmlMeanReversionStrategy(MeanReversionStrategy):
    """Trade the EUR ASML listing on Euronext Amsterdam in one-share lots."""

    def __init__(self) -> None:
        super().__init__(symbol="ASML", qty=1)


class ToyotaMeanReversionStrategy(MeanReversionStrategy):
    """Trade Toyota's native TSE listing in its standard 100-share board lot."""

    def __init__(self) -> None:
        super().__init__(symbol="TOYOTA", qty=100)


class TopixEtfMeanReversionStrategy(MeanReversionStrategy):
    """Trade the TSE-listed NEXT FUNDS TOPIX ETF (1306) in ten-unit lots."""

    def __init__(self) -> None:
        super().__init__(symbol="TOPIX_ETF", qty=10)
