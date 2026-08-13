"""Build the local position classification catalogue from Yahoo reference data.

Run this offline (for example nightly), not as part of the portfolio API:
``python -m core.refresh_security_classifications``.
It writes cached reference data only; it never connects to TWS or submits an
order.  A licensed reference-data source can replace Yahoo later without any
dashboard or execution changes.
"""

from __future__ import annotations

import csv
from pathlib import Path

import yfinance as yf

from dashboard.ibkr_account import load_paper_account_data
from dashboard.portfolio_classification import CATALOGUE_PATH, EXCHANGE_COUNTRIES, _region


def main() -> None:
    snapshot = load_paper_account_data()
    positions = snapshot["positions"]
    rows: list[dict[str, str]] = []
    for position in positions.itertuples():
        symbol = str(position.symbol).upper()
        exchange = str(position.exchange).upper()
        currency = str(position.currency).upper()
        country = EXCHANGE_COUNTRIES.get(exchange, "Unknown")
        sector = "Unclassified"
        try:
            info = yf.Ticker(symbol).get_info()
            country = str(info.get("country") or country).strip() or country
            sector = str(info.get("sector") or sector).strip() or sector
        except Exception:
            pass
        rows.append({
            "symbol": symbol,
            "exchange": exchange,
            "currency": currency,
            "country": country,
            "region": _region(country),
            "sector": sector,
        })

    CATALOGUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CATALOGUE_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["symbol", "exchange", "currency", "country", "region", "sector"])
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda row: (row["symbol"], row["exchange"], row["currency"])))
    print(f"Wrote {len(rows)} cached classifications to {CATALOGUE_PATH}")


if __name__ == "__main__":
    main()
