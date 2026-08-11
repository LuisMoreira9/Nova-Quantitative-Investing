"""Reviewed STOXX Europe 600 instrument registry.

The index membership source and the execution mapping are deliberately kept
separate.  A constituent must have an ISIN plus a reviewed Yahoo and native
IBKR listing before ``approved`` is set to true.  This avoids treating an
ambiguous company name or foreign ADR as an orderable European security.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from core.instruments import InstrumentSpec


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "config" / "stoxx_europe_600_registry.csv"
APPROVED_SYMBOLS_PATH = ROOT / "data" / "stoxx_europe_600_approved_symbols.txt"


@dataclass(frozen=True)
class StoxxInstrument:
    instrument_id: str
    isin: str
    name: str
    country: str
    yfinance_ticker: str
    ibkr_symbol: str
    primary_exchange: str
    currency: str
    min_quantity: int
    approved: bool

    def to_instrument_spec(self) -> InstrumentSpec:
        return InstrumentSpec(
            self.instrument_id,
            self.ibkr_symbol,
            "SMART",
            self.primary_exchange,
            self.currency,
            self.min_quantity,
            f"{self.name} ({self.primary_exchange}; ISIN {self.isin})",
            self.isin,
        )


def load_registry(path: Path = REGISTRY_PATH) -> list[StoxxInstrument]:
    """Load and validate the human-reviewed registry."""

    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        required = {
            "instrument_id", "isin", "name", "country", "yfinance_ticker",
            "ibkr_symbol", "primary_exchange", "currency", "min_quantity", "approved",
        }
        if not reader.fieldnames or required - set(reader.fieldnames):
            raise ValueError("STOXX registry has missing required columns")
        rows = [
            StoxxInstrument(
                instrument_id=row["instrument_id"].upper().strip(),
                isin=row["isin"].upper().strip(),
                name=row["name"].strip(),
                country=row["country"].upper().strip(),
                yfinance_ticker=row["yfinance_ticker"].upper().strip(),
                ibkr_symbol=row["ibkr_symbol"].upper().strip(),
                primary_exchange=row["primary_exchange"].upper().strip(),
                currency=row["currency"].upper().strip(),
                min_quantity=int(row["min_quantity"]),
                approved=row["approved"].strip().lower() == "true",
            )
            for row in reader
        ]
    ids = [row.instrument_id for row in rows]
    isins = [row.isin for row in rows]
    if len(ids) != len(set(ids)) or len(isins) != len(set(isins)):
        raise ValueError("STOXX registry contains duplicate instrument IDs or ISINs")
    return rows


def approved_universe() -> dict[str, StoxxInstrument]:
    """Return the only STOXX rows eligible for yfinance and paper execution."""

    return {row.instrument_id: row for row in load_registry() if row.approved}


def instrument_for_stoxx(instrument_id: str) -> InstrumentSpec | None:
    row = approved_universe().get(instrument_id.upper().strip())
    return row.to_instrument_spec() if row else None


def write_approved_symbols(path: Path = APPROVED_SYMBOLS_PATH) -> Path:
    """Materialise approved IDs for the risk gateway; never approves new rows."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(sorted(approved_universe())) + "\n", encoding="utf-8")
    return path
