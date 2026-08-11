# Live/Paper Strategy Contract

This document is the current integration contract for strategies that can reach
Nova's IBKR simulated-paper execution path. Update it whenever the data,
risk, or execution architecture changes.

## Architecture

```text
external one-minute data (currently yfinance)
  -> shared universe executor / strategy logic
  -> plain signal with price, currency, and timestamp
  -> RiskGateway
  -> IBKR contract/account/order/execution API
```

IBKR is not the broad market-data provider. It is used for contract resolution,
account and position state, paper-order submission, and execution callbacks.
The dashboard is read-only.

The optional `core.fx_hedge_executor` is an execution-side risk control, not a
strategy. It calculates net foreign-currency exposure as **foreign stock value
plus the native cash balance in that currency**. This prevents an FX hedge from
duplicating a natural offset created when a foreign stock is financed by a
debit in the same currency. It sizes any residual conversion with Yahoo FX data
and uses an IBKR IDEALPRO cash-pair market order only when that residual exceeds
the threshold in `config/fx_hedge_profile.json`. It never requests an IBKR FX
quote. It is paper-only and opt-in: `NOVA_FX_HEDGE_ENABLED=CONFIRM` is required.
A broker-rejected FX order stops the hedge executor rather than retrying
automatically; operator review is required before it is restarted.

## Signal Shape

An external-data strategy must return either `None` or:

```python
{
    "symbol": "ASML",                 # Nova instrument ID
    "action": "BUY",                  # BUY or SELL
    "qty": 1,                          # whole valid board lot
    "reference_price": 1548.20,        # external source price
    "reference_currency": "EUR",      # listing currency
    "reference_timestamp": "2026-08-11T14:00:00+00:00",  # ISO-8601, tz-aware
}
```

Strategies must never create an `IBKRClient`, request broker prices, read
credentials, or submit/cancel orders themselves.

## Data and Freshness

- Yahoo data supplies the current one-minute-bar signal data. It may be about
  15–20 minutes delayed.
- `config/risk_profile.json` currently allows external data up to 1,200 seconds
  old. Missing, invalid, future-dated, or older data is rejected immediately.
- Rejected stale signals are not queued. On the next data update, the strategy
  recomputes its conditions and may emit a new signal only if they still hold.

## Risk and Execution

- `RiskGateway` enforces paper-only mode, allowed symbols/actions, board lots,
  max quantity, EUR-equivalent notional, and buying power.
- Buy notional includes the configured slippage buffer. This is a risk reserve,
  not a guaranteed fill price; orders remain IBKR paper market orders.
- IBKR resolves one unambiguous native listing before an order is submitted.
- The executor treats an order as successful only after IBKR reports a fill.

## Position Ownership

Universe executors read all TWS positions to prevent duplicate buys. A strategy
must track and persist its own managed positions if it should sell positions
across restarts. It must not automatically adopt manually entered or unrelated
Nova positions.

## STOXX Europe 600 Expansion

`python -m core.stage_stoxx_europe_600` downloads the current public official
membership table (company, country, and weight) into an ignored local file.
It does not change `config/stoxx_europe_600_registry.csv`, the risk allow-list,
or any orders. The optional `--discover-ibkr` mode uses Yahoo name search and
IBKR contract search to generate an ignored *candidate-only* report. Its output
must be reviewed to select the native listing and establish an ISIN plus Yahoo
ticker before a row is manually added to the approved registry. The expansion
tool never auto-approves a constituent because names, share classes, and
exchange listings are ambiguous.

The first review batch added clear EUR primary listings only. Constituents whose
native listing is in CHF, GBP, DKK, or another non-EUR currency remain
candidate-only until their currency and hedging treatment are explicitly
approved.

## Existing Runtimes

- `core.europe_yfinance_executor`: approved European registry rows, native
  listings, EUR signals, one-minute scans.
- `core.sp500_yfinance_executor`: current S&P 500 universe, USD signals,
  one-minute data scanned at a controlled cadence. Its deliberately loose
  architecture-test threshold is `NOVA_SP500_REVERSAL_THRESHOLD` (default
  `0.00001`, or 0.001%), with up to 25 new orders per scan and a 100-position
  maximum unless the local environment overrides those caps.
- `core.main_executor`: legacy IBKR-price runtime; disabled by default so it
  cannot consume broker market-data lines.
- `core.fx_hedge_executor`: optional EUR-base paper FX hedge for configured
  foreign-currency stock exposure; runs separately from universe strategies.

## Strategy Performance Attribution

TWS reports only account-level equity. The dashboard therefore treats the TWS
equity history as the authoritative total portfolio chart. Separately, it
maintains an ignored local execution ledger from TWS executions whose
`orderRef` begins with `nova-`. It replays each strategy's fills into its own
cash-and-position sleeve, marks open positions with external Yahoo prices, and
displays the resulting **gross strategy P&L** in the EUR reporting base.

The strategy chart excludes manual and untagged IBKR activity, is gross of
commissions, and begins accumulating when the dashboard first observes the
execution. It must not be summed with the TWS account-equity value or treated
as a broker-reported sub-account NAV.
