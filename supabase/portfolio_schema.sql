-- Public, read-only portfolio snapshots for novaquantclub.com.
-- Run in Supabase SQL Editor before enabling the host publisher.

create table if not exists public.portfolio_snapshots (
  id bigint generated always as identity primary key,
  snapshot jsonb not null,
  published_at timestamptz not null default now()
);

create index if not exists portfolio_snapshots_published_at_idx
  on public.portfolio_snapshots (published_at desc);

alter table public.portfolio_snapshots enable row level security;

-- The browser may read the most recent sanitised snapshot. It may never write.
drop policy if exists "public reads portfolio snapshots" on public.portfolio_snapshots;
create policy "public reads portfolio snapshots"
  on public.portfolio_snapshots for select
  to anon, authenticated
  using (true);

revoke insert, update, delete on public.portfolio_snapshots from anon, authenticated;

-- Durable, browser-safe Nova trade history.  The host upserts fills using the
-- service-role key; browser clients receive no IBKR execution or order IDs.
create table if not exists public.portfolio_trade_executions (
  id text primary key,
  executed_at timestamptz not null,
  strategy text not null,
  symbol text not null,
  exchange text,
  currency text not null,
  side text not null check (side in ('buy', 'sell')),
  quantity numeric not null check (quantity > 0),
  price numeric not null check (price >= 0),
  published_at timestamptz not null default now()
);

create index if not exists portfolio_trade_executions_executed_at_idx
  on public.portfolio_trade_executions (executed_at desc);

alter table public.portfolio_trade_executions enable row level security;

drop policy if exists "public reads portfolio trade executions" on public.portfolio_trade_executions;
create policy "public reads portfolio trade executions"
  on public.portfolio_trade_executions for select
  to anon, authenticated
  using (true);

revoke insert, update, delete on public.portfolio_trade_executions from anon, authenticated;

-- Retain only recent snapshots. Execute daily with Supabase Cron, or manually.
-- delete from public.portfolio_snapshots
-- where published_at < now() - interval '30 days';
