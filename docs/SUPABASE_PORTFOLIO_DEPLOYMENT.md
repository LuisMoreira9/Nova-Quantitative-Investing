# Hosted Portfolio Deployment

The public website must never connect to TWS, IB Gateway, or a home-machine
port.  The host publishes a sanitised snapshot over outbound HTTPS; the
website reads the newest snapshot from Supabase.

## 1. Create the table and read policy

In Supabase **SQL Editor**, run [`supabase/portfolio_schema.sql`](../supabase/portfolio_schema.sql).
The policy permits browser reads only.  The host writes using a service-role
key, which must remain only in the host's ignored `.env`.

For VaR history (project **Portfolio Updates**, ID `fouueohrhxvkacrnuylx`,
eu-west-2, PostgreSQL 17), also run
[`supabase/migrations/20260921_portfolio_risk.sql`](../supabase/migrations/20260921_portfolio_risk.sql).
It creates `portfolio_risk_history` (at most one observation per UTC minute,
30-day retention via daily cron **after** the daily upsert) and
`portfolio_risk_daily` (final state per UTC date, indefinite). Both grant
browser roles SELECT only; writes/cleanup stay on the host service role.
Existing `portfolio_snapshots` retention (~40MB, latest hosted 2026-09-12) is
unchanged. Never seed current VaR from that historical snapshot; restore and
verify a fresh 60s feed first.

## 2. Configure the host

Add to the host `.env`:

```env
SUPABASE_URL=https://YOUR_PROJECT.supabase.co
SUPABASE_SERVICE_ROLE_KEY=host-only-secret-key
```

Test one outbound publication while paper TWS is available:

```powershell
python -m dashboard.supabase_publisher
```

This reads local account state and sends a sanitised snapshot. It does not
place, modify, or cancel an order.

Schedule it after the local account collector has a valid snapshot (for
example every 60 seconds).  Do not publish more frequently than the account
refresh interval.

## 3. Configure the website deployment

Set these public deployment variables in the website host:

```env
PUBLIC_SUPABASE_URL=https://YOUR_PROJECT.supabase.co
PUBLIC_SUPABASE_PUBLISHABLE_KEY=your-publishable-key
PUBLIC_RISK_ENABLED=1
```

Do not set `SUPABASE_SERVICE_ROLE_KEY` in the website host.

## Risk rollout and rollback

1. Implement/test offline (`python -m unittest discover -s tests`); no orders.
2. Resolve mappings against fresh holdings; complete coverage required.
3. Apply the risk migration above; verify anon/authenticated SELECT, no WRITE.
4. Restore/verify the 60s publisher feed without restarting trading executors.
5. Confirm successive 1-minute risk cycles, idempotent retries, storage growth.
6. Deploy Worker `website` (`167ea1fc6e52431c8368435efdf02267`) after verifying
   account + `novaquantclub.com/portfolio` route; smoke-test live.
7. Rollback switch: host `NOVA_RISK_ENABLED=0` stops risk publication while
   portfolio snapshots continue and history is preserved; website
   `PUBLIC_RISK_ENABLED=0` hides the risk section. No backfill with today's
   weights; no trade limits or auto decisions in v1.

## Operations

- Review the JSON in `portfolio_snapshots.snapshot` before enabling public use.
- Set a daily retention task for old rows (the schema includes an example).
- Rotate the service key immediately if it is ever exposed.
- Keep the host publisher, TWS, and executors on the reviewed deployment branch only.
