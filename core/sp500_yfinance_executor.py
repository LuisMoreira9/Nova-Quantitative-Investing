"""Personal-use yfinance S&P 500 scanner with IBKR paper execution.

Yahoo data creates candidates; IBKR remains the contract/risk/execution source.
This process is deliberately capped at 25 new paper orders per scan and one
share per symbol while it builds toward a 100-position learning portfolio.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import time
from io import StringIO
import json

import pandas as pd
import requests
import yfinance as yf

from core.ibkr_adapter import IBKRClient
from core.main_executor import ibkr_connection_settings, is_us_equity_regular_session, load_local_env, route_signal
from core.risk_gateway import RiskGateway
from core.yahoo_price_provider import YahooFxPriceProvider


LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
UNIVERSE_CACHE = ROOT / "data" / "sp500_universe_symbols.txt"
SP500_SOURCE = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
OWNERSHIP_STATE_PATH = ROOT / "data" / "sp500_long_paper_state.json"
ORDER_REF_PREFIX = "nova-Sp500YfinanceMomentu-"


def _load_owned_quantities() -> dict[str, int] | None:
    """Load this executor's own long sleeve, never adopt arbitrary holdings."""

    if not OWNERSHIP_STATE_PATH.exists():
        return None
    try:
        payload = json.loads(OWNERSHIP_STATE_PATH.read_text(encoding="utf-8"))
        quantities = payload.get("owned_quantities", {})
        return {
            str(symbol).upper(): int(quantity)
            for symbol, quantity in quantities.items()
            if int(quantity) > 0
        }
    except (OSError, ValueError, TypeError, AttributeError):
        LOGGER.warning("Could not read S&P ownership state; refusing to adopt existing long positions.")
        return {}


def _save_owned_quantities(owned_quantities: dict[str, int]) -> None:
    OWNERSHIP_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"owned_quantities": {symbol: quantity for symbol, quantity in sorted(owned_quantities.items()) if quantity > 0}}
    OWNERSHIP_STATE_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _rebuild_owned_quantities(executions: list[dict], universe: set[str]) -> dict[str, int]:
    """Seed ownership from this session's Nova-tagged S&P fills only."""

    quantities: dict[str, int] = {}
    for row in executions:
        if not str(row.get("client_order_id") or "").startswith(ORDER_REF_PREFIX):
            continue
        symbol = str(row.get("symbol") or "").upper()
        if symbol not in universe:
            continue
        quantity = int(float(row.get("filled_quantity", row.get("quantity", 0))))
        if quantity <= 0:
            continue
        signed = quantity if str(row.get("side") or "").lower() in {"bot", "buy"} else -quantity
        quantities[symbol] = quantities.get(symbol, 0) + signed
    return {symbol: quantity for symbol, quantity in quantities.items() if quantity > 0}


def load_universe() -> list[str]:
    """Fetch current constituents once per process and persist only symbols."""

    response = requests.get(SP500_SOURCE, headers={"User-Agent": "Mozilla/5.0 (Nova student-club research)"}, timeout=30)
    response.raise_for_status()
    tables = pd.read_html(StringIO(response.text))
    table = next(frame for frame in tables if "Symbol" in frame.columns)
    symbols = sorted({str(value).upper().replace(".", "-") for value in table["Symbol"].dropna()})
    if len(symbols) < 450:
        raise RuntimeError(f"unexpected S&P 500 universe size: {len(symbols)}")
    UNIVERSE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    UNIVERSE_CACHE.write_text("\n".join(symbols) + "\n", encoding="utf-8")
    return symbols


def fetch_closes(symbols: list[str]) -> dict[str, pd.Series]:
    """Download one-minute closes in five Yahoo batches, not 500 calls."""

    result: dict[str, pd.Series] = {}
    for start in range(0, len(symbols), 50):
        batch = symbols[start : start + 50]
        # Avoid yfinance's local-cache lock contention by downloading in
        # batches. The executor controls the one-minute scan cadence.
        frame = yf.download(
            batch,
            period="1d",
            interval="1m",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=False,
            timeout=15,
        )
        if frame.empty:
            continue
        for symbol in batch:
            try:
                close = frame[symbol]["Close"] if isinstance(frame.columns, pd.MultiIndex) else frame["Close"]
                close = close.dropna()
                if len(close) >= 6:
                    result[symbol] = close
            except (KeyError, TypeError):
                continue
    return result


def candidates(closes: dict[str, pd.Series], held: set[str], max_orders: int, threshold: float = 0.00001, currency: str = "USD") -> list[dict]:
    """Return rebound buys and reversal sells, ranked by latest momentum."""

    buys: list[tuple[float, dict]] = []
    sells: list[tuple[float, dict]] = []
    for symbol, series in closes.items():
        values = series.iloc[-6:]
        older = float(values.iloc[2] / values.iloc[0] - 1)
        recent = float(values.iloc[-1] / values.iloc[-3] - 1)
        reference_price = float(values.iloc[-1])
        reference_timestamp = values.index[-1].isoformat()
        context = {"reference_price": reference_price, "reference_currency": currency, "reference_timestamp": reference_timestamp}
        if symbol in held and older >= threshold and recent <= -threshold:
            sells.append((abs(recent), {"symbol": symbol, "action": "SELL", "qty": 1, **context}))
        elif symbol not in held and older <= -threshold and recent >= threshold:
            buys.append((recent, {"symbol": symbol, "action": "BUY", "qty": 1, **context}))
    ranked = [signal for _, signal in sorted(sells, key=lambda item: item[0], reverse=True)]
    ranked += [signal for _, signal in sorted(buys, key=lambda item: item[0], reverse=True)]
    return ranked[:max_orders]


def main() -> None:
    load_local_env()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    universe = load_universe()
    max_positions = int(os.getenv("NOVA_SP500_MAX_POSITIONS", "100"))
    max_orders = int(os.getenv("NOVA_SP500_MAX_NEW_ORDERS_PER_CYCLE", "25"))
    # This intentionally small threshold is for observing the end-to-end
    # paper-trading architecture, not for claiming a viable trading signal.
    threshold = float(os.getenv("NOVA_SP500_REVERSAL_THRESHOLD", "0.00001"))
    scan_seconds = max(60, int(os.getenv("NOVA_SP500_SCAN_SECONDS", "60")))
    host, port, client_id = ibkr_connection_settings()
    broker = IBKRClient(host, port, client_id + 20)
    broker.connect_and_start()
    risk = RiskGateway(YahooFxPriceProvider())
    universe_set = set(universe)
    owned_quantities = _load_owned_quantities()
    if owned_quantities is None:
        owned_quantities = _rebuild_owned_quantities(broker.get_today_executions(), universe_set)
        _save_owned_quantities(owned_quantities)
    try:
        LOGGER.warning(
            "yfinance S&P 500 paper scanner started: %s names, %s-position cap, %s orders/cycle, %.4f%% reversal threshold.",
            len(universe),
            max_positions,
            max_orders,
            threshold * 100,
        )
        while broker.isConnected():
            if not is_us_equity_regular_session():
                time.sleep(60)
                continue
            portfolio = broker.get_portfolio()
            signed_positions = {
                row["symbol"].upper(): float(row["quantity"])
                for row in portfolio
                if row["symbol"].upper() in universe_set
            }
            # This is a long-only, owned-sleeve scanner. A positive position
            # is sellable only when this executor's own fill ledger says it
            # owns at least one share. Any manual or other-strategy position
            # (long or short) remains outside its candidate universe.
            held = {
                symbol
                for symbol, quantity in signed_positions.items()
                if quantity > 0 and owned_quantities.get(symbol, 0) > 0
            }
            occupied_elsewhere = {
                symbol
                for symbol, quantity in signed_positions.items()
                if quantity != 0 and symbol not in held
            }
            eligible_closes = {
                symbol: series
                for symbol, series in fetch_closes(universe).items()
                if symbol not in occupied_elsewhere
            }
            signals = candidates(eligible_closes, held, max_orders, threshold=threshold)
            for signal in signals:
                if signal["action"] == "BUY" and len(held) >= max_positions:
                    continue
                try:
                    broker.verify_instruments([signal["symbol"]])
                    if route_signal(signal, broker, risk, "Sp500YfinanceMomentumReversal"):
                        if signal["action"] == "BUY":
                            owned_quantities[signal["symbol"]] = owned_quantities.get(signal["symbol"], 0) + int(signal["qty"])
                            held.add(signal["symbol"])
                        else:
                            remaining = owned_quantities.get(signal["symbol"], 0) - int(signal["qty"])
                            if remaining > 0:
                                owned_quantities[signal["symbol"]] = remaining
                            else:
                                owned_quantities.pop(signal["symbol"], None)
                                held.discard(signal["symbol"])
                        _save_owned_quantities(owned_quantities)
                except Exception:
                    LOGGER.exception("Could not process S&P 500 candidate %s", signal["symbol"])
            time.sleep(scan_seconds)
    except KeyboardInterrupt:
        LOGGER.info("Stopping yfinance S&P 500 paper scanner")
    finally:
        broker.close()


if __name__ == "__main__":
    main()
