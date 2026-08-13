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

-- Retain only recent snapshots. Execute daily with Supabase Cron, or manually.
-- delete from public.portfolio_snapshots
-- where published_at < now() - interval '30 days';
