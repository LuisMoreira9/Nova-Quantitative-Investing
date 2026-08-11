# Nova Quantitative Investing

Stage 1 local IBKR paper-trading pipeline for Nova Quant Club.

This repo is meant to let club members write Python trading strategies, run them
against Interactive Brokers Trader Workstation (TWS) simulated trading, and enforce shared club risk limits
before any simulated order is submitted.

## Stage 1 Scope

What is built now:

- `strategies/mean_reversion.py`: example student strategy with the required
  `on_bar(bar)` method.
- `core/main_executor.py`: local runtime that loads strategies, listens to
  legacy executor kept only for compatibility; it is disabled by default so
  IBKR is not used as a broad price-data provider.
- `core/risk_gateway.py`: central risk checkpoint. Every order signal must pass
  through this file before it can reach TWS.
- `config/risk_profile.json`: single source of truth for risk limits.
- `.env.example`: documents the private environment variables each local runner
  needs.
- `dashboard/app.py`: a read-only Streamlit view of the connected IBKR paper
  account, including account equity, positions, orders, and a simulated FX
  hedge overlay.
- `core/instruments.py`: native exchange definitions for AAPL, ASML on
  Euronext Amsterdam (EUR), Toyota on TSE (JPY), and the TOPIX ETF 1306 on TSE
  (JPY). TWS must resolve the exact contract before Nova can trade it.

What is intentionally not built yet:

- GitHub Actions checks.
- Automated deployment.
- Cloud server process management.
- Website portfolio feed.

Those belong to later stages after the local loop is reviewed.

## How the Files Connect

The order flow is:

```text
IBKR delayed price snapshot
  -> core/main_executor.py
  -> strategy.on_bar(bar)
  -> plain signal dict or None
  -> core/risk_gateway.py
  -> IBKR simulated order only if approved
```

Student strategies should only return signals. They should not import IBKR API
clients, read credentials, or call `placeOrder`.

## Strategy Contract

Each strategy class should live in `strategies/` and expose:

```python
def on_bar(self, bar):
    ...
```

Return `None` when no trade is needed. Return a signal when the strategy wants
to propose a trade:

```python
{"symbol": "AAPL", "action": "BUY", "qty": 10}
```

The risk gateway can still reject that signal.

## Risk Profile

All trading limits live in `config/risk_profile.json`. Change limits there, not
inside Python files.

The initial values are conservative placeholders for paper trading and should be
reviewed before the club treats the system as official. See
`config/README.md` for each field's meaning.

Important Stage 1 fixes:

- Trade value is calculated with the latest IBKR market price, not a hardcoded
  placeholder price.
- Risk limits are centralized in `config/risk_profile.json`, not duplicated
across Python files.

## Native Currency and FX Hedge Simulation

Nova trades the configured native listing and order currency; it does not
silently replace ASML with its U.S. ADR or Tokyo listings with U.S. tickers.
The default reporting/risk currency is EUR. A JPY position is converted to EUR
for the risk limit with a delayed IBKR EUR/JPY quote.

The dashboard's 100% FX hedge is currently an **analytical overlay**: it shows
the opposite EUR value for a foreign-currency position and its remaining net
EUR exposure. It does not submit an FX order or create a cash-FX position in
TWS. Set `NOVA_SIMULATED_FX_HEDGE_RATIO` to a value from `0` to `1` in `.env`
to change the displayed hedge ratio.

The risk gateway fails closed if it cannot price the foreign exchange or if
the TWS account buying-power currency does not match `NOVA_BASE_CURRENCY`.

### Deliberate paper-order test

`python -m core.paper_order_smoke_test` is an opt-in order-path check, not a
strategy. It will only run after `NOVA_PAPER_SMOKE_TEST=CONFIRM` is placed in
your ignored `.env`. It resolves the selected native contract, buys one valid
board lot, waits for a paper fill, and then sells that same quantity. Run it
only while that market is open. It cancels its own buy if no terminal order
status arrives; it never modifies another TWS order.

## IBKR Paper Setup

Interactive Brokers Trader Workstation (TWS) simulated trading is used for
paper orders; no real money is involved.

To run locally:

1. Open TWS in its Simulated Trading session.
2. In `Global Configuration → API → Settings`, enable ActiveX and Socket
   Clients, disable Read-Only API, and confirm the paper socket port (normally
   `7497`).
3. Copy `.env.example` to `.env` and set the local host, port, and a unique
   client ID.
4. Install dependencies: `pip install -r requirements.txt`.
5. Run the local executor: `python -m core.main_executor`.

Never commit `.env` or real API keys.

## Paper-account Dashboard

The dashboard reads the same IBKR **simulated** account used by the executor.
It does not use historical price snapshots from the earlier research MVP.

```powershell
pip install -r requirements.txt
streamlit run dashboard/app.py
```

The dashboard connects to local paper TWS in read-only query mode; it cannot
submit, cancel, or modify orders. It records equity while the dashboard is
refreshed, and displays current TWS positions plus orders/executions available
to the session. Orders placed by this executor are tagged with their strategy
class so the dashboard can group activity by strategy.

The active yfinance executors use external one-minute data for strategy signals.
IBKR is used for contract resolution, account/position/order state, and paper
execution—not broad price polling. Each signal carries a price, currency, and
timestamp; the risk gateway rejects it when the delayed external data is older
than 20 minutes instead of queuing it for a later trade.

## STOXX Europe 600 Expansion

The European executor reads `config/stoxx_europe_600_registry.csv`, not a
hard-coded ticker list. Each constituent row must include an ISIN, a Yahoo
Finance data ticker, the intended native IBKR listing, currency, board lot,
and an explicit `approved=true` flag. Only approved rows are written to the
local risk allow-list and can reach paper execution.

Use `python -m core.validate_stoxx_europe_600` to run a data-only TWS contract
check for approved rows. It writes an ignored local report under `data/`; it
does not submit orders or auto-approve any row. Use `--all` only after adding
and manually reviewing a larger constituent mapping. This staged process is
intentional: an official component file identifies constituents, but a company
name alone is not a safe execution contract.

The European yfinance strategy uses a deliberately permissive 0.001% reversal
trigger by default while validating the paper-trading architecture. Set
`NOVA_EUROPE_REVERSAL_THRESHOLD` in `.env` to tune it; this does not change the
separate S&P 500 scanner's independently configurable trigger.

For a one-off end-to-end demonstration, set
`NOVA_EUROPE_ARCHITECTURE_TEST_MODE=BUY_ONE`. It buys exactly one currently
unheld approved European instrument, writes an ignored completion marker, and
does not force further orders. Delete the local marker only when deliberately
re-running that controlled test.

The six-name European pilot scans one-minute Yahoo bars every 60 seconds by
default (`NOVA_EUROPE_SCAN_SECONDS`). Each completed scan reports its quote and
eligible-signal counts in `data/europe_yfinance_executor_error.log`.

## Live Strategy Contract

The maintained contract for any strategy that can reach paper execution is
[`docs/LIVE_PAPER_STRATEGY_CONTRACT.md`](docs/LIVE_PAPER_STRATEGY_CONTRACT.md).
It is the source of truth for signal fields, freshness handling, risk, broker
boundaries, and position ownership.

Strategies follow `strategies.base.BaseLiveStrategy`: they expose a `symbol`
and implement `on_bar(bar)`. They never query the broker or submit orders.
Historical snapshot adapters from the research dashboard are deliberately not
copied here because a snapshot target-weight backtest is not a live IBKR
strategy. Each live strategy needs its own real-time data, rebalance schedule,
and paper-trading validation.
