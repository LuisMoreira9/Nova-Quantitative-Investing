# Risk Profile Configuration

`risk_profile.json` is the single source of truth for Stage 1 trading limits.
If the club wants to change risk limits later, change the JSON file here rather
than editing constants inside Python files.

What each setting does:

- `paper_trading_only`: must stay `true`. The project is currently designed only
  for Alpaca paper trading.
- `allowed_symbols`: ticker symbols strategies are allowed to trade. Stage 1
  starts with only `AAPL` so the first local test is simple.
- `allowed_actions`: order actions the gateway can approve. Removing `SELL`
  would make the first version long-only.
- `max_trade_notional_usd`: maximum dollar value for a single proposed trade.
  The gateway calculates this as `qty * latest Alpaca market price`.
- `max_order_quantity`: maximum number of shares per order.
- `min_order_quantity`: minimum number of shares per order.
- `require_whole_shares`: when `true`, rejects fractional-share signals.

How it ties into the pipeline:

1. A strategy in `strategies/` returns a signal.
2. `core/main_executor.py` receives that signal.
3. `core/risk_gateway.py` loads this JSON file and checks the signal.
4. Only approved signals are converted into Alpaca paper orders.
