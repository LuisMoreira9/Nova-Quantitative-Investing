"""Instrument mapping for VaR: native listing -> yfinance ticker, no ADR substitution."""

from __future__ import annotations

from dataclasses import dataclass


SUPPORTED_SEC_TYPES = {"STK", "ETF"}

# Explicit ticker exceptions beyond STOXX + US universe (extend, never guess ADRs).
# Key: (SYMBOL, EXCHANGE, CURRENCY) upper -> yfinance ticker upper.
TICKER_EXCEPTIONS: dict[tuple[str, str, str], str] = {
    ("7203", "TSEJ", "JPY"): "7203.T",
    ("1306", "TSEJ", "JPY"): "1306.T",
}

# LSE yfinance tickers (*.L) quote in GBp (pence). Returns are scale-invariant,
# but keep the multiplier explicit for audit and price-level checks.
GBP_PENCE_MULTIPLIER = 0.01


@dataclass(frozen=True)
class AssetMapping:
    yfinance_ticker: str
    quote_currency: str  # e.g. USD, EUR, JPY, GBP (GBp normalised to GBP)
    price_multiplier: float
    fx_currency: str  # currency whose EUR/FX series applies (GBP for GBp)
    instrument_id: str = ""
    isin: str = ""


def _stoxx_lookup(symbol: str, exchange: str, currency: str):
    try:
        from core.stoxx_europe_600 import approved_universe
    except Exception:
        return None
    for row in approved_universe().values():
        if (
            row.ibkr_symbol.upper() == symbol
            and row.primary_exchange.upper() == exchange
            and row.currency.upper() == currency
        ):
            return row
    return None


def _us_hard_lookup(symbol: str, exchange: str, currency: str):
    try:
        from core.instruments import INSTRUMENTS
    except Exception:
        return None
    for spec in INSTRUMENTS.values():
        if (
            spec.symbol.upper() == symbol
            and (not spec.primary_exchange or spec.primary_exchange.upper() == exchange or spec.exchange.upper() == exchange)
            and spec.currency.upper() == currency
        ):
            return spec
    return None


def map_position(position: dict) -> tuple[AssetMapping | None, str | None]:
    """Map one broker position row to a yfinance source.

    Returns (mapping, reason). reason is a stable public code when unavailable.
    Never substitutes an ADR or another company on ticker resemblance.
    """
    symbol = str(position.get("symbol") or "").upper().strip()
    exchange = str(position.get("exchange") or "").upper().strip()
    currency = str(position.get("currency") or "").upper().strip()
    sec_type = str(position.get("security_type") or position.get("secType") or "STK").upper().strip()
    if not symbol or not currency:
        return None, "missing_mapping"
    if sec_type and sec_type not in SUPPORTED_SEC_TYPES:
        return None, "unsupported_instrument"
    # Derivatives requiring repricing are explicitly unsupported in v1.
    if sec_type in {"OPT", "FUT", "FOP", "WAR", "CFD", "BOND", "CMDTY"}:
        return None, "unsupported_instrument"

    # 1. Reviewed STOXX registry (native European listings).
    stoxx_row = _stoxx_lookup(symbol, exchange, currency)
    if stoxx_row is not None:
        return (
            AssetMapping(
                yfinance_ticker=stoxx_row.yfinance_ticker.upper(),
                quote_currency=stoxx_row.currency.upper(),
                price_multiplier=1.0,
                fx_currency=stoxx_row.currency.upper(),
                instrument_id=stoxx_row.instrument_id,
                isin=stoxx_row.isin,
            ),
            None,
        )

    # 2. Explicit exceptions (e.g. Tokyo numeric listings).
    exc = TICKER_EXCEPTIONS.get((symbol, exchange, currency))
    if exc is not None:
        return AssetMapping(exc, currency, 1.0, currency), None

    # 3. Hard-coded native contracts (includes TOYOTA/TOPIX numeric symbols).
    hard = _us_hard_lookup(symbol, exchange, currency)
    if hard is not None:
        # Derive yfinance ticker: STOXX already handled; US SMART -> symbol;
        # Tokyo numeric handled via exceptions above; fallback to exception map.
        yf = symbol
        if currency == "JPY" and symbol.isdigit():
            yf = f"{symbol}.T"
        return AssetMapping(yf, currency, 1.0, currency, hard.instrument_id, hard.isin), None

    # 4. US universe: native USD listing only, never an ADR guess.
    # Require USD currency and a US venue; ticker must be plain US symbol.
    us_venues = {"SMART", "NASDAQ", "NYSE", "ARCA", "BATS", "IEX", "IBKRATS"}
    if currency == "USD" and (exchange in us_venues or exchange == ""):
        import re

        if re.fullmatch(r"[A-Z]{1,5}(?:-[A-Z])?", symbol):
            return AssetMapping(symbol, "USD", 1.0, "USD"), None

    # 5. LSE pence handling: explicit GBp quote units.
    if exchange in {"LSE", "LSEETF"} and currency in {"GBP", "GBX", "GBp".upper()}:
        # Require an explicit exception; never guess the YFinance suffix.
        return None, "missing_mapping"

    return None, "missing_mapping"


def fx_ticker_for(currency: str, base: str = "EUR") -> str | None:
    """Return yfinance FX ticker for EUR value per unit foreign currency."""
    cur, base = currency.upper(), base.upper()
    if cur == base:
        return None
    # GBp/GBX are pence of GBP: same FX series as GBP.
    if cur in {"GBX", "GBP", "GBp".upper()}:
        cur = "GBP"
    return f"{base}{cur}=X"


def quote_multiplier(yfinance_ticker: str) -> float:
    """Return price multiplier to normalised quote currency (GBp -> GBP)."""
    if yfinance_ticker.upper().endswith(".L"):
        return GBP_PENCE_MULTIPLIER
    return 1.0
