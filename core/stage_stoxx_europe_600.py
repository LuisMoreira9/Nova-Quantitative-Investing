"""Stage, but never enable, the current official STOXX Europe 600 membership.

This tool separates three things that must not be conflated for paper trading:
official index membership, possible IBKR contracts, and an approved execution
mapping. It writes data-only local CSV reports and never edits the approved
registry or submits an order.
"""

from __future__ import annotations

import argparse
import csv
from io import StringIO
import logging
from pathlib import Path
import time

import pandas as pd
import requests
import yfinance as yf

from core.ibkr_adapter import IBKRClient
from core.main_executor import ibkr_connection_settings, load_local_env


LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://stoxx.com/index/sxxp/?components=true"
MEMBERSHIP_PATH = ROOT / "data" / "stoxx_europe_600_membership.csv"
DISCOVERY_PATH = ROOT / "data" / "stoxx_europe_600_contract_discovery.csv"


def fetch_membership() -> pd.DataFrame:
    """Fetch the public official table of names, countries, and weights."""

    response = requests.get(SOURCE_URL, headers={"User-Agent": "Mozilla/5.0 (Nova student-club research)"}, timeout=30)
    response.raise_for_status()
    tables = pd.read_html(StringIO(response.text))
    table = next(
        frame
        for frame in tables
        if {"Company", "Country", "Weight (%)"}.issubset({str(column) for column in frame.columns})
    )
    members = table[["Company", "Country", "Weight (%)"]].copy()
    members.columns = ["company", "country", "weight_pct"]
    members["company"] = members["company"].astype(str).str.strip()
    members["country"] = members["country"].astype(str).str.strip()
    members["weight_pct"] = pd.to_numeric(members["weight_pct"], errors="coerce")
    members = members.dropna(subset=["company", "country"]).drop_duplicates(subset=["company", "country"])
    if len(members) != 600:
        raise RuntimeError(f"official STOXX page returned {len(members)} members; expected exactly 600")
    return members.sort_values(["country", "company"], kind="stable").reset_index(drop=True)


def write_membership() -> pd.DataFrame:
    members = fetch_membership()
    MEMBERSHIP_PATH.parent.mkdir(parents=True, exist_ok=True)
    members.to_csv(MEMBERSHIP_PATH, index=False)
    LOGGER.info("Wrote %s official STOXX Europe 600 membership rows to %s", len(members), MEMBERSHIP_PATH)
    return members


def yahoo_equity_symbols(company: str) -> list[dict[str, str]]:
    """Find possible Yahoo equity symbols without treating any as approved."""

    try:
        quotes = yf.Search(company, max_results=5).quotes
    except Exception as exc:
        LOGGER.warning("Yahoo search failed for %s: %s", company, exc)
        return []
    return [
        {
            "symbol": str(quote.get("symbol", "")).upper(),
            "name": str(quote.get("longname") or quote.get("shortname") or ""),
            "exchange": str(quote.get("exchDisp") or quote.get("exchange") or ""),
        }
        for quote in quotes
        if quote.get("quoteType") == "EQUITY" and quote.get("symbol")
    ]


def discover_contracts(members: pd.DataFrame, broker: IBKRClient, pause_seconds: float) -> None:
    """Create a review report of possible IBKR stock contracts by company name.

    The report deliberately contains every plausible match and marks none of
    them approved. A human must choose the native listing and separately supply
    a Yahoo ticker/ISIN before adding a row to the execution registry.
    """

    DISCOVERY_PATH.parent.mkdir(parents=True, exist_ok=True)
    fields = ["company", "country", "weight_pct", "yahoo_ticker", "yahoo_name", "yahoo_exchange", "match_count", "con_id", "symbol", "primary_exchange", "exchange", "currency", "status"]
    with DISCOVERY_PATH.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for number, row in enumerate(members.itertuples(index=False), start=1):
            try:
                yahoo_matches = yahoo_equity_symbols(row.company)
                if not yahoo_matches:
                    writer.writerow({"company": row.company, "country": row.country, "weight_pct": row.weight_pct, "match_count": 0, "status": "no Yahoo equity candidate"})
                    continue
                total_contracts = 0
                for yahoo_match in yahoo_matches:
                    # Yahoo suffixes identify venues (for example ASML.AS),
                    # whereas IBKR matching starts from the root ticker.
                    root_symbol = yahoo_match["symbol"].split(".", maxsplit=1)[0]
                    matches = broker.search_stock_contracts(root_symbol)
                    total_contracts += len(matches)
                    if not matches:
                        writer.writerow({"company": row.company, "country": row.country, "weight_pct": row.weight_pct, "yahoo_ticker": yahoo_match["symbol"], "yahoo_name": yahoo_match["name"], "yahoo_exchange": yahoo_match["exchange"], "match_count": 0, "status": "no IBKR stock match"})
                        continue
                    for contract in matches:
                        writer.writerow(
                            {
                                "company": row.company,
                                "country": row.country,
                                "weight_pct": row.weight_pct,
                                "yahoo_ticker": yahoo_match["symbol"],
                                "yahoo_name": yahoo_match["name"],
                                "yahoo_exchange": yahoo_match["exchange"],
                                "match_count": len(matches),
                                "con_id": contract.conId,
                                "symbol": contract.symbol,
                                "primary_exchange": contract.primaryExchange,
                                "exchange": contract.exchange,
                                "currency": contract.currency,
                                "status": "candidate only; not approved",
                            }
                        )
                LOGGER.info("%s/600: discovered %s IBKR stock candidate(s) for %s", number, total_contracts, row.company)
            except Exception as exc:
                writer.writerow({"company": row.company, "country": row.country, "weight_pct": row.weight_pct, "match_count": 0, "status": f"search failed: {exc}"})
                LOGGER.warning("%s/600: contract discovery failed for %s: %s", number, row.company, exc)
            time.sleep(pause_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage the official STOXX Europe 600 membership without enabling trades.")
    parser.add_argument("--discover-ibkr", action="store_true", help="also query IBKR for candidate stock contracts; no market data or orders")
    parser.add_argument("--limit", type=int, default=0, help="discover only N members for a data-only trial (default: all 600)")
    parser.add_argument("--top-by-weight", action="store_true", help="when limiting, start with the highest-weight constituents")
    parser.add_argument("--pause-seconds", type=float, default=0.15, help="pause between IBKR name searches (default: 0.15)")
    args = parser.parse_args()
    load_local_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    members = write_membership()
    if not args.discover_ibkr:
        return
    host, port, client_id = ibkr_connection_settings()
    broker = IBKRClient(host, port, client_id + 50)
    broker.connect_and_start()
    try:
        if args.limit:
            members = members.nlargest(args.limit, "weight_pct") if args.top_by_weight else members.head(args.limit)
        discover_contracts(members, broker, max(0.05, args.pause_seconds))
    finally:
        broker.close()


if __name__ == "__main__":
    main()
