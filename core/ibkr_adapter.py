"""Small synchronous facade over Interactive Brokers' callback-driven TWS API.

Nova uses this adapter only with a local TWS/IB Gateway paper-trading session.
It centralizes IBKR connection state, order IDs, market-data callbacks, and
read-only account queries so strategies and risk rules remain broker-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from threading import Event, Lock, Thread
from time import monotonic
from typing import Any, Callable
from uuid import uuid4

from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.execution import ExecutionFilter
from ibapi.order import Order
from ibapi.wrapper import EWrapper

from core.instruments import InstrumentSpec, instrument_for


LOGGER = logging.getLogger(__name__)


class IBKRConnectionError(RuntimeError):
    """Raised when TWS/IB Gateway is unavailable or rejects an API request."""


@dataclass(frozen=True)
class LiveBar:
    """Broker-neutral bar shape consumed by Nova's existing strategies."""

    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class LatestTrade:
    """Minimal latest-price model consumed by ``RiskGateway``."""

    price: float
    currency: str


class IBKRClient(EWrapper, EClient):
    """Thread-safe, paper-session client for the narrow Nova execution surface."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 101,
        market_data_type: int = 3,
    ) -> None:
        EClient.__init__(self, self)
        self._host = host
        self._port = port
        self.client_id = client_id
        # IBKR type 3 is delayed data (normally 15–20 minutes). It is the
        # default until a real-time exchange subscription is configured.
        self.market_data_type = market_data_type
        self._lock = Lock()
        self._ready = Event()
        self._accounts_ready = Event()
        self._account_summary_ready = Event()
        self._portfolio_ready = Event()
        self._open_orders_ready = Event()
        self._executions_ready = Event()
        self._order_status_events: dict[int, Event] = {}
        self._order_statuses: dict[int, str] = {}
        self._order_references: dict[int, str] = {}
        self._next_order_id: int | None = None
        self._next_request_id = 10_000
        self._thread: Thread | None = None
        self._accounts: list[str] = []
        self._account_summary: dict[str, str] = {}
        self._account_currency: str | None = None
        self._portfolio: list[dict[str, Any]] = []
        self._currency_cash_balances: dict[str, float] = {}
        self._open_orders: list[dict[str, Any]] = []
        self._executions: list[dict[str, Any]] = []
        self._price_events: dict[int, Event] = {}
        self._prices: dict[int, float] = {}
        self._bid_prices: dict[int, float] = {}
        self._ask_prices: dict[int, float] = {}
        self._allow_bid_ask: set[int] = set()
        self._contract_detail_events: dict[int, Event] = {}
        self._contract_details: dict[int, list[Contract]] = {}
        self._symbol_match_events: dict[int, Event] = {}
        self._symbol_matches: dict[int, list[Contract]] = {}
        self._resolved_contracts: dict[str, Contract] = {}
        self._request_errors: dict[int, str] = {}
        self._bar_handlers: dict[int, tuple[str, Callable[[LiveBar], None]]] = {}
        self._fatal_error: str | None = None

    def connect_and_start(self, timeout: float = 15.0) -> None:
        """Connect to local TWS/IB Gateway and wait for the order-ID handshake."""

        self.connect(self._host, self._port, self.client_id)
        self._thread = Thread(target=self.run, name="ibkr-api", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            self.disconnect()
            raise IBKRConnectionError(
                f"Timed out connecting to IBKR at {self._host}:{self._port}. "
                "Confirm that paper TWS is running and socket clients are enabled."
            )
        if self._fatal_error:
            raise IBKRConnectionError(self._fatal_error)
        if not self._accounts_ready.wait(timeout):
            raise IBKRConnectionError("IBKR did not report a managed account after connecting.")

    def close(self) -> None:
        """Close the local API socket without changing account state."""

        if self.isConnected():
            self.disconnect()
        if self._thread is not None:
            self._thread.join(timeout=2)

    @property
    def account_id(self) -> str:
        if not self._accounts:
            raise IBKRConnectionError("No managed account is available from TWS.")
        return self._accounts[0]

    def get_account(self) -> dict[str, str]:
        """Return the buying-power fields required by Nova's risk gateway."""

        request_id = self._request_id()
        self._account_summary.clear()
        self._account_currency = None
        self._account_summary_ready.clear()
        self.reqAccountSummary(request_id, "All", "NetLiquidation,TotalCashValue,BuyingPower")
        self._wait(self._account_summary_ready, "account summary")
        self.cancelAccountSummary(request_id)
        buying_power = self._account_summary.get("BuyingPower", "0")
        return {
            "buying_power": buying_power,
            "daytrading_buying_power": buying_power,
            "equity": self._account_summary.get("NetLiquidation", "0"),
            "cash": self._account_summary.get("TotalCashValue", "0"),
            "currency": self._account_currency or "",
        }

    def get_latest_trade(self, symbol: str) -> LatestTrade:
        """Request the configured (currently delayed) last trade for risk math."""

        spec = instrument_for(symbol)
        return self._latest_trade_for_contract(self.instrument_contract(spec), spec.instrument_id, spec.currency)

    def get_fx_rate_to_base(self, currency: str, base_currency: str = "EUR") -> float:
        """Return units of ``base_currency`` per one unit of ``currency``.

        Nova reports in EUR for now.  IBKR quotes the needed EUR/foreign cash
        pair as foreign units per EUR, so the reciprocal is the conversion
        from the listing currency back into EUR.
        """

        source = currency.upper().strip()
        base = base_currency.upper().strip()
        if source == base:
            return 1.0
        if base != "EUR":
            raise IBKRConnectionError("FX reporting currently supports EUR as the base currency only.")
        contract = Contract()
        contract.symbol = "EUR"
        contract.secType = "CASH"
        contract.exchange = "IDEALPRO"
        contract.currency = source
        quote = self._latest_trade_for_contract(contract, f"EUR{source}", source, allow_bid_ask=True)
        return 1.0 / quote.price

    def _latest_trade_for_contract(
        self, contract: Contract, label: str, currency: str, *, allow_bid_ask: bool = False
    ) -> LatestTrade:
        """Fetch a delayed snapshot for an already-resolved IBKR contract."""

        request_id = self._request_id()
        event = Event()
        with self._lock:
            self._price_events[request_id] = event
            if allow_bid_ask:
                self._allow_bid_ask.add(request_id)
        try:
            self.reqMarketDataType(self.market_data_type)
            self.reqMktData(request_id, contract, "", True, False, [])
            self._wait(event, f"latest price for {label}")
            with self._lock:
                request_error = self._request_errors.pop(request_id, None)
                price = self._prices.pop(request_id, None)
            if request_error:
                raise IBKRConnectionError(request_error)
        finally:
            self.cancelMktData(request_id)
            with self._lock:
                self._price_events.pop(request_id, None)
                self._request_errors.pop(request_id, None)
                self._bid_prices.pop(request_id, None)
                self._ask_prices.pop(request_id, None)
                self._allow_bid_ask.discard(request_id)
        if price is None or price <= 0:
            raise IBKRConnectionError(f"IBKR did not return a delayed last price for {label}.")
        return LatestTrade(price=price, currency=currency)

    def subscribe_real_time_bars(self, symbol: str, handler: Callable[[LiveBar], None]) -> None:
        """Subscribe to five-second live TRADES bars for one U.S. stock contract."""

        request_id = self._request_id()
        self._bar_handlers[request_id] = (symbol.upper(), handler)
        spec = instrument_for(symbol)
        self.reqRealTimeBars(request_id, self.instrument_contract(spec), 5, "TRADES", False, [])
        LOGGER.info("Subscribed to IBKR real-time bars for %s", symbol.upper())

    def place_market_order(self, signal: dict[str, Any], strategy_id: str) -> int:
        """Submit one paper market order after ``RiskGateway`` approval."""

        action = str(signal["action"]).upper().strip()
        if action not in {"BUY", "SELL"}:
            raise IBKRConnectionError(f"Unsupported IBKR action: {action}")
        order = Order()
        order.action = action
        order.orderType = "MKT"
        order.totalQuantity = float(signal["qty"])
        order.tif = "DAY"
        # TWS 10.4+ rejects these legacy SMART-routing attributes when their
        # old ibapi defaults are True. Explicitly disable them for plain MKT
        # orders so modern paper TWS accepts the request.
        order.eTradeOnly = False
        order.firmQuoteOnly = False
        order.orderRef = f"nova-{strategy_id[:20]}-{uuid4().hex[:12]}"
        # Explicit allocation is harmless for an individual account and is
        # required for a number of paper/FA account configurations.
        order.account = self.account_id
        with self._lock:
            if self._next_order_id is None:
                raise IBKRConnectionError("IBKR order ID is not initialized.")
            order_id = self._next_order_id
            self._next_order_id += 1
            self._order_status_events[order_id] = Event()
            self._order_statuses.pop(order_id, None)
            self._order_references[order_id] = order.orderRef
        spec = instrument_for(str(signal["symbol"]))
        self.placeOrder(order_id, self.instrument_contract(spec), order)
        LOGGER.info("IBKR paper order submitted: id=%s ref=%s", order_id, order.orderRef)
        return order_id

    def place_fx_market_order(
        self,
        base_currency: str,
        quote_currency: str,
        quantity: int,
        action: str,
        strategy_id: str,
    ) -> int:
        """Submit a paper IDEALPRO cash-pair market order without FX quotes.

        ``quantity`` is in the base-currency units of the pair. For example,
        buying 3,000 EUR.USD buys EUR and sells USD, which hedges the USD
        currency exposure of a EUR-based investor's U.S. equity holdings.
        """

        base, quote = base_currency.upper().strip(), quote_currency.upper().strip()
        normalized_action = action.upper().strip()
        if base == quote or len(base) != 3 or len(quote) != 3:
            raise IBKRConnectionError("FX pairs require two distinct ISO currency codes")
        if normalized_action not in {"BUY", "SELL"}:
            raise IBKRConnectionError(f"Unsupported IBKR FX action: {action}")
        if not isinstance(quantity, int) or quantity <= 0:
            raise IBKRConnectionError("FX order quantity must be a positive whole number")

        contract = Contract()
        contract.symbol = base
        contract.secType = "CASH"
        contract.exchange = "IDEALPRO"
        contract.currency = quote
        order = Order()
        order.action = normalized_action
        order.orderType = "MKT"
        order.totalQuantity = quantity
        order.tif = "DAY"
        order.eTradeOnly = False
        order.firmQuoteOnly = False
        order.orderRef = f"nova-{strategy_id[:20]}-{uuid4().hex[:12]}"
        order.account = self.account_id
        with self._lock:
            if self._next_order_id is None:
                raise IBKRConnectionError("IBKR order ID is not initialized.")
            order_id = self._next_order_id
            self._next_order_id += 1
            self._order_status_events[order_id] = Event()
            self._order_statuses.pop(order_id, None)
            self._order_references[order_id] = order.orderRef
        self.placeOrder(order_id, contract, order)
        LOGGER.info("IBKR paper FX order submitted: id=%s ref=%s pair=%s.%s", order_id, order.orderRef, base, quote)
        return order_id

    def wait_for_terminal_order(self, order_id: int, timeout: float = 45.0) -> str:
        """Wait for a Nova-owned order to fill, cancel, or become inactive."""

        with self._lock:
            event = self._order_status_events.get(order_id)
            status = self._order_statuses.get(order_id)
            order_ref = self._order_references.get(order_id)
        if event is None:
            raise IBKRConnectionError(f"No local status tracking exists for order {order_id}.")
        terminal = {"Filled", "Cancelled", "ApiCancelled", "Inactive", "Rejected"}
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            with self._lock:
                status = self._order_statuses.get(order_id)
            if status in terminal:
                break
            # IBKR documents that a market order can fill without an
            # orderStatus callback. Check executions as the complementary
            # source before declaring the test order lost.
            if order_ref and any(row.get("client_order_id") == order_ref for row in self.get_today_executions()):
                status = "Filled"
                break
            event.wait(min(2.0, max(0.1, deadline - monotonic())))
            event.clear()
        else:
            raise IBKRConnectionError(f"Timed out waiting for order {order_id} to reach a terminal status.")
        with self._lock:
            self._order_status_events.pop(order_id, None)
            self._order_references.pop(order_id, None)
        return status or "Unknown"

    def get_portfolio(self) -> list[dict[str, Any]]:
        """Return current portfolio rows from IBKR account updates."""

        portfolio, _ = self.get_portfolio_and_cash()
        return portfolio

    def get_portfolio_and_cash(self) -> tuple[list[dict[str, Any]], dict[str, float]]:
        """Return securities plus TWS's per-currency cash balances.

        The cash figures are needed to calculate *net* currency exposure. A
        foreign stock bought with a debit in that same currency is naturally
        currency-offset and must not receive a second synthetic FX hedge.
        """

        self._portfolio.clear()
        self._currency_cash_balances.clear()
        self._portfolio_ready.clear()
        self.reqAccountUpdates(True, self.account_id)
        self._wait(self._portfolio_ready, "portfolio download")
        self.reqAccountUpdates(False, self.account_id)
        return list(self._portfolio), dict(self._currency_cash_balances)

    def get_open_orders(self) -> list[dict[str, Any]]:
        """Return currently open API/TWS orders; does not mutate them."""

        self._open_orders.clear()
        self._open_orders_ready.clear()
        self.reqOpenOrders()
        self._wait(self._open_orders_ready, "open-order download")
        return list(self._open_orders)

    def get_all_open_orders(self) -> list[dict[str, Any]]:
        """Return all open TWS orders visible to this user, read-only."""

        self._open_orders.clear()
        self._open_orders_ready.clear()
        self.reqAllOpenOrders()
        self._wait(self._open_orders_ready, "all-open-order download")
        return list(self._open_orders)

    def get_today_executions(self) -> list[dict[str, Any]]:
        """Return executions available to the active TWS session for today."""

        request_id = self._request_id()
        self._executions.clear()
        self._executions_ready.clear()
        self.reqExecutions(request_id, ExecutionFilter())
        self._wait(self._executions_ready, "execution download")
        return list(self._executions)

    def verify_instruments(self, instrument_ids: list[str]) -> dict[str, Contract]:
        """Resolve every configured listing through TWS without placing orders."""

        return {instrument_id.upper(): self._resolve_contract(instrument_for(instrument_id)) for instrument_id in instrument_ids}

    def instrument_contract(self, spec: InstrumentSpec) -> Contract:
        """Return the exact TWS-resolved contract for a configured listing."""

        return self._resolved_contracts.get(spec.instrument_id) or self._resolve_contract(spec)

    def _resolve_contract(self, spec: InstrumentSpec) -> Contract:
        if spec.instrument_id in self._resolved_contracts:
            return self._resolved_contracts[spec.instrument_id]
        request_id = self._request_id()
        event = Event()
        with self._lock:
            self._contract_detail_events[request_id] = event
            self._contract_details[request_id] = []
        try:
            self.reqContractDetails(request_id, self._configured_stock_contract(spec))
            self._wait(event, f"contract details for {spec.instrument_id}")
            with self._lock:
                request_error = self._request_errors.pop(request_id, None)
                matches = self._contract_details.pop(request_id, [])
            if request_error:
                raise IBKRConnectionError(request_error)
            if len(matches) != 1:
                raise IBKRConnectionError(
                    f"IBKR returned {len(matches)} contract matches for {spec.instrument_id}; refusing an ambiguous order."
                )
            self._resolved_contracts[spec.instrument_id] = matches[0]
            LOGGER.info("Verified %s as IBKR conId %s (%s)", spec.instrument_id, matches[0].conId, matches[0].localSymbol)
            return matches[0]
        finally:
            with self._lock:
                self._contract_detail_events.pop(request_id, None)
                self._contract_details.pop(request_id, None)
                self._request_errors.pop(request_id, None)

    @staticmethod
    def _configured_stock_contract(spec: InstrumentSpec) -> Contract:
        """Build a native-currency SMART contract with its primary exchange."""

        contract = Contract()
        contract.symbol = spec.symbol
        contract.secType = "STK"
        contract.exchange = spec.exchange
        if spec.primary_exchange:
            contract.primaryExchange = spec.primary_exchange
        contract.currency = spec.currency
        # An ISIN is the stable identifier for a European constituent. It
        # makes TWS reject a stale/incorrect ticker mapping rather than
        # silently resolving a similarly named share class or foreign listing.
        if spec.isin:
            contract.secIdType = "ISIN"
            contract.secId = spec.isin
        return contract

    def search_stock_contracts(self, query: str) -> list[Contract]:
        """Return IBKR's data-only stock matches for a company-name query."""

        normalized = query.strip()
        if not normalized:
            raise IBKRConnectionError("contract search query cannot be blank")
        request_id = self._request_id()
        event = Event()
        with self._lock:
            self._symbol_match_events[request_id] = event
            self._symbol_matches[request_id] = []
        try:
            self.reqMatchingSymbols(request_id, normalized)
            self._wait(event, f"contract search for {normalized}")
            with self._lock:
                matches = list(self._symbol_matches.pop(request_id, []))
            return [contract for contract in matches if contract.secType == "STK"]
        finally:
            with self._lock:
                self._symbol_match_events.pop(request_id, None)
                self._symbol_matches.pop(request_id, None)

    def nextValidId(self, orderId: int) -> None:  # noqa: N802 - IBKR callback name
        with self._lock:
            self._next_order_id = orderId
        self._ready.set()

    def managedAccounts(self, accountsList: str) -> None:  # noqa: N802
        self._accounts = [value for value in accountsList.split(",") if value]
        self._accounts_ready.set()

    def accountSummary(self, reqId: int, account: str, tag: str, value: str, currency: str) -> None:  # noqa: N802
        # TWS reports the account's base currency here, which is not
        # necessarily USD.  The dashboard labels monetary amounts with the
        # account base currency rather than discarding a valid non-USD row.
        if account == self.account_id:
            self._account_summary[tag] = value
            if currency:
                self._account_currency = currency.upper()

    def accountSummaryEnd(self, reqId: int) -> None:  # noqa: N802
        self._account_summary_ready.set()

    def tickPrice(self, reqId: int, tickType: int, price: float, attrib: Any) -> None:  # noqa: N802
        # 4 is LAST, 68 is DELAYED_LAST, and 75 is DELAYED_CLOSE. Outside a
        # market's session the delayed close is the best available last price.
        if tickType in {4, 68, 75} and price > 0:
            with self._lock:
                self._prices[reqId] = price
                event = self._price_events.get(reqId)
            if event is not None:
                event.set()
            return
        # FX pairs do not necessarily publish a last trade. For the explicit
        # currency-conversion path only, use a delayed bid/ask midpoint.
        if tickType in {1, 66} and price > 0:
            with self._lock:
                self._bid_prices[reqId] = price
                bid = self._bid_prices.get(reqId)
                ask = self._ask_prices.get(reqId)
                event = self._price_events.get(reqId)
                allow_mid = reqId in self._allow_bid_ask
        elif tickType in {2, 67} and price > 0:
            with self._lock:
                self._ask_prices[reqId] = price
                bid = self._bid_prices.get(reqId)
                ask = self._ask_prices.get(reqId)
                event = self._price_events.get(reqId)
                allow_mid = reqId in self._allow_bid_ask
        else:
            return
        if allow_mid and bid and ask:
            with self._lock:
                self._prices[reqId] = (bid + ask) / 2
            if event is not None:
                event.set()

    def contractDetails(self, reqId: int, contractDetails: Any) -> None:  # noqa: N802
        with self._lock:
            if reqId in self._contract_details:
                self._contract_details[reqId].append(contractDetails.contract)

    def contractDetailsEnd(self, reqId: int) -> None:  # noqa: N802
        with self._lock:
            event = self._contract_detail_events.get(reqId)
        if event is not None:
            event.set()

    def symbolSamples(self, reqId: int, contractDescriptions: list[Any]) -> None:  # noqa: N802
        with self._lock:
            if reqId not in self._symbol_matches:
                return
            self._symbol_matches[reqId].extend(description.contract for description in contractDescriptions)
            event = self._symbol_match_events.get(reqId)
        if event is not None:
            event.set()

    def realtimeBar(  # noqa: N802
        self,
        reqId: int,
        time_: int,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: float,
        wap: float,
        count: int,
    ) -> None:
        subscribed = self._bar_handlers.get(reqId)
        if subscribed is None:
            return
        symbol, handler = subscribed
        bar = LiveBar(symbol, datetime.fromtimestamp(time_, tz=timezone.utc), open_, high, low, close, volume)
        try:
            handler(bar)
        except Exception:
            LOGGER.exception("Strategy handler failed for IBKR bar %s", symbol)

    def updatePortfolio(  # noqa: N802
        self,
        contract: Contract,
        position: float,
        marketPrice: float,
        marketValue: float,
        averageCost: float,
        unrealizedPNL: float,
        realizedPNL: float,
        accountName: str,
    ) -> None:
        if accountName != self.account_id or not position:
            return
        self._portfolio.append(
            {
                "symbol": contract.symbol,
                "security_type": contract.secType,
                "currency": contract.currency,
                "exchange": contract.primaryExchange or contract.exchange,
                "quantity": position,
                "side": "long" if position > 0 else "short",
                "market_value": marketValue,
                "cost_basis": abs(position) * averageCost,
                "average_entry_price": averageCost,
                "current_price": marketPrice,
                "unrealized_pl": unrealizedPNL,
                "unrealized_plpc": unrealizedPNL / (abs(position) * averageCost) if averageCost and position else 0,
            }
        )

    def updateAccountValue(self, key: str, val: str, currency: str, accountName: str) -> None:  # noqa: N802
        """Capture the native cash balances sent with an account download."""

        if accountName != self.account_id or key != "TotalCashBalance" or not currency or currency == "BASE":
            return
        try:
            self._currency_cash_balances[currency.upper()] = float(val)
        except ValueError:
            LOGGER.debug("Ignoring non-numeric %s cash balance: %s", currency, val)

    def accountDownloadEnd(self, accountName: str) -> None:  # noqa: N802
        if accountName == self.account_id:
            self._portfolio_ready.set()

    def openOrder(self, orderId: int, contract: Contract, order: Order, orderState: Any) -> None:  # noqa: N802
        self._open_orders.append(
            {
                "submitted_at": None,
                "symbol": contract.symbol,
                "exchange": contract.primaryExchange or contract.exchange,
                "currency": contract.currency,
                "side": str(order.action).lower(),
                "status": str(orderState.status).lower(),
                "quantity": order.totalQuantity,
                "filled_quantity": None,
                "filled_avg_price": None,
                "type": str(order.orderType).lower(),
                "strategy": self._strategy_id(order.orderRef),
                "client_order_id": order.orderRef,
            }
        )

    def openOrderEnd(self) -> None:  # noqa: N802
        self._open_orders_ready.set()

    def orderStatus(  # noqa: N802
        self,
        orderId: int,
        status: str,
        filled: float,
        remaining: float,
        avgFillPrice: float,
        permId: int,
        parentId: int,
        lastFillPrice: float,
        clientId: int,
        whyHeld: str,
        mktCapPrice: float,
    ) -> None:
        terminal = {"Filled", "Cancelled", "ApiCancelled", "Inactive", "Rejected"}
        with self._lock:
            if orderId not in self._order_status_events:
                return
            self._order_statuses[orderId] = status
            event = self._order_status_events[orderId]
        if status in terminal:
            event.set()

    def execDetails(self, reqId: int, contract: Contract, execution: Any) -> None:  # noqa: N802
        order_ref = str(execution.orderRef or "")
        self._executions.append(
            {
                "execution_id": str(execution.execId),
                "submitted_at": execution.time,
                "symbol": contract.symbol,
                "exchange": contract.primaryExchange or contract.exchange,
                "currency": contract.currency,
                "side": str(execution.side).lower(),
                "status": "filled",
                "quantity": execution.shares,
                "filled_quantity": execution.shares,
                "filled_avg_price": execution.price,
                "type": "execution",
                "strategy": self._strategy_id(order_ref),
                "client_order_id": order_ref,
            }
        )

    def execDetailsEnd(self, reqId: int) -> None:  # noqa: N802
        self._executions_ready.set()

    def error(self, reqId: int, errorCode: int, errorString: str, advancedOrderRejectJson: str = "") -> None:  # noqa: N802
        # Connection, authentication, and request failures are actionable;
        # informational farm-status messages are left at debug level.
        message = f"IBKR {errorCode} (request {reqId}): {errorString}"
        if errorCode in {502, 503, 504}:
            self._fatal_error = message
            self._ready.set()
            LOGGER.error(message)
        # 10167 announces the expected switch to delayed data after
        # ``reqMarketDataType(3)``; a delayed tick follows it.
        elif errorCode not in {10167, 2100, 2104, 2106, 2108, 2158}:
            LOGGER.warning(message)
            with self._lock:
                event = self._price_events.get(reqId)
                contract_event = self._contract_detail_events.get(reqId)
                if event is not None:
                    self._request_errors[reqId] = message
                    event.set()
                if contract_event is not None:
                    self._request_errors[reqId] = message
                    contract_event.set()
                order_event = self._order_status_events.get(reqId)
                if order_event is not None:
                    self._order_statuses[reqId] = "Rejected"
                    order_event.set()
        else:
            LOGGER.debug(message)

    def _request_id(self) -> int:
        with self._lock:
            request_id = self._next_request_id
            self._next_request_id += 1
        return request_id

    @staticmethod
    def _strategy_id(order_ref: str | None) -> str:
        value = str(order_ref or "")
        if not value.startswith("nova-"):
            return "External / untagged"
        parts = value.split("-", 2)
        compact = parts[1] if len(parts) == 3 else "Nova"
        return {
            "Sp500YfinanceMomentu": "S&P 500 momentum reversal",
            "EuropeYfinanceMoment": "Europe momentum reversal",
            "PaperOrderSmokeTest": "Paper order smoke test",
            "DashboardPositionDem": "Dashboard position demo",
            "FxHedge": "FX hedge",
        }.get(compact, compact)

    @staticmethod
    def _wait(event: Event, description: str, timeout: float = 15.0) -> None:
        if not event.wait(timeout):
            raise IBKRConnectionError(f"Timed out waiting for IBKR {description}.")
