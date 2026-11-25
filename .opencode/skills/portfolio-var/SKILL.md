---
name: portfolio-var
description: "Use for whole-account 1-day 99% historical VaR reporting across Python backend, Supabase fouueohrhxvkacrnuylx, and Cloudflare website Worker. Triggers: risk payload schema, portfolio_risk_history/daily tables, RLS, retention, 60s publisher, rollback switch, rollout/acceptance, README/runbook updates."
metadata:
  audience: maintainers
  scope: var-reporting-v1
---

# Portfolio VaR Reporting

Confirmed targets:
- Backend: this repository (canonical `novaquantclub/IBKR-Paper-Trading`).
- Website: the sibling website checkout (canonical `novaquantclub/website`).
- Supabase: project Portfolio Updates, ID `fouueohrhxvkacrnuylx`, URL `https://fouueohrhxvkacrnuylx.supabase.co`, region `eu-west-2`, PG17. Verify a fresh feed before calculating; never seed current VaR from a stale snapshot.
- Worker: `website` (confirm the account and route before deploying).

## Workflow constraints

- Preserve unrelated local changes. Use a task-specific branch and stage only feature files.
- Follow `supabase`, `supabase-postgres-best-practices`, `wrangler` skills. Follow website `AGENTS.md`.
- Offline-first implementation/test. No order changes, no executor restarts.

## Public payload

- Add optional `risk` object, bump schema version, retain all existing fields (backward compat required).
- Fields: status `ready|unavailable|stale`, calc time, source portfolio time, latest verification time, history cutoff, confidence 0.99, horizon 1d, N, lookback dates, quantile convention nearest-rank, base EUR, methodology version, VaR fraction, VaR EUR, 21-scenario worst loss, coverage summary, stable reason codes, last-valid result (separate ID + original timestamp when relevant).
- Nulls for unavailable metrics, never zero-fill missing. Exclude broker account IDs, raw contract IDs, credentials, internal errors.

## Supabase storage

- `portfolio_risk_history`: max 1 obs per UTC minute (calc metadata + status + scalars). 30d retention.
- `portfolio_risk_daily`: final observed state per UTC date. Retain indefinitely. Write daily before pruning minutes.
- Logical portfolio ID (not broker account no), idempotent publication keys, indexes for portfolio/time-range reads.
- No full history duplicated into portfolio snapshots. Do not silently change snapshot retention; document its growth/cleanup separately (currently 40MB).
- RLS enabled, browser roles SELECT only. Writes/cleanup host service-role only, key stays on Python host, never in repo.
- Outage: queue locally, retry idempotent without dupes or timestamp mutation. No backfill with today's weights.

## Ops

- Publisher cadence 60s (observation cadence, not every-position guarantee).
- Rollout: offline tests -> resolve mappings vs fresh holdings -> additive migrations + RLS test -> restore publisher feed -> confirm successive 1-min cycles + retries + storage -> deploy Worker (verified account/route) + live smoke test -> document rollback switch (disable risk pub/display, preserve portfolio reporting + history).
- Docs: update backend README + runbooks (methodology, config, cache locations, recovery, storage policy, exact project targets). Example env files: non-secret settings only.
- V1 is reporting only: no trade limits, no auto decisions, no historical-portfolio reconstruction, no validated 99% coverage claim. Backtesting follows after live forecasts + next-day outcomes accumulate.
