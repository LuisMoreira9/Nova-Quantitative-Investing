# Hosted Portfolio Deployment

The public website must never connect to TWS, IB Gateway, or a home-machine
port.  The host publishes a sanitised snapshot over outbound HTTPS; the
website reads the newest snapshot from Supabase.

## 1. Create the table and read policy

In Supabase **SQL Editor**, run [`supabase/portfolio_schema.sql`](../supabase/portfolio_schema.sql).
The policy permits browser reads only.  The host writes using a service-role
key, which must remain only in the host's ignored `.env`.

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
```

Do not set `SUPABASE_SERVICE_ROLE_KEY` in the website host.

## Operations

- Review the JSON in `portfolio_snapshots.snapshot` before enabling public use.
- Set a daily retention task for old rows (the schema includes an example).
- Rotate the service key immediately if it is ever exposed.
- Keep the host publisher, TWS, and executors on the reviewed deployment branch only.
