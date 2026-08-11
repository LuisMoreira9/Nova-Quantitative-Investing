"""Local Stage 1 executor for the Nova Quant Club paper-trading pipeline.

This is the orchestration layer. It knows how to:
    1. load student strategy classes from ``strategies/``;
    2. subscribe to live Alpaca stock bars;
    3. call each strategy's ``on_bar(bar)`` method;
    4. send any returned signal through ``core/risk_gateway.py``;
    5. submit an Alpaca paper order only when the gateway approves it.

The executor is intentionally the only file that submits orders. Student
strategy files should not import Alpaca trading clients or API keys. Keeping
that boundary clear makes the shared repo safer and easier to review.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - useful before dependencies are installed
    load_dotenv = None

from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.live import StockDataStream
from alpaca.data.requests import StockLatestTradeRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

from core.risk_gateway import RiskGateway, RiskGatewayError


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
STRATEGIES_DIR = PROJECT_ROOT / "strategies"


class ExecutorConfigError(Exception):
    """Raised when the local executor environment is incomplete."""


class AlpacaLatestPriceProvider:
    """Small adapter between Alpaca's SDK and ``RiskGateway``.

    Alpaca-py expects a ``StockLatestTradeRequest`` object and returns a mapping
    keyed by symbol. The risk gateway deliberately does not know those SDK
    details; it only asks for ``get_latest_trade(symbol)`` and reads the price
    from the returned trade object.
    """

    def __init__(self, client: StockHistoricalDataClient, feed: DataFeed) -> None:
        self.client = client
        self.feed = feed

    def get_latest_trade(self, symbol: str) -> Any:
        request = StockLatestTradeRequest(symbol_or_symbols=symbol, feed=self.feed)
        latest_trades = self.client.get_stock_latest_trade(request)
        return latest_trades[symbol]


def load_local_env() -> None:
    """Load ``.env`` for local development when python-dotenv is installed.

    The committed repo only contains ``.env.example``. Each member can create a
    private ``.env`` on their own machine after generating Alpaca paper keys.
    """

    if load_dotenv is not None:
        load_dotenv(PROJECT_ROOT / ".env")


def load_strategy_classes(strategies_dir: Path = STRATEGIES_DIR) -> list[type]:
    """Find strategy classes in ``strategies/``.

    A strategy is any class defined in a strategy module that exposes an
    ``on_bar`` method. This lets students add one Python file per strategy
    without editing this executor every time.
    """

    classes: list[type] = []
    for strategy_file in sorted(strategies_dir.glob("*.py")):
        if strategy_file.name.startswith("__"):
            continue

        module_name = f"strategies.{strategy_file.stem}"
        module = importlib.import_module(module_name)

        for _, obj in inspect.getmembers(module, inspect.isclass):
            is_defined_in_module = obj.__module__ == module.__name__
            has_on_bar_contract = callable(getattr(obj, "on_bar", None))
            # ``strategies/base.py`` is a shared abstract contract, not a
            # runnable strategy.  Only concrete classes can be instantiated.
            is_concrete = not inspect.isabstract(obj)
            if is_defined_in_module and has_on_bar_contract and is_concrete:
                classes.append(obj)

    return classes


def instantiate_strategies() -> list[Any]:
    """Create one instance of every discovered strategy class."""

    strategies = [strategy_class() for strategy_class in load_strategy_classes()]
    if not strategies:
        raise ExecutorConfigError("no strategy classes with an on_bar method were found")
    return strategies


def required_env(name: str) -> str:
    """Read a required environment variable or fail with a clear message."""

    value = os.getenv(name)
    if not value:
        raise ExecutorConfigError(f"missing required environment variable: {name}")
    return value


def configured_data_feed() -> DataFeed:
    """Return the Alpaca stock data feed to use.

    ``iex`` is the default because Alpaca's free market-data plan normally
    supports it. Members with a SIP subscription can set ``ALPACA_DATA_FEED=sip``
    in their private ``.env``.
    """

    feed_name = os.getenv("ALPACA_DATA_FEED", "iex").lower()
    try:
        return DataFeed(feed_name)
    except ValueError as exc:
        valid_feeds = ", ".join(feed.value for feed in DataFeed)
        raise ExecutorConfigError(f"invalid ALPACA_DATA_FEED={feed_name!r}; expected one of: {valid_feeds}") from exc


def build_clients() -> tuple[TradingClient, StockDataStream, StockHistoricalDataClient, DataFeed]:
    """Create Alpaca clients using paper-trading credentials.

    ``TradingClient(..., paper=True)`` is the important safety setting here. It
    points order submission at Alpaca's simulated paper account rather than live
    brokerage trading.
    """

    api_key = required_env("ALPACA_API_KEY")
    secret_key = required_env("ALPACA_SECRET_KEY")
    feed = configured_data_feed()

    trading_client = TradingClient(api_key, secret_key, paper=True)
    stream_client = StockDataStream(api_key, secret_key, feed=feed)
    data_client = StockHistoricalDataClient(api_key, secret_key)
    return trading_client, stream_client, data_client, feed


def normalize_action(action: str) -> OrderSide:
    """Convert a strategy action string into Alpaca's order-side enum."""

    action = action.upper().strip()
    if action == "BUY":
        return OrderSide.BUY
    if action == "SELL":
        return OrderSide.SELL
    raise ExecutorConfigError(f"unsupported order action: {action}")


async def route_signal(
    signal: dict[str, Any],
    trading_client: TradingClient,
    risk_gateway: RiskGateway,
    strategy_id: str,
) -> None:
    """Risk-check one signal and submit a paper order only if approved."""

    account = trading_client.get_account()
    decision = risk_gateway.evaluate(signal, account)

    if not decision.approved:
        LOGGER.warning("Risk rejected %s: %s", signal, decision.reason)
        return

    # This is the only order-submission point in Stage 1. Keeping it here makes
    # code review simple: search for submit_order and confirm it is guarded by
    # RiskGateway.evaluate(...).
    order_data = MarketOrderRequest(
        symbol=str(signal["symbol"]).upper(),
        qty=float(signal["qty"]),
        side=normalize_action(str(signal["action"])),
        time_in_force=TimeInForce.DAY,
        # Alpaca retains this value with the order.  It makes paper-account
        # activity attributable to a local strategy in dashboard/app.py.
        client_order_id=f"nova-{strategy_id[:20]}-{uuid4().hex[:12]}",
    )
    order = trading_client.submit_order(order_data=order_data)
    LOGGER.info("Paper order submitted: %s", getattr(order, "id", order))


async def handle_bar(bar: Any, strategies: list[Any], trading_client: TradingClient, risk_gateway: RiskGateway) -> None:
    """Fan one incoming market bar out to the strategies that trade its symbol."""

    for strategy in strategies:
        # Each strategy declares the one symbol it watches. Later versions can
        # support multi-symbol strategies by standardizing a richer interface.
        if getattr(strategy, "symbol", "").upper() != bar.symbol.upper():
            continue

        try:
            signal = strategy.on_bar(bar)
        except Exception:
            # A broken student strategy should be logged and skipped; it should
            # not crash the whole club executor.
            LOGGER.exception("Strategy %s failed while handling %s", strategy.__class__.__name__, bar.symbol)
            continue

        if signal:
            try:
                await route_signal(signal, trading_client, risk_gateway, strategy.__class__.__name__)
            except RiskGatewayError:
                LOGGER.exception("Risk gateway could not evaluate signal: %s", signal)
            except Exception:
                LOGGER.exception("Failed while routing signal: %s", signal)


def main() -> None:
    """Start the local paper-trading executor."""

    load_local_env()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")

    strategies = instantiate_strategies()
    trading_client, stream_client, data_client, feed = build_clients()
    risk_gateway = RiskGateway(price_provider=AlpacaLatestPriceProvider(data_client, feed))

    symbols = sorted({strategy.symbol for strategy in strategies})
    LOGGER.info("Starting Nova Quant Club paper executor for symbols: %s", ", ".join(symbols))

    async def on_bar(bar: Any) -> None:
        await handle_bar(bar, strategies, trading_client, risk_gateway)

    stream_client.subscribe_bars(on_bar, *symbols)
    stream_client.run()


if __name__ == "__main__":
    main()
