# Local website portfolio integration

This is a development-only bridge between the local IBKR paper-trading setup
and the local Svelte website checkout. It is not exposed to the internet and
never submits, modifies, or cancels orders.

## Run it locally

With paper TWS running and API socket clients enabled, start the read-only
bridge from the IBKR project root:

```powershell
python -m dashboard.portfolio_api
```

It listens only on `127.0.0.1:8765`. The endpoint `/health` is a connectivity
check and `/api/portfolio` provides current account equity, positions, Nova
execution-ledger attribution, and history.

Then start the website from its separate checkout:

```powershell
pnpm dev
```

Open `http://localhost:5173/portfolio`.

## Data semantics

- **Total equity** is supplied by TWS and is the account-level source of truth.
- **Positions** are the current account positions supplied by TWS.
- **Strategy performance** is Nova-attributed gross P&L based on strategy-tagged
  fills and external Yahoo marks. It is not a broker-provided strategy NAV:
  a true strategy NAV requires explicit cash allocations per strategy.
- The website polls the local bridge every 15 seconds and updates Svelte state
  in place. It does not reload the browser page.

For a later hosted deployment, replace this localhost bridge with an
authenticated ingestion service. Browser clients must never connect directly
to TWS or receive IBKR credentials.
