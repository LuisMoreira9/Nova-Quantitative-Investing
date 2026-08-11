"""Deliberate two-leg paper-order test; it is disabled unless explicitly armed.

Run only while the selected listing is open.  This is not a strategy and is
never invoked by ``main_executor``.  It proves the TWS paper order path by
buying one configured board lot, waiting for a fill, then selling the same
quantity.  If the buy does not fill, the script cancels only its own order.
"""

from __future__ import annotations

import logging
import os

from core.ibkr_adapter import IBKRClient
from core.instruments import instrument_for
from core.main_executor import ibkr_connection_settings, load_local_env
from core.risk_gateway import RiskGateway


def main() -> None:
    load_local_env()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    if os.getenv("NOVA_PAPER_SMOKE_TEST") != "CONFIRM":
        raise SystemExit("Refusing to trade. Set NOVA_PAPER_SMOKE_TEST=CONFIRM in your local .env to arm this paper-only test.")

    instrument_id = os.getenv("NOVA_PAPER_SMOKE_INSTRUMENT", "ASML").upper()
    spec = instrument_for(instrument_id)
    quantity = spec.min_quantity
    host, port, client_id = ibkr_connection_settings()
    broker = IBKRClient(host, port, client_id + 10)
    broker.connect_and_start()
    try:
        broker.verify_instruments([instrument_id])
        risk = RiskGateway(broker)
        account = broker.get_account()
        buy = {"symbol": instrument_id, "action": "BUY", "qty": quantity}
        decision = risk.evaluate(buy, account)
        if not decision.approved:
            raise SystemExit(f"Buy rejected by risk gateway: {decision.reason}")
        buy_id = broker.place_market_order(buy, "PaperOrderSmokeTest")
        try:
            buy_status = broker.wait_for_terminal_order(buy_id)
        except Exception:
            broker.cancelOrder(buy_id)
            raise
        if buy_status != "Filled":
            raise SystemExit(f"Buy order {buy_id} ended as {buy_status}; no sell order was sent.")

        sell = {"symbol": instrument_id, "action": "SELL", "qty": quantity}
        decision = risk.evaluate(sell, broker.get_account())
        if not decision.approved:
            raise SystemExit(f"Sell rejected by risk gateway: {decision.reason}; close the filled paper position manually.")
        sell_id = broker.place_market_order(sell, "PaperOrderSmokeTest")
        sell_status = broker.wait_for_terminal_order(sell_id)
        if sell_status != "Filled":
            raise SystemExit(f"Sell order {sell_id} ended as {sell_status}; inspect the paper position in TWS.")
        print(f"Paper smoke test completed: bought and sold {quantity} {instrument_id}.")
    finally:
        broker.close()


if __name__ == "__main__":
    main()
