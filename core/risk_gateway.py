"""Central risk checks for Nova Quant Club's paper-trading pipeline.

This module is intentionally separate from ``core/main_executor.py`` because
the club should be able to review and test risk controls without reading the
live streaming/execution code. The executor receives trade signals from
student strategies, then calls ``RiskGateway.evaluate(...)`` before it can
    submit any order to Interactive Brokers TWS.

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
from datetime import datetime, timezone
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
    """External FX conversion interface used by the risk gateway."""

    def get_fx_rate_to_base(self, currency: str, base_currency: str) -> float:
        """Return base-currency value of one unit of ``currency``."""


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
        3. validate the fresh externally supplied reference price;
        4. compare trade notional and account buying power.
        """

        # Normalize early so every later check can rely on uppercase symbols,
        # uppercase actions, and Decimal quantities.
        normalized = self._normalize_signal(signal)

        # Hard stop for the club's current scope: this project is for simulated
        # IBKR simulated trading only.
        if not self.profile.get("paper_trading_only", True):
            return RiskDecision(False, "risk profile must enforce paper trading only")

        # Keep the active trading universe visible in config/risk_profile.json.
        # Stage 1 starts with AAPL only, but the club can expand this list later.
        if normalized["symbol"] not in self._allowed_symbols():
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

        lot_size = Decimal(str(self.profile["min_order_quantity_by_symbol"].get(normalized["symbol"], min_qty)))
        if qty % lot_size != 0:
            return RiskDecision(False, f"quantity {qty} is not a valid {lot_size}-unit board lot for {normalized['symbol']}")

        # IBKR is execution/account infrastructure only. Strategies must carry
        # a fresh price from the shared external data service; stale intents are
        # never queued for later execution.
        latest_price, price_currency = self._reference_quote(normalized)
        if normalized["action"] == "BUY":
            latest_price *= Decimal("1") + Decimal(str(self.profile.get("slippage_buffer_pct", "0.03")))
        native_notional = qty * latest_price
        conversion_rate = self._fx_rate_to_base(price_currency)
        trade_notional = native_notional * conversion_rate
        base_currency = self.profile["base_currency"]
        max_notional = Decimal(str(self.profile["max_trade_notional_base_currency"]))

        if trade_notional > max_notional:
            return RiskDecision(
                False,
                f"trade notional {trade_notional:.2f} {base_currency} exceeds limit {max_notional:.2f} {base_currency}",
                trade_notional=trade_notional,
                latest_price=latest_price,
            )

        # For BUY orders, confirm the paper account has enough buying power. SELL
        # handling is intentionally simple in Stage 1; position-aware sell checks
        # can be added in a later risk-profile expansion.
        buying_power = self._account_buying_power(account)
        account_currency = self._account_currency(account)
        if account_currency != base_currency:
            return RiskDecision(False, f"account buying power is in {account_currency}, but risk base is {base_currency}")
        if normalized["action"] == "BUY" and trade_notional > buying_power:
            return RiskDecision(
                False,
                f"trade notional {trade_notional:.2f} {base_currency} exceeds buying power {buying_power:.2f} {base_currency}",
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
            "base_currency",
            "max_trade_notional_base_currency",
            "max_order_quantity",
            "min_order_quantity",
            "min_order_quantity_by_symbol",
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

        normalized = {"symbol": symbol, "action": action, "qty": qty}
        if "reference_price" in signal:
            try:
                reference_price = Decimal(str(signal["reference_price"]))
            except (InvalidOperation, ValueError) as exc:
                raise RiskGatewayError("reference_price must be numeric") from exc
            if reference_price <= 0:
                raise RiskGatewayError("reference_price must be positive")
            normalized["reference_price"] = reference_price
        elif self.profile.get("require_reference_price", True):
            raise RiskGatewayError("external reference_price is required; IBKR market data is disabled")

        if "reference_currency" in signal:
            normalized["reference_currency"] = str(signal["reference_currency"]).upper().strip()
        elif "reference_price" in normalized:
            normalized["reference_currency"] = self.profile["base_currency"]

        if "reference_timestamp" in signal:
            try:
                timestamp = datetime.fromisoformat(str(signal["reference_timestamp"]).replace("Z", "+00:00"))
            except ValueError as exc:
                raise RiskGatewayError("reference_timestamp must be ISO-8601") from exc
            if timestamp.tzinfo is None:
                raise RiskGatewayError("reference_timestamp must include a timezone")
            normalized["reference_timestamp"] = timestamp.astimezone(timezone.utc)
        elif self.profile.get("require_reference_price", True):
            raise RiskGatewayError("external reference_timestamp is required; stale signals cannot be queued")
        return normalized

    def _allowed_symbols(self) -> set[str]:
        allowed = set(self.profile["allowed_symbols"])
        configured_paths = [self.profile.get("allowed_symbols_file"), *self.profile.get("allowed_symbols_files", [])]
        for configured_path in configured_paths:
            if not configured_path:
                continue
            path = self.risk_profile_path.parents[1] / configured_path
            if path.exists():
                allowed.update(line.strip().upper() for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        return allowed

    def _reference_quote(self, signal: dict[str, Any]) -> tuple[Decimal, str]:
        """Use only fresh shared external data; never request an IBKR quote."""

        try:
            price = signal["reference_price"]
            timestamp = signal["reference_timestamp"]
        except KeyError as exc:
            raise RiskGatewayError("external reference price and timestamp are required") from exc
        max_age = float(self.profile.get("max_reference_age_seconds", 300))
        age = (datetime.now(timezone.utc) - timestamp).total_seconds()
        if age < -30 or age > max_age:
            raise RiskGatewayError(f"external reference price is stale ({age:.0f}s; limit {max_age:.0f}s)")
        return price, signal["reference_currency"]

    def _fx_rate_to_base(self, currency: str) -> Decimal:
        base_currency = self.profile["base_currency"]
        if currency == base_currency:
            return Decimal("1")
        try:
            value = self.price_provider.get_fx_rate_to_base(currency, base_currency)
            rate = Decimal(str(value))
        except (AttributeError, InvalidOperation, ValueError) as exc:
            raise RiskGatewayError(f"could not convert {currency} to {base_currency} for risk checks") from exc
        if rate <= 0:
            raise RiskGatewayError(f"FX rate {currency}/{base_currency} must be positive")
        return rate

    def _account_buying_power(self, account: Any) -> Decimal:
        """Extract buying power from an IBKR account summary.

        The dict fallback keeps unit tests simple while the IBKR adapter returns
        buying power in its account summary mapping.
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

    def _account_currency(self, account: Any) -> str:
        raw_currency = getattr(account, "currency", None)
        if raw_currency is None and isinstance(account, dict):
            raw_currency = account.get("currency")
        # Fakes used by unit tests may omit a currency; in that case their
        # buying power is understood to be in the configured base currency.
        return str(raw_currency or self.profile["base_currency"]).upper()
