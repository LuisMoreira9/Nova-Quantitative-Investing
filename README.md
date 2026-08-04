# Nova Quantitative Investing

Stage 1 local paper-trading pipeline for Nova Quant Club.

This repo is meant to let club members write Python trading strategies, run them
against Alpaca's paper trading environment, and enforce shared club risk limits
before any simulated order is submitted.

## Stage 1 Scope

What is built now:

- `strategies/mean_reversion.py`: example student strategy with the required
  `on_bar(bar)` method.
- `core/main_executor.py`: local runtime that loads strategies, listens to
  Alpaca market bars, sends signals to the risk gateway, and submits approved
  paper orders.
- `core/risk_gateway.py`: central risk checkpoint. Every order signal must pass
  through this file before it can reach Alpaca.
- `config/risk_profile.json`: single source of truth for risk limits.
- `.env.example`: documents the private environment variables each local runner
  needs.

What is intentionally not built yet:

- GitHub Actions checks.
- Automated deployment.
- Cloud server process management.
- Website portfolio feed.

Those belong to later stages after the local loop is reviewed.

## How the Files Connect

The order flow is:

```text
Alpaca live bar
  -> core/main_executor.py
  -> strategy.on_bar(bar)
  -> plain signal dict or None
  -> core/risk_gateway.py
  -> Alpaca paper order only if approved
```

Student strategies should only return signals. They should not import Alpaca
trading clients, read API keys, or call `submit_order`.

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

- Trade value is calculated with the latest Alpaca market price, not a hardcoded
  placeholder price.
- Risk limits are centralized in `config/risk_profile.json`, not duplicated
  across Python files.

## Alpaca Paper Setup

Alpaca is a broker API. Its paper trading mode is a simulator: orders use fake
money in a paper account, not real money.

To run locally:

1. Create an Alpaca account.
2. Open Alpaca's Paper Trading dashboard.
3. Generate paper API keys.
4. Copy `.env.example` to `.env`.
5. Fill in `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` in `.env`.
6. Install dependencies: `pip install -r requirements.txt`.
7. Run the local executor: `python -m core.main_executor`.

Never commit `.env` or real API keys.
