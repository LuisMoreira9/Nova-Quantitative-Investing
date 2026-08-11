# Risk Profile Configuration

`risk_profile.json` is the single source of truth for Stage 1 trading limits.
If the club wants to change risk limits later, change the JSON file here rather
than editing constants inside Python files.

What each setting does:

- `paper_trading_only`: must stay `true`. The project is currently designed only
  for IBKR simulated trading.
- `allowed_symbols`: Nova instrument IDs strategies are allowed to trade. The
  current set maps to native IBKR listings: `ASML` (Amsterdam, EUR), `TOYOTA`
  (Tokyo, JPY), and `TOPIX_ETF` (Tokyo-listed 1306, JPY), alongside `AAPL`.
- `allowed_symbols_file` / `allowed_symbols_files`: optional local, generated
  allow-lists. The S&P scanner and the reviewed STOXX registry each write one;
  these files extend the static list but never bypass the other risk checks.
- `allowed_actions`: order actions the gateway can approve. Removing `SELL`
  would make the first version long-only.
- `base_currency`: portfolio reporting/risk currency. The initial local setup
  uses EUR.
- `max_trade_notional_base_currency`: maximum EUR-equivalent value for a
  single proposed trade. The gateway values a native listing at its IBKR price
  and converts foreign currencies before it approves the order.
- `require_reference_price`: keeps IBKR market data out of the risk path. A
  strategy must provide externally sourced price, currency, and timestamp.
- `max_reference_age_seconds`: stale/missing data rejects that cycle's signal;
  it is recalculated only when a fresh data update arrives. The current value
  is 1,200 seconds (20 minutes) to accommodate Yahoo's delayed one-minute bars.
- `max_order_quantity`: maximum number of shares per order.
- `min_order_quantity`: minimum number of shares per order.
- `min_order_quantity_by_symbol`: exchange board lots. Toyota is 100 shares
  and the selected TOPIX ETF (1306) is 10 units; an invalid lot is rejected.
- `require_whole_shares`: when `true`, rejects fractional-share signals.

How it ties into the pipeline:

1. A strategy in `strategies/` returns a signal.
2. `core/main_executor.py` receives that signal.
3. `core/risk_gateway.py` loads this JSON file and checks the signal.
4. Only approved signals are converted into IBKR simulated orders.
