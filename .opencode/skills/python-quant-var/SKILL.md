---
name: python-quant-var
description: "Use for Python historical-simulation VaR in Nova-Quantitative-Investing (pandas, yfinance, stdlib sqlite3, unittest, Windows venv). Triggers: pure VaR calculation, EUR/FX returns, 252-scenario alignment, GBp/GBP units, mapping, background price worker, local SQLite cache, calculation fixtures."
metadata:
  audience: maintainers
  stack: python-pandas-yfinance-sqlite
---

# Python Quant VaR

Target repo: this repository (canonical `novaquantclub/IBKR-Paper-Trading`).
Env: existing Windows venv, `requirements.txt` (`yfinance`, `pandas`, `ibapi`, `fastapi`/`uvicorn` local bridge only). Never place/cancel/modify orders for website work. Never restart trading executors.

## Architecture

`Read-only IBKR collection -> local risk calc/cache -> Supabase publisher -> website`

- Pure calculation module: no network, no DB imports. Inputs: signed market values, native cash by currency, equity, aligned return matrix. Outputs: VaR fraction/amount + diagnostics.
- Separate layers: price acquisition (`yfinance` adjusted closes + FX), instrument mapping, persistence.
- Local SQLite via stdlib only: cached prices, audit inputs (30d), pending publications queue, reusable price cache (bounded). Ignore runtime files in Git.
- Background price worker: bounded retries + backoff. Must not block 60s portfolio publication or touch order-submission path. Discard results if input snapshot is no longer current. Reuse results when inputs unchanged, record last-checked time.
- Recalculate once per 60s cycle when positions/valuations/cash/equity/return dataset changes. New holding -> fetch, expose unavailable until complete coverage.

## Methodology (v1)

- Denominator: positive broker-reported total equity. Never normalize weights to 100%, never use website gross-exposure weights.
- Weight: signed current MV in EUR / equity. Include signed shorts, cash by currency.
- EUR return: `(1 + local return) * (1 + FX return) - 1`. FX quote direction fixed: EUR value per unit foreign currency. Foreign cash: signed EUR value * FX return. EUR cash: zero return. No double-counting; offsetting currency exposures must cancel.
- Hedge rule: only actual cash balances count. No analytical/simulated overlays.
- Scope v1: linear stocks, ETFs, shorts, cash. Any held derivative needing repricing -> whole-account VaR unavailable.
- Quantile: losses = -portfolio returns, ascending, pick `ceil(0.99*N)` nearest-rank. N=252 -> 3rd-largest loss. `VaR fraction = max(0, selected)`, `VaR EUR = fraction * equity`. Horizon 1 day. Secondary: worst of last 21 scenarios (not VaR).
- History: only completed daily bars, exclude current UTC date. Require 252 valid aligned scenarios + starting prices. Match start AND end dates per interval; exclude incompatible holidays/missing obs without forward-fill or zero-substitution. Fetch up to 2 calendar years. Show actual dates + N. Refresh full cached window after daily updates (no mixed adjustment bases).
- Identity: native listing/exchange/currency + broker contract IDs (extend internal payload). Reuse STOXX registry + US universe. Explicit mappings for others; never substitute ADR/other company on ticker resemblance. Handle GBp vs GBP explicitly.
- Failure: missing mapping/history/FX, invalid prices, unsupported instrument, incomplete broker state, nonpositive equity -> explicit unavailable + coverage diagnostics + stable reason codes. Never reuse adapter default FX=1 after lookup failure. Prior valid result only as "Last valid estimate" with original timestamp. Pure EUR cash + positive equity = valid zero.

## Tests (unittest, offline fixtures)

Quantile rank, long/short/leverage/cash-dilution/correlation, EUR-cash-only zero, FX without double-count, allocation change moves result with fixed scenarios, price change moves result with fixed weights, splits/dividends, quote units, holidays/incomplete bars/mismatched intervals, all unavailable branches. Pipeline: cold/warm cache, new holdings, Yahoo failure, broker failure, restart, concurrent readers, late calc never overwrites newer state.
