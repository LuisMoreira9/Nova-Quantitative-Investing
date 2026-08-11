"""IBKR paper-trading executor for Nova Quant Club.

Strategies only return signals.  This process owns the TWS connection, asks
``RiskGateway`` for approval, and is the only component allowed to place an
Interactive Brokers paper order.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

from core.ibkr_adapter import IBKRClient, LiveBar
from core.instruments import instrument_for
from core.risk_gateway import RiskGateway, RiskGatewayError


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
STRATEGIES_DIR = PROJECT_ROOT / "strategies"


class ExecutorConfigError(Exception):
    """Raised when the local IBKR executor environment is incomplete."""


def load_local_env() -> None:
    """Load the Git-ignored local configuration file when available."""

    if load_dotenv is not None:
        load_dotenv(PROJECT_ROOT / ".env")


def load_strategy_classes(strategies_dir: Path = STRATEGIES_DIR) -> list[type]:
    """Find concrete classes in ``strategies/`` with an ``on_bar`` method."""

    classes: list[type] = []
    for strategy_file in sorted(strategies_dir.glob("*.py")):
        if strategy_file.name.startswith("__"):
            continue
        module = importlib.import_module(f"strategies.{strategy_file.stem}")
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if (
                obj.__module__ == module.__name__
                and not obj.__name__.startswith("_")
                and callable(getattr(obj, "on_bar", None))
                and not inspect.isabstract(obj)
            ):
                classes.append(obj)
    return classes


def instantiate_strategies() -> list[Any]:
    selected = {name.strip() for name in os.getenv("NOVA_ENABLED_STRATEGIES", "").split(",") if name.strip()}
    classes = load_strategy_classes()
    if selected:
        classes = [strategy_class for strategy_class in classes if strategy_class.__name__ in selected]
        missing = selected - {strategy_class.__name__ for strategy_class in classes}
        if missing:
            raise ExecutorConfigError(f"unknown enabled strategies: {', '.join(sorted(missing))}")
    strategies = [strategy_class() for strategy_class in classes]
    if not strategies:
        raise ExecutorConfigError("no concrete strategies with an on_bar method were found")
    return strategies


def ibkr_connection_settings() -> tuple[str, int, int]:
    """Read the local TWS paper API endpoint; defaults match paper TWS."""

    host = os.getenv("IBKR_HOST", "127.0.0.1")
    try:
        port = int(os.getenv("IBKR_PORT", "7497"))
        client_id = int(os.getenv("IBKR_CLIENT_ID", "101"))
    except ValueError as exc:
        raise ExecutorConfigError("IBKR_PORT and IBKR_CLIENT_ID must be integers") from exc
    return host, port, client_id


def delayed_poll_seconds() -> int:
    """Return the interval between delayed market-data snapshots."""

    try:
        seconds = int(os.getenv("IBKR_DELAYED_POLL_SECONDS", "60"))
    except ValueError as exc:
        raise ExecutorConfigError("IBKR_DELAYED_POLL_SECONDS must be an integer") from exc
    if seconds < 15:
        raise ExecutorConfigError("IBKR_DELAYED_POLL_SECONDS must be at least 15 seconds")
    return seconds


def route_signal(signal: dict[str, Any], broker: IBKRClient, risk_gateway: RiskGateway, strategy_id: str) -> bool:
    """Risk-check one signal, then submit it to the connected IBKR paper account."""

    decision = risk_gateway.evaluate(signal, broker.get_account())
    if not decision.approved:
        LOGGER.warning("Risk rejected %s: %s", signal, decision.reason)
        return False
    order_id = broker.place_market_order(signal, strategy_id)
    status = broker.wait_for_terminal_order(order_id)
    if status != "Filled":
        LOGGER.warning("IBKR paper order %s ended as %s", order_id, status)
        return False
    LOGGER.info("IBKR paper order filled: id=%s symbol=%s", order_id, signal["symbol"])
    return True


def handle_bar(bar: LiveBar, strategies: list[Any], broker: IBKRClient, risk_gateway: RiskGateway) -> None:
    """Fan one IBKR bar to matching strategies and route any returned signal."""

    for strategy in strategies:
        if getattr(strategy, "symbol", "").upper() != bar.symbol.upper():
            continue
        try:
            signal = strategy.on_bar(bar)
            if signal:
                filled = route_signal(signal, broker, risk_gateway, strategy.__class__.__name__)
                callback = getattr(strategy, "on_order_result", None)
                if callable(callback):
                    callback(signal["action"], filled, bar.timestamp)
        except RiskGatewayError:
            LOGGER.exception("Risk gateway could not evaluate signal: %s", signal)
        except Exception:
            LOGGER.exception("Strategy %s failed while handling %s", strategy.__class__.__name__, bar.symbol)


def is_us_equity_regular_session() -> bool:
    """Avoid queuing delayed-data pilot orders before the U.S. cash session."""

    now = datetime.now(ZoneInfo("America/New_York"))
    return now.weekday() < 5 and (now.hour, now.minute) >= (9, 30) and (now.hour, now.minute) < (16, 0)


def should_poll_symbol(symbol: str) -> bool:
    spec = instrument_for(symbol)
    if spec.currency == "USD" and spec.primary_exchange in {"NASDAQ", "NYSE", "ARCA"}:
        return is_us_equity_regular_session()
    return True


def main() -> None:
    """Legacy IBKR-price executor; disabled for the shared-data architecture."""

    load_local_env()
    if os.getenv("NOVA_ALLOW_LEGACY_IBKR_PRICE_DATA") != "CONFIRM":
        raise ExecutorConfigError(
            "IBKR price polling is disabled. Use a shared external-data executor "
            "(for example core.europe_yfinance_executor) and keep IBKR for execution only."
        )
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    strategies = instantiate_strategies()
    host, port, client_id = ibkr_connection_settings()
    broker = IBKRClient(host, port, client_id)
    broker.connect_and_start()
    risk_gateway = RiskGateway(price_provider=broker)
    symbols = sorted({strategy.symbol.upper() for strategy in strategies})
    poll_seconds = delayed_poll_seconds()
    try:
        # Fail closed before the loop (and before any strategy can emit an
        # order) if TWS cannot identify one of the intended native listings.
        broker.verify_instruments(symbols)
        LOGGER.info("Connected to IBKR paper TWS for account %s; symbols: %s", broker.account_id, ", ".join(symbols))
        LOGGER.warning(
            "Using IBKR delayed market data (normally 15–20 minutes old); polling every %s seconds.",
            poll_seconds,
        )
        while broker.isConnected():
            for symbol in symbols:
                if not should_poll_symbol(symbol):
                    LOGGER.debug("Skipping %s outside U.S. regular trading hours", symbol)
                    continue
                trade = broker.get_latest_trade(symbol)
                # Delayed snapshots provide a price, not a complete OHLCV bar.
                # Nova's Stage 1 mean-reversion strategy consumes only ``close``.
                bar = LiveBar(symbol, datetime.now(timezone.utc), trade.price, trade.price, trade.price, trade.price, 0.0)
                handle_bar(bar, strategies, broker, risk_gateway)
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        LOGGER.info("Stopping IBKR paper executor")
    finally:
        broker.close()


if __name__ == "__main__":
    main()
