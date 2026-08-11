"""Paper-only FX hedge executor for Nova's net foreign-currency exposure.

The executor owns only the FX orders it records in its ignored local state
file. It uses external Yahoo FX conversions for sizing and never requests an
IBKR market-data quote.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import time
from typing import Any

from core.ibkr_adapter import IBKRClient
from core.main_executor import ibkr_connection_settings, load_local_env
from core.yahoo_price_provider import YahooFxPriceProvider


LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "config" / "fx_hedge_profile.json"
class FxHedgeExecutionError(RuntimeError):
    """A broker rejected a hedge order; operator review is required."""


def load_profile() -> dict[str, Any]:
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    required = {"paper_trading_only", "base_currency", "hedged_currencies", "rebalance_threshold_base_currency", "max_order_base_currency"}
    missing = required - set(profile)
    if missing:
        raise ValueError(f"FX hedge profile missing: {', '.join(sorted(missing))}")
    if not profile["paper_trading_only"]:
        raise ValueError("FX hedge profile must remain paper-trading-only")
    return profile


def foreign_stock_exposure(portfolio: list[dict[str, Any]], currency: str) -> float:
    """Return net native-currency exposure from stock positions only."""

    return sum(
        float(row.get("market_value", 0) or 0)
        for row in portfolio
        if str(row.get("security_type", "STK")).upper() == "STK"
        and str(row.get("currency", "")).upper() == currency
    )


def rebalance_once(broker: IBKRClient, profile: dict[str, Any], provider: YahooFxPriceProvider) -> None:
    base = str(profile["base_currency"]).upper()
    threshold = float(profile["rebalance_threshold_base_currency"])
    max_order = int(profile["max_order_base_currency"])
    portfolio, cash_balances = broker.get_portfolio_and_cash()
    for foreign in [str(value).upper() for value in profile["hedged_currencies"]]:
        if foreign == base:
            continue
        pair_key = f"{base}.{foreign}"
        stock_exposure = foreign_stock_exposure(portfolio, foreign)
        cash_balance = float(cash_balances.get(foreign, 0.0))
        native_exposure = stock_exposure + cash_balance
        fx_to_base = provider.get_fx_rate_to_base(foreign, base)
        target_base_units = int(round(native_exposure * fx_to_base))
        if abs(target_base_units) < threshold:
            LOGGER.info(
                "FX hedge %s is within threshold: stock=%.2f, cash=%.2f, net=%.2f %s.",
                pair_key,
                stock_exposure,
                cash_balance,
                native_exposure,
                foreign,
            )
            continue
        quantity = min(abs(target_base_units), max_order)
        action = "BUY" if target_base_units > 0 else "SELL"
        LOGGER.warning(
            "FX hedge rebalance: %s %s %s (stock=%.2f, cash=%.2f, net=%.2f %s).",
            action,
            quantity,
            pair_key,
            stock_exposure,
            cash_balance,
            native_exposure,
            foreign,
        )
        order_id = broker.place_fx_market_order(base, foreign, quantity, action, "FxHedgeRebalance")
        status = broker.wait_for_terminal_order(order_id)
        if status != "Filled":
            raise FxHedgeExecutionError(
                f"IBKR paper FX order {order_id} ended as {status}; automatic retries are disabled"
            )
        LOGGER.info("IBKR paper FX hedge filled: %s %s %s.", action, quantity, pair_key)


def main() -> None:
    load_local_env()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    if os.getenv("NOVA_FX_HEDGE_ENABLED", "").upper() != "CONFIRM":
        raise RuntimeError("FX hedge is disabled. Set NOVA_FX_HEDGE_ENABLED=CONFIRM in .env for paper-only execution.")
    profile = load_profile()
    interval = max(60, int(os.getenv("NOVA_FX_HEDGE_REBALANCE_SECONDS", "60")))
    host, port, client_id = ibkr_connection_settings()
    broker = IBKRClient(host, port, client_id + 40)
    broker.connect_and_start()
    provider = YahooFxPriceProvider()
    try:
        LOGGER.warning("Paper-only FX hedge executor started; interval=%ss.", interval)
        while broker.isConnected():
            try:
                rebalance_once(broker, profile, provider)
            except FxHedgeExecutionError:
                LOGGER.exception("FX hedge executor stopped after a broker rejection. Review TWS account permissions/margin before restarting.")
                break
            except Exception:
                LOGGER.exception("FX hedge rebalance failed; it will retry on the next interval.")
            time.sleep(interval)
    except KeyboardInterrupt:
        LOGGER.info("Stopping paper FX hedge executor")
    finally:
        broker.close()


if __name__ == "__main__":
    main()
