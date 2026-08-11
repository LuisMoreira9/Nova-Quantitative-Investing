"""Data-only IBKR contract validation for the reviewed STOXX registry.

This command does not place orders and does not alter the ``approved`` flags.
It creates a local CSV report which a maintainer reviews before enabling any
new registry row.
"""

from __future__ import annotations

import argparse
import csv
import logging
from pathlib import Path

from core.ibkr_adapter import IBKRClient
from core.main_executor import ibkr_connection_settings, load_local_env
from core.stoxx_europe_600 import REGISTRY_PATH, load_registry


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "data" / "stoxx_europe_600_contract_validation.csv"


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate STOXX registry contracts with local paper TWS.")
    parser.add_argument("--all", action="store_true", help="Validate every registry row; otherwise only approved rows.")
    args = parser.parse_args()
    load_local_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    rows = load_registry(REGISTRY_PATH)
    if not args.all:
        rows = [row for row in rows if row.approved]
    host, port, client_id = ibkr_connection_settings()
    broker = IBKRClient(host, port, client_id + 40)
    broker.connect_and_start()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with REPORT_PATH.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=["instrument_id", "isin", "approved", "result"])
            writer.writeheader()
            for row in rows:
                try:
                    broker.verify_instruments([row.instrument_id])
                    result = "resolved"
                except Exception as exc:  # report each failure without placing an order
                    result = f"failed: {exc}".replace("\n", " ")
                writer.writerow({"instrument_id": row.instrument_id, "isin": row.isin, "approved": row.approved, "result": result})
                logging.info("%s: %s", row.instrument_id, result)
    finally:
        broker.close()


if __name__ == "__main__":
    main()
