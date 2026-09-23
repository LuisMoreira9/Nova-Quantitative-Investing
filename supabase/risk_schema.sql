-- Portfolio VaR risk history for novaquantclub.com.
-- Additive migration: run in Supabase SQL Editor (project fouueohrhxvkacrnuylx, eu-west-2, PG17).
-- Host publisher upserts with the service-role key; browsers get SELECT only.
-- Minute history: 30 days. Daily history: indefinite. Snapshots retention unchanged.

create table if not exists public.portfolio_risk_history (
  id text primary key,
  portfolio_id text not null,
  minute_bucket timestamptz not null,
  calculated_at timestamptz not null,
  observed_at timestamptz,
  source_portfolio_time timestamptz,
  verification_time timestamptz,
  history_cutoff date,
  status text not null check (status in ('ready', 'unavailable', 'stale')),
  var_fraction numeric,
  var_eur numeric,
  worst_21_fraction numeric,
  sample_count integer not null default 0,
  lookback_start date,
  lookback_end date,
  methodology_version text,
  base_currency text not null default 'EUR',
  reasons jsonb not null default '[]'::jsonb,
  coverage jsonb not null default '{}'::jsonb,
  published_at timestamptz not null default now()
);

create index if not exists portfolio_risk_history_portfolio_minute_idx
  on public.portfolio_risk_history (portfolio_id, minute_bucket desc);
create index if not exists portfolio_risk_history_portfolio_calculated_idx
  on public.portfolio_risk_history (portfolio_id, calculated_at desc);

alter table public.portfolio_risk_history enable row level security;

drop policy if exists "public reads portfolio risk history" on public.portfolio_risk_history;
create policy "public reads portfolio risk history"
  on public.portfolio_risk_history for select
  to anon, authenticated
  using (true);

grant select on public.portfolio_risk_history to anon, authenticated;
revoke insert, update, delete on public.portfolio_risk_history from anon, authenticated;

create table if not exists public.portfolio_risk_daily (
  id text primary key,
  portfolio_id text not null,
  risk_date date not null,
  calculated_at timestamptz not null,
  observed_at timestamptz,
  source_portfolio_time timestamptz,
  verification_time timestamptz,
  history_cutoff date,
  status text not null check (status in ('ready', 'unavailable', 'stale')),
  var_fraction numeric,
  var_eur numeric,
  worst_21_fraction numeric,
  sample_count integer not null default 0,
  lookback_start date,
  lookback_end date,
  methodology_version text,
  base_currency text not null default 'EUR',
  reasons jsonb not null default '[]'::jsonb,
  coverage jsonb not null default '{}'::jsonb,
  published_at timestamptz not null default now(),
  unique (portfolio_id, risk_date)
);

create index if not exists portfolio_risk_daily_portfolio_date_idx
  on public.portfolio_risk_daily (portfolio_id, risk_date desc);

alter table public.portfolio_risk_daily enable row level security;

drop policy if exists "public reads portfolio risk daily" on public.portfolio_risk_daily;
create policy "public reads portfolio risk daily"
  on public.portfolio_risk_daily for select
  to anon, authenticated
  using (true);

grant select on public.portfolio_risk_daily to anon, authenticated;
revoke insert, update, delete on public.portfolio_risk_daily from anon, authenticated;

-- Retention: keep minute-level risk for 30 days, daily indefinitely.
-- Run daily AFTER the publisher has upserted the daily row (daily before pruning).
-- delete from public.portfolio_risk_history
-- where minute_bucket < now() - interval '30 days';

-- Existing portfolio_snapshots retention is separate and unchanged (currently ~40MB).
-- Do not silently alter it here; see docs/SUPABASE_PORTFOLIO_DEPLOYMENT.md.
