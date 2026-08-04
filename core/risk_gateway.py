"""Central risk checks for Nova Quant Club's paper-trading pipeline.

This module is intentionally separate from ``core/main_executor.py`` because
the club should be able to review and test risk controls without reading the
live streaming/execution code. The executor receives trade signals from
student strategies, then calls ``RiskGateway.evaluate(...)`` before it can
submit any order to Alpaca.

Important boundary:
    Strategies should never submit broker orders directly. They only return a
    plain signal such as ``{"symbol": "AAPL", "action": "BUY", "qty": 10}``.
    This gateway turns that signal into an approve/reject decision using the
    shared limits in ``config/risk_profile.json``.

Known bug fixed here:
    The draft plan estimated trade value with a hardcoded price. This file
    instead requires a price provider and fetches the latest market price before
    calculating trade notional.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol


# Single source of truth for risk limits. If the club changes max trade size,
# quantity limits, allowed symbols, etc., it should edit this JSON file instead
# of editing Python constants.
DEFAULT_RISK_PROFILE_PATH = Path(__file__).resolve().parents[1] / "config" / "risk_profile.json"


class RiskGatewayError(Exception):
    """Raised when the gateway cannot safely evaluate a trade signal."""


class PriceProvider(Protocol):
    """Minimal market-data interface used by the gateway.

    The real executor passes an Alpaca-backed adapter. Tests can pass a fake
    object with the same ``get_latest_trade(symbol)`` method, which lets us test
    risk rules without making network calls or needing Alpaca credentials.
    """

    def get_latest_trade(self, symbol: str) -> Any:
        """Return an object/dict containing the latest trade price."""


@dataclass(frozen=True)
class RiskDecision:
    """Gateway result for one proposed strategy signal.

    ``approved`` is the machine-readable allow/deny flag used by the executor.
    ``reason`` is meant for logs so club execs can understand rejected signals.
    ``trade_notional`` and ``latest_price`` are included when available to make
    review/debugging easier.
    """

    approved: bool
    reason: str
    trade_notional: Decimal | None = None
    latest_price: Decimal | None = None


class RiskGateway:
    """Central checkpoint for every outgoing paper-trading order."""

    def __init__(self, price_provider: PriceProvider, risk_profile_path: Path | str = DEFAULT_RISK_PROFILE_PATH) -> None:
        self.price_provider = price_provider
        self.risk_profile_path = Path(risk_profile_path)
        self.profile = self._load_profile()

    def evaluate(self, signal: dict[str, Any], account: Any) -> RiskDecision:
        """Return an approval decision for one proposed strategy signal.

        The executor calls this before order submission. The checks are ordered
        from cheap/local checks to external/account-dependent checks:
        1. validate the signal shape;
        2. enforce JSON config rules;
        3. fetch the real latest market price;
        4. compare trade notional and account buying power.
        """

        # Normalize early so every later check can rely on uppercase symbols,
        # uppercase actions, and Decimal quantities.
        normalized = self._normalize_signal(signal)

        # Hard stop for the club's current scope: this project is for simulated
        # Alpaca paper trading only.
        if not self.profile.get("paper_trading_only", True):
            return RiskDecision(False, "risk profile must enforce paper trading only")

        # Keep the active trading universe visible in config/risk_profile.json.
        # Stage 1 starts with AAPL only, but the club can expand this list later.
        if normalized["symbol"] not in set(self.profile["allowed_symbols"]):
            return RiskDecision(False, f"symbol {normalized['symbol']} is not in allowed_symbols")

        # Actions are also config-driven. If the club wants long-only behavior,
        # remove SELL from allowed_actions instead of editing Python code.
        if normalized["action"] not in set(self.profile["allowed_actions"]):
            return RiskDecision(False, f"action {normalized['action']} is not in allowed_actions")

        min_qty = Decimal(str(self.profile["min_order_quantity"]))
        max_qty = Decimal(str(self.profile["max_order_quantity"]))
        qty = normalized["qty"]

        if qty < min_qty:
            return RiskDecision(False, f"quantity {qty} is below min_order_quantity {min_qty}")

        if qty > max_qty:
            return RiskDecision(False, f"quantity {qty} exceeds max_order_quantity {max_qty}")

        if self.profile.get("require_whole_shares", True) and qty != qty.to_integral_value():
            return RiskDecision(False, "fractional share orders are disabled by risk_profile.json")

        # Critical risk check: use the current market price from Alpaca data, not
        # a hardcoded or strategy-provided price.
        latest_price = self._latest_price(normalized["symbol"])
        trade_notional = qty * latest_price
        max_notional = Decimal(str(self.profile["max_trade_notional_usd"]))

        if trade_notional > max_notional:
            return RiskDecision(
                False,
                f"trade notional {trade_notional:.2f} exceeds max_trade_notional_usd {max_notional:.2f}",
                trade_notional=trade_notional,
                latest_price=latest_price,
            )

        # For BUY orders, confirm the paper account has enough buying power. SELL
        # handling is intentionally simple in Stage 1; position-aware sell checks
        # can be added in a later risk-profile expansion.
        buying_power = self._account_buying_power(account)
        if normalized["action"] == "BUY" and trade_notional > buying_power:
            return RiskDecision(
                False,
                f"trade notional {trade_notional:.2f} exceeds buying power {buying_power:.2f}",
                trade_notional=trade_notional,
                latest_price=latest_price,
            )

        return RiskDecision(True, "approved", trade_notional=trade_notional, latest_price=latest_price)

    def approve(self, signal: dict[str, Any], account: Any) -> bool:
        """Convenience wrapper for callers that only need a boolean."""

        return self.evaluate(signal, account).approved

    def _load_profile(self) -> dict[str, Any]:
        """Load and lightly validate ``config/risk_profile.json``.

        JSON does not support real comments, so human-facing explanations live
        in README.md and config/README.md. This method enforces that the
        machine-readable file still contains every key the gateway depends on.
        """

        if not self.risk_profile_path.exists():
            raise RiskGatewayError(f"risk profile not found: {self.risk_profile_path}")

        with self.risk_profile_path.open("r", encoding="utf-8") as file:
            profile = json.load(file)

        required_keys = {
            "paper_trading_only",
            "allowed_symbols",
            "allowed_actions",
            "max_trade_notional_usd",
            "max_order_quantity",
            "min_order_quantity",
            "require_whole_shares",
        }
        missing = sorted(required_keys - set(profile))
        if missing:
            raise RiskGatewayError(f"risk profile missing required keys: {', '.join(missing)}")

        return profile

    def _normalize_signal(self, signal: dict[str, Any]) -> dict[str, Any]:
        """Validate the signal contract used by every strategy.

        All student strategies should return either ``None`` or a dict with:
        ``symbol`` (ticker), ``action`` ("BUY" or "SELL"), and ``qty`` (shares).
        """

        try:
            symbol = str(signal["symbol"]).upper().strip()
            action = str(signal["action"]).upper().strip()
            qty = Decimal(str(signal["qty"]))
        except KeyError as exc:
            raise RiskGatewayError(f"signal missing required field: {exc.args[0]}") from exc
        except (InvalidOperation, ValueError) as exc:
            raise RiskGatewayError("signal quantity must be numeric") from exc

        if not symbol:
            raise RiskGatewayError("signal symbol cannot be blank")
        if qty <= 0:
            raise RiskGatewayError("signal quantity must be positive")

        return {"symbol": symbol, "action": action, "qty": qty}

    def _latest_price(self, symbol: str) -> Decimal:
        """Fetch and validate the current market price for risk math."""

        latest_trade = self.price_provider.get_latest_trade(symbol)
        raw_price = getattr(latest_trade, "price", None)
        if raw_price is None and isinstance(latest_trade, dict):
            raw_price = latest_trade.get("price")
        if raw_price is None:
            raise RiskGatewayError(f"latest trade for {symbol} did not include a price")

        try:
            price = Decimal(str(raw_price))
        except (InvalidOperation, ValueError) as exc:
            raise RiskGatewayError(f"latest price for {symbol} is not numeric") from exc

        if price <= 0:
            raise RiskGatewayError(f"latest price for {symbol} must be positive")
        return price

    def _account_buying_power(self, account: Any) -> Decimal:
        """Extract buying power from Alpaca's account object.

        Alpaca account objects can expose ``daytrading_buying_power`` and
        ``buying_power``. The dict fallback keeps Stage 2 unit tests simple.
        """

        raw_buying_power = getattr(account, "daytrading_buying_power", None) or getattr(account, "buying_power", None)
        if raw_buying_power is None and isinstance(account, dict):
            raw_buying_power = account.get("daytrading_buying_power") or account.get("buying_power")
        if raw_buying_power is None:
            raise RiskGatewayError("account did not include buying_power or daytrading_buying_power")

        try:
            buying_power = Decimal(str(raw_buying_power))
        except (InvalidOperation, ValueError) as exc:
            raise RiskGatewayError("account buying power is not numeric") from exc

        if buying_power < 0:
            raise RiskGatewayError("account buying power cannot be negative")
        return buying_power
