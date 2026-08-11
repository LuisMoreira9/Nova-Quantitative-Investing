"""Nova's explicitly approved IBKR instruments and native-currency contracts.

Instrument IDs are the values strategies emit as ``symbol``.  Keeping the
exchange, listing currency, and board-lot information here prevents a plain
ticker such as ``ASML`` or ``7203`` from being routed to the wrong market.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class InstrumentSpec:
    """An orderable exchange listing, not a generic company name."""

    instrument_id: str
    symbol: str
    exchange: str
    primary_exchange: str
    currency: str
    min_quantity: int
    description: str
    isin: str = ""


INSTRUMENTS: dict[str, InstrumentSpec] = {
    "AAPL": InstrumentSpec("AAPL", "AAPL", "SMART", "NASDAQ", "USD", 1, "Apple Inc. (NASDAQ)"),
    "MSFT": InstrumentSpec("MSFT", "MSFT", "SMART", "NASDAQ", "USD", 1, "Microsoft Corp. (NASDAQ)"),
    "NVDA": InstrumentSpec("NVDA", "NVDA", "SMART", "NASDAQ", "USD", 1, "NVIDIA Corp. (NASDAQ)"),
    "AMZN": InstrumentSpec("AMZN", "AMZN", "SMART", "NASDAQ", "USD", 1, "Amazon.com Inc. (NASDAQ)"),
    "GOOGL": InstrumentSpec("GOOGL", "GOOGL", "SMART", "NASDAQ", "USD", 1, "Alphabet Inc. Class A (NASDAQ)"),
    "META": InstrumentSpec("META", "META", "SMART", "NASDAQ", "USD", 1, "Meta Platforms Inc. (NASDAQ)"),
    "OR": InstrumentSpec("OR", "OR", "SMART", "SBF", "EUR", 1, "L'Oréal (Euronext Paris)"),
    "SAN": InstrumentSpec("SAN", "SAN", "SMART", "BM", "EUR", 1, "Banco Santander (BME)"),
    "TOYOTA": InstrumentSpec("TOYOTA", "7203", "SMART", "TSEJ", "JPY", 100, "Toyota Motor Corp. (Tokyo Stock Exchange)"),
    "TOPIX_ETF": InstrumentSpec("TOPIX_ETF", "1306", "SMART", "TSEJ", "JPY", 10, "NEXT FUNDS TOPIX ETF (Tokyo Stock Exchange)"),
}


def instrument_for(instrument_id: str) -> InstrumentSpec:
    """Return a configured instrument or fail before any broker request."""

    normalized = instrument_id.upper().strip()
    # Prefer a reviewed STOXX registry row over an older hard-coded entry so
    # its ISIN remains part of the IBKR contract-resolution request.
    from core.stoxx_europe_600 import instrument_for_stoxx

    stoxx_instrument = instrument_for_stoxx(normalized)
    if stoxx_instrument is not None:
        return stoxx_instrument
    try:
        return INSTRUMENTS[normalized]
    except KeyError as exc:
        # European rows are kept in a reviewable CSV registry rather than
        # hard-coded into this module.  Only rows marked approved can resolve.
        # The yfinance S&P 500 scanner writes an approved universe file which
        # the risk gateway checks separately.  A generic SMART contract lets
        # TWS resolve a selected U.S. constituent without hard-coding 500
        # exchange listings here.
        if re.fullmatch(r"[A-Z]{1,5}(?:-[A-Z])?", normalized):
            return InstrumentSpec(normalized, normalized, "SMART", "", "USD", 1, "U.S. equity candidate")
        raise ValueError(f"unsupported instrument: {normalized}") from exc
