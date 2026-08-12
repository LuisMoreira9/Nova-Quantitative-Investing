"""Opt-in, capped S&P 500 short/cover paper-trading demonstration.

This is deliberately separate from the long-only S&P 500 scanner.  It is for
learning how TWS represents a short position and the later BUY-to-cover order;
it is not an investment recommendation or a production short-selling model.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import time
import csv

from core.ibkr_adapter import IBKRClient
from core.main_executor import ibkr_connection_settings, is_us_equity_regular_session, load_local_env, route_signal
from core.risk_gateway import RiskGateway
from core.sp500_yfinance_executor import fetch_closes
from core.yahoo_price_provider import YahooFxPriceProvider


LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data" / "sp500_short_paper_state.json"
EXECUTION_LEDGER_PATH = ROOT / "data" / "nova_strategy_execution_ledger.csv"
DEFAULT_UNIVERSE = ("AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL")
ORDER_REF_PREFIX = "nova-Sp500ShortMomentumRe-"


def _load_managed_shorts() -> set[str] | None:
    """Load only shorts previously opened by this executor, never manual ones."""

    if not STATE_PATH.exists():
        return None
    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return {str(symbol).upper() for symbol in payload.get("managed_short_symbols", [])}
    except (OSError, ValueError, TypeError):
        LOGGER.warning("Could not read short-strategy state; rebuilding only from Nova-tagged executions.")
        return None


def _save_managed_shorts(symbols: set[str]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps({"managed_short_symbols": sorted(symbols)}, indent=2) + "\n",
        encoding="utf-8",
    )


def _rebuild_managed_shorts(executions: list[dict], universe: set[str]) -> set[str]:
    """Recover only the short sleeve created by this strategy's tagged fills.

    The local state file is the fast path.  If it is absent, replay the durable
    Nova execution ledger first, then add any newer same-session TWS executions
    not yet observed by the dashboard.  It never adopts a manual or another
    strategy's short simply because the account has a negative position.
    """

    net: dict[str, float] = {}
    seen_execution_ids: set[str] = set()

    def apply(row: dict) -> None:
        reference = str(row.get("order_ref", row.get("client_order_id", "")) or "")
        if not reference.startswith(ORDER_REF_PREFIX):
            return
        symbol = str(row.get("symbol") or "").upper()
        if symbol not in universe:
            return
        try:
            quantity = float(row.get("quantity", row.get("filled_quantity", 0)))
        except (TypeError, ValueError):
            return
        if quantity <= 0:
            return
        side = str(row.get("side") or "").lower()
        net[symbol] = net.get(symbol, 0.0) + (quantity if side in {"sld", "sell"} else -quantity)

    if EXECUTION_LEDGER_PATH.exists():
        try:
            with EXECUTION_LEDGER_PATH.open("r", newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    seen_execution_ids.add(str(row.get("execution_id") or ""))
                    apply(row)
        except OSError:
            LOGGER.warning("Could not read Nova execution ledger while rebuilding short ownership.")
    for row in executions:
        execution_id = str(row.get("execution_id") or "")
        if execution_id and execution_id in seen_execution_ids:
            continue
        apply(row)
    return {symbol for symbol, quantity in net.items() if quantity > 0}


def _to_base(value: float, currency: str, risk: RiskGateway) -> float:
    """Convert a native exposure using the same external FX provider as risk."""

    base_currency = str(risk.profile["base_currency"]).upper()
    denomination = str(currency or base_currency).upper()
    if denomination == base_currency:
        return value
    return value * float(risk.price_provider.get_fx_rate_to_base(denomination, base_currency))


def short_open_allowed(
    signal: dict,
    portfolio: list[dict],
    executions: list[dict],
    account: dict,
    risk: RiskGateway,
) -> tuple[bool, str]:
    """Apply aggregate short/margin limits without asking IBKR for quotes."""

    profile = risk.profile
    existing_shorts = [row for row in portfolio if float(row.get("quantity", 0)) < 0]
    if len(existing_shorts) >= int(profile.get("max_portfolio_short_positions", 0)):
        return False, "portfolio short-position cap reached"
    try:
        gross_existing = sum(
            _to_base(abs(float(row["quantity"])) * float(row["current_price"]), str(row.get("currency") or ""), risk)
            for row in existing_shorts
        )
        proposed = _to_base(
            float(signal["qty"]) * float(signal["reference_price"]) * (1 + float(profile.get("slippage_buffer_pct", 0))),
            str(signal.get("reference_currency") or ""),
            risk,
        )
        max_gross = float(profile.get("max_gross_short_notional_base_currency", 0))
        if max_gross <= 0 or gross_existing + proposed > max_gross:
            return False, "gross short-notional cap would be exceeded"
        short_opens = [
            row for row in executions
            if str(row.get("client_order_id") or "").startswith(ORDER_REF_PREFIX)
            and str(row.get("side") or "").lower() in {"sld", "sell"}
        ]
        if len(short_opens) >= int(profile.get("max_daily_short_open_orders", 0)):
            return False, "daily short-opening order cap reached"
        daily_notional = sum(
            _to_base(
                float(row.get("filled_quantity", row.get("quantity", 0))) * float(row.get("filled_avg_price", 0)),
                str(row.get("currency") or ""),
                risk,
            )
            for row in short_opens
        )
        if daily_notional + proposed > float(profile.get("max_daily_short_open_notional_base_currency", 0)):
            return False, "daily short-opening notional cap would be exceeded"
        buying_power = float(account.get("buying_power", 0))
        if buying_power < float(profile.get("min_short_buying_power_buffer_base_currency", 0)):
            return False, "buying-power buffer is below the short-opening minimum"
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return False, f"could not calculate aggregate short risk: {exc}"
    return True, "approved"


def short_and_cover_signals(
    closes: dict,
    positions: dict[str, float],
    managed_shorts: set[str],
    threshold: float,
    max_new_shorts: int,
) -> list[dict]:
    """Open bearish reversals and cover only short exposure this runtime owns."""

    covers: list[tuple[float, dict]] = []
    opens: list[tuple[float, dict]] = []
    for symbol, series in closes.items():
        values = series.iloc[-6:]
        older = float(values.iloc[2] / values.iloc[0] - 1)
        recent = float(values.iloc[-1] / values.iloc[-3] - 1)
        price = float(values.iloc[-1])
        timestamp = values.index[-1].isoformat()
        context = {
            "symbol": symbol,
            "qty": 1,
            "reference_price": price,
            "reference_currency": "USD",
            "reference_timestamp": timestamp,
        }
        quantity = float(positions.get(symbol, 0.0))
        # Cover only a short this executor opened. A rebound after a decline is
        # intentionally a very loose paper-demo exit condition.
        if symbol in managed_shorts and quantity < 0 and older <= -threshold and recent >= threshold:
            covers.append((recent, {**context, "action": "BUY", "position_effect": "CLOSE"}))
        # Never sell against an existing long or adopt an unrelated short.
        elif symbol not in managed_shorts and quantity == 0 and older >= threshold and recent <= -threshold:
            opens.append((abs(recent), {**context, "action": "SELL", "position_effect": "OPEN"}))
    ranked = [signal for _, signal in sorted(covers, key=lambda item: item[0], reverse=True)]
    ranked += [signal for _, signal in sorted(opens, key=lambda item: item[0], reverse=True)[:max_new_shorts]]
    return ranked


def main() -> None:
    load_local_env()
    if os.getenv("NOVA_SHORT_SELLING_PAPER_TEST") != "CONFIRM":
        raise RuntimeError(
            "Short paper trading is disabled. Add NOVA_SHORT_SELLING_PAPER_TEST=CONFIRM to the ignored .env after reviewing the limits."
        )
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    universe = tuple(
        symbol.strip().upper()
        for symbol in os.getenv("NOVA_SHORT_SELLING_UNIVERSE", ",".join(DEFAULT_UNIVERSE)).split(",")
        if symbol.strip()
    )
    if not universe:
        raise ValueError("NOVA_SHORT_SELLING_UNIVERSE cannot be empty")
    threshold = float(os.getenv("NOVA_SHORT_SELLING_REVERSAL_THRESHOLD", "0.0001"))
    max_positions = max(1, int(os.getenv("NOVA_SHORT_SELLING_MAX_POSITIONS", "3")))
    scan_seconds = max(60, int(os.getenv("NOVA_SHORT_SELLING_SCAN_SECONDS", "60")))
    host, port, client_id = ibkr_connection_settings()
    broker = IBKRClient(host, port, client_id + 35)
    broker.connect_and_start()
    risk = RiskGateway(YahooFxPriceProvider())
    managed_shorts = _load_managed_shorts()
    if managed_shorts is None:
        managed_shorts = _rebuild_managed_shorts(broker.get_today_executions(), set(universe))
        _save_managed_shorts(managed_shorts)
    try:
        broker.verify_instruments(list(universe))
        LOGGER.warning(
            "Short paper demo started: %s; max %s owned one-share shorts; threshold %.4f%%.",
            ", ".join(universe), max_positions, threshold * 100,
        )
        while broker.isConnected():
            if not is_us_equity_regular_session():
                time.sleep(60)
                continue
            portfolio = broker.get_portfolio()
            positions = {row["symbol"].upper(): float(row["quantity"]) for row in portfolio}
            executions = broker.get_today_executions()
            closes = fetch_closes(list(universe))
            short_count = sum(1 for symbol in managed_shorts if positions.get(symbol, 0.0) < 0)
            signals = short_and_cover_signals(
                closes, positions, managed_shorts, threshold, max(0, max_positions - short_count)
            )
            LOGGER.info("Short-paper scan: %s quotes, %s owned shorts, %s eligible orders.", len(closes), short_count, len(signals))
            for signal in signals:
                if signal["position_effect"] == "OPEN" and short_count >= max_positions:
                    continue
                try:
                    if signal["position_effect"] == "OPEN":
                        allowed, reason = short_open_allowed(signal, portfolio, executions, broker.get_account(), risk)
                        if not allowed:
                            LOGGER.warning("Blocked short opening for %s: %s", signal["symbol"], reason)
                            continue
                    if route_signal(signal, broker, risk, "Sp500ShortMomentumReversal"):
                        symbol = signal["symbol"]
                        if signal["position_effect"] == "OPEN":
                            managed_shorts.add(symbol)
                            short_count += 1
                        else:
                            managed_shorts.discard(symbol)
                        _save_managed_shorts(managed_shorts)
                except Exception:
                    LOGGER.exception("Could not process short-paper candidate %s", signal["symbol"])
            time.sleep(scan_seconds)
    except KeyboardInterrupt:
        LOGGER.info("Stopping S&P 500 short paper demo")
    finally:
        broker.close()


if __name__ == "__main__":
    main()
