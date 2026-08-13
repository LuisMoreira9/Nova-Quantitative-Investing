"""Cached security classifications used by the local portfolio API.

TWS is the source of truth for quantities and valuations, but an account
position callback does not contain a dependable sector or domicile field.
This module deliberately performs no broker or market-data requests.  It
joins positions to a locally maintained catalogue, which keeps API refreshes
fast even when the account contains thousands of securities.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOGUE_PATH = PROJECT_ROOT / "data" / "security_classifications.csv"

# Safe fallbacks for positions not yet present in the classification catalogue.
EXCHANGE_COUNTRIES = {
    "AEB": "Netherlands", "SBF": "France", "IBIS": "Germany", "BM": "Spain",
    "LSE": "United Kingdom", "LSEETF": "United Kingdom", "SIX": "Switzerland",
    "TSEJ": "Japan", "TSE": "Japan", "NYSE": "United States", "NASDAQ": "United States",
    "ARCA": "United States", "BATS": "United States", "IEX": "United States",
    "IBKRATS": "United States", "SMART": "United States", "IEX": "United States",
    "TGATE": "Germany", "GETTEX": "Germany", "EUDARK": "Germany", "TGHEIT": "Germany",
    "LSE": "United Kingdom", "LSEETF": "United Kingdom", "SIX": "Switzerland",
}


def _region(country: str) -> str:
    """Return the club's investable-region convention for a country."""

    if country == "United States":
        return "US"
    if country == "United Kingdom":
        return "UK"
    if country in {"China", "Hong Kong", "Taiwan", "Macau"}:
        return "Greater China"
    if country == "Japan":
        return "Japan"
    if country in {"Singapore", "South Korea", "India", "Indonesia", "Malaysia", "Thailand", "Philippines", "Vietnam", "Australia", "New Zealand"}:
        return "Asia ex Greater China & Japan"
    if country in {
        "Austria", "Belgium", "Croatia", "Cyprus", "Czech Republic", "Denmark", "Estonia", "Finland",
        "France", "Germany", "Greece", "Hungary", "Iceland", "Ireland", "Italy", "Luxembourg",
        "Netherlands", "Norway", "Poland", "Portugal", "Spain", "Sweden", "Switzerland",
    }:
        return "Europe ex UK"
    if country in {"Canada", "Brazil", "Mexico", "Chile", "Argentina", "Colombia", "Peru"}:
        return "Americas ex US"
    if country in {"Unknown", ""}:
        return "Unclassified"
    return "EMEA ex Europe & UK"


def _catalogue() -> pd.DataFrame:
    """Read the small local catalogue once per API request.

    The file may be updated independently by an offline universe-enrichment
    job.  Required columns are intentionally narrow so it can be supplied
    from any licensed reference-data source later.
    """

    columns = ["symbol", "exchange", "currency", "country", "region", "sector"]
    if not CATALOGUE_PATH.exists():
        return pd.DataFrame(columns=columns)
    frame = pd.read_csv(CATALOGUE_PATH, dtype=str).fillna("")
    for column in columns:
        if column not in frame:
            frame[column] = ""
    for column in ["symbol", "exchange", "currency"]:
        frame[column] = frame[column].str.upper().str.strip()
    return frame[columns].drop_duplicates(["symbol", "exchange", "currency"], keep="last")


def _infer_sector(symbol: str) -> str:
    """Small, explicit starter mapping for the current paper portfolio.

    Full coverage belongs in ``security_classifications.csv`` from a licensed
    reference-data export.  This fallback never makes a network request and
    is intentionally limited to well-known names used by the local test.
    """

    sectors = {
        "AAPL": "Information Technology", "MSFT": "Information Technology", "NVDA": "Information Technology",
        "META": "Communication Services", "AMZN": "Consumer Discretionary", "TSLA": "Consumer Discretionary",
        "ASML": "Information Technology", "SAP": "Information Technology", "AIR": "Industrials",
        "MC": "Consumer Discretionary", "OR": "Consumer Staples", "SAN": "Financials",
        "ALV": "Financials", "IBE": "Utilities", "SAF": "Industrials", "SIE": "Industrials",
    }
    return sectors.get(symbol, "Unclassified")


def enrich_positions(positions: pd.DataFrame) -> pd.DataFrame:
    """Add geography and sector without changing broker-owned position data."""

    if positions.empty:
        return positions.copy()

    result = positions.copy()
    for column in ["symbol", "exchange", "currency"]:
        result[column] = result.get(column, "").fillna("").astype(str).str.upper().str.strip()

    catalogue = _catalogue().rename(columns={"country": "catalogue_country", "region": "catalogue_region", "sector": "catalogue_sector"})
    result = result.merge(catalogue, on=["symbol", "exchange", "currency"], how="left")
    fallback_country = result["exchange"].map(EXCHANGE_COUNTRIES).fillna("Unknown")
    result["country"] = result["catalogue_country"].replace("", pd.NA).fillna(fallback_country)
    result["region"] = result["catalogue_region"].replace("", pd.NA).fillna(result["country"].map(_region))
    result["sector"] = result["catalogue_sector"].replace("", pd.NA).fillna(result["symbol"].map(_infer_sector))
    return result.drop(columns=["catalogue_country", "catalogue_region", "catalogue_sector"])


def exposure_by(positions: pd.DataFrame, column: str) -> pd.DataFrame:
    """Return base-currency gross exposure suitable for a pie/donut chart.

    A pie cannot represent negative values.  We therefore use absolute market
    values and label the result as *gross* exposure; short direction remains
    visible in the signed position-size chart and table.
    """

    if positions.empty or column not in positions:
        return pd.DataFrame(columns=["label", "value", "weight", "position_count", "top_positions"])
    values = pd.to_numeric(positions.get("native_market_value_base"), errors="coerce")
    fallback = pd.to_numeric(positions.get("market_value"), errors="coerce")
    values = values.where(values.notna(), fallback).abs()
    frame = pd.DataFrame({"label": positions[column].fillna("Unclassified"), "value": values}).dropna(subset=["value"])
    grouped = frame.groupby("label", as_index=False).agg(value=("value", "sum"), position_count=("value", "size"))
    top_positions: dict[str, list[dict[str, float | str]]] = {}
    working = positions.copy()
    working["_gross_value"] = values
    for label, group in working.groupby(column, dropna=False):
        top_positions[str(label) if pd.notna(label) else "Unclassified"] = [
            {"symbol": str(row.symbol), "value": float(row._gross_value)}
            for row in group.sort_values("_gross_value", ascending=False).head(3).itertuples()
        ]
    grouped["top_positions"] = grouped["label"].map(top_positions)
    grouped = grouped.sort_values("value", ascending=False)
    total = float(grouped["value"].sum())
    grouped["weight"] = grouped["value"] / total if total else 0.0
    return grouped
