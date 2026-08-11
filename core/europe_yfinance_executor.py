"""EUR European momentum-reversal paper-trading pilot for the current session."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from core.ibkr_adapter import IBKRClient
from core.main_executor import ibkr_connection_settings, load_local_env, route_signal
from core.risk_gateway import RiskGateway
from core.sp500_yfinance_executor import candidates, fetch_closes
from core.stoxx_europe_600 import approved_universe, write_approved_symbols
from core.yahoo_price_provider import YahooFxPriceProvider


LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURE_TEST_MARKER = ROOT / "data" / "europe_architecture_test_completed.txt"
ARCHITECTURE_TEST_ARM = ROOT / "data" / "europe_architecture_test_arm.txt"
def europe_regular_session() -> bool:
    now = datetime.now(ZoneInfo("Europe/Amsterdam"))
    return now.weekday() < 5 and (now.hour, now.minute) >= (9, 0) and (now.hour, now.minute) < (17, 30)


def architecture_test_signal(closes: dict, actual: set[str]) -> dict | None:
    """Emit exactly one controlled buy for dashboard/order-path validation."""

    if ARCHITECTURE_TEST_MARKER.exists():
        return None
    for symbol in sorted(closes):
        if symbol in actual:
            continue
        series = closes[symbol]
        return {
            "symbol": symbol,
            "action": "BUY",
            "qty": 1,
            "reference_price": float(series.iloc[-1]),
            "reference_currency": "EUR",
            "reference_timestamp": series.index[-1].isoformat(),
        }
    return None


def main() -> None:
    load_local_env()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    universe = approved_universe()
    write_approved_symbols()
    if not universe:
        raise RuntimeError("no approved STOXX Europe 600 registry rows are available")
    host, port, client_id = ibkr_connection_settings()
    # Intentionally permissive while the club validates the paper-trading
    # architecture. This is not a production strategy parameter.
    threshold = float(os.getenv("NOVA_EUROPE_REVERSAL_THRESHOLD", "0.00001"))
    if not 0 < threshold < 0.1:
        raise ValueError("NOVA_EUROPE_REVERSAL_THRESHOLD must be between 0 and 0.1")
    scan_seconds = max(60, int(os.getenv("NOVA_EUROPE_SCAN_SECONDS", "60")))
    architecture_test_mode = (
        os.getenv("NOVA_EUROPE_ARCHITECTURE_TEST_MODE", "").upper() == "BUY_ONE"
        or ARCHITECTURE_TEST_ARM.exists()
    )
    broker = IBKRClient(host, port, client_id + 30)
    broker.connect_and_start()
    risk = RiskGateway(YahooFxPriceProvider())
    managed: set[str] = set()
    try:
        broker.verify_instruments(list(universe))
        LOGGER.warning(
            "European yfinance paper executor started for %s approved STOXX registry rows at %.3f%% reversal threshold.",
            len(universe), threshold * 100,
        )
        while broker.isConnected():
            if not europe_regular_session():
                time.sleep(60)
                continue
            LOGGER.info("European scan started for %s approved instruments.", len(universe))
            raw = fetch_closes([row.yfinance_ticker for row in universe.values()])
            closes = {instrument: raw[row.yfinance_ticker] for instrument, row in universe.items() if row.yfinance_ticker in raw}
            actual = {row["symbol"].upper() for row in broker.get_portfolio()}
            signals = candidates(closes, managed, max_orders=2, threshold=threshold, currency="EUR")
            if architecture_test_mode:
                forced = architecture_test_signal(closes, actual)
                if forced:
                    signals = [forced]
                    LOGGER.warning("Architecture test mode: proposing one controlled %s buy.", forced["symbol"])
            LOGGER.info("European scan completed: %s quotes, %s eligible signals.", len(closes), len(signals))
            for signal in signals:
                # Existing positions (including the ASML dashboard demo) are
                # visible but never adopted or sold by this new strategy.
                if signal["action"] == "BUY" and signal["symbol"] in actual:
                    continue
                try:
                    if route_signal(signal, broker, risk, "EuropeYfinanceMomentumReversal"):
                        if signal["action"] == "BUY":
                            managed.add(signal["symbol"])
                            actual.add(signal["symbol"])
                            if architecture_test_mode:
                                ARCHITECTURE_TEST_MARKER.parent.mkdir(parents=True, exist_ok=True)
                                ARCHITECTURE_TEST_MARKER.write_text(signal["symbol"] + "\n", encoding="utf-8")
                                ARCHITECTURE_TEST_ARM.unlink(missing_ok=True)
                        else:
                            managed.discard(signal["symbol"])
                            actual.discard(signal["symbol"])
                except Exception:
                    LOGGER.exception("Could not process European candidate %s", signal["symbol"])
            time.sleep(scan_seconds)
    except KeyboardInterrupt:
        LOGGER.info("Stopping European yfinance paper pilot")
    finally:
        broker.close()


if __name__ == "__main__":
    main()
