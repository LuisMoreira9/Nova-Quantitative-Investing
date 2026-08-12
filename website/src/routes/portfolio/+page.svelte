<script lang="ts">
	import { onMount } from 'svelte';
	import { LineChart } from 'layerchart';
	import { scaleUtc } from 'd3-scale';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import Wifi from '@lucide/svelte/icons/wifi';
	import * as Card from '$lib/components/ui/card/index.js';
	import * as Chart from '$lib/components/ui/chart/index.js';

	type Row = Record<string, string | number | null>;
	type PortfolioPayload = {
		account: Record<string, string>;
		base_currency: string;
		loaded_at: string;
		history: Row[];
		positions: Row[];
		strategy_sleeves: Row[];
		strategy_history: Row[];
	};

	const apiUrl = 'http://127.0.0.1:8765/api/portfolio';
	const refreshEveryMs = 15_000;

	let portfolio = $state<PortfolioPayload | null>(null);
	let error = $state<string | null>(null);
	let loading = $state(true);
	let selectedStrategy = $state('All strategies');

	const baseCurrency = $derived(portfolio?.base_currency ?? 'EUR');
	const strategyNames: Record<string, string> = {
		Sp500ShortMomentumRe: 'S&P 500 short momentum reversal',
		ShortExposureRemedia: 'Short exposure remediation'
	};
	const normalizedStrategyHistory = $derived(
		(portfolio?.strategy_history ?? [])
			.map((row) => ({
				date: new Date(String(row.timestamp)),
				strategy: strategyNames[String(row.strategy)] ?? String(row.strategy),
				pnl: Number(row.gross_pnl_base)
			}))
			.filter((row) => !Number.isNaN(row.date.getTime()) && Number.isFinite(row.pnl))
			.sort((left, right) => left.date.getTime() - right.date.getTime())
	);
	const strategies = $derived(
		[...new Set(normalizedStrategyHistory.map((row) => row.strategy))].sort()
	);
	const totalSeries = $derived(
		(portfolio?.history ?? [])
			.map((row) => ({ date: new Date(String(row.timestamp)), equity: Number(row.equity) }))
			.filter((row) => !Number.isNaN(row.date.getTime()) && Number.isFinite(row.equity))
			.sort((left, right) => left.date.getTime() - right.date.getTime())
	);
	function buildStrategySeries(history: { date: Date; strategy: string; pnl: number }[]) {
		const snapshots = new Map<number, { date: Date; values: Map<string, number> }>();
		for (const row of history) {
			const timestamp = row.date.getTime();
			const snapshot = snapshots.get(timestamp) ?? { date: row.date, values: new Map<string, number>() };
			// A strategy may have multiple currency sleeves in one snapshot.
			snapshot.values.set(row.strategy, (snapshot.values.get(row.strategy) ?? 0) + row.pnl);
			snapshots.set(timestamp, snapshot);
		}

		const latestByStrategy = new Map<string, number>();
		return [...snapshots.values()]
			.sort((left, right) => left.date.getTime() - right.date.getTime())
			.map((snapshot) => {
				for (const [strategy, pnl] of snapshot.values) latestByStrategy.set(strategy, pnl);
				const pnl = selectedStrategy === 'All strategies'
					? [...latestByStrategy.values()].reduce((total, value) => total + value, 0)
					: latestByStrategy.get(selectedStrategy);
				return { date: snapshot.date, pnl: pnl ?? Number.NaN };
			})
			.filter((row) => Number.isFinite(row.pnl));
	}

	const strategySeries = $derived(buildStrategySeries(normalizedStrategyHistory));

	function paddedDomain(values: number[]): [number, number] | undefined {
		if (values.length === 0) return undefined;
		const lower = Math.min(...values);
		const upper = Math.max(...values);
		const range = upper - lower;
		const padding = range > 0 ? range * 0.12 : Math.max(Math.abs(upper) * 0.002, 1);
		return [lower - padding, upper + padding];
	}

	const totalEquityDomain = $derived(paddedDomain(totalSeries.map((row) => row.equity)));
	const strategyPnlDomain = $derived(paddedDomain(strategySeries.map((row) => row.pnl)));

	const totalChartConfig = {
		equity: { label: 'Account equity', color: 'var(--chart-1)' }
	} satisfies Chart.ChartConfig;
	const strategyChartConfig = {
		pnl: { label: 'Gross P&L', color: 'var(--chart-2)' }
	} satisfies Chart.ChartConfig;

	function money(value: string | number | null | undefined, currency = baseCurrency) {
		const amount = Number(value);
		if (!Number.isFinite(amount)) return '—';
		return new Intl.NumberFormat('en-IE', {
			style: 'currency',
			currency,
			maximumFractionDigits: 2
		}).format(amount);
	}

	function number(value: string | number | null | undefined) {
		const amount = Number(value);
		return Number.isFinite(amount) ? amount.toLocaleString('en-IE', { maximumFractionDigits: 4 }) : '—';
	}

	function timestamp(value: string | undefined) {
		if (!value) return 'Waiting for local data';
		const date = new Date(value);
		return Number.isNaN(date.getTime())
			? 'Waiting for local data'
			: date.toLocaleString('en-IE', { dateStyle: 'medium', timeStyle: 'medium' });
	}

	async function refreshPortfolio() {
		try {
			const response = await fetch(apiUrl, { headers: { Accept: 'application/json' } });
			const body = await response.json();
			if (!response.ok) throw new Error(body.detail ?? 'The local portfolio bridge returned an error.');
			portfolio = body as PortfolioPayload;
			error = null;
		} catch (cause) {
			error = cause instanceof Error ? cause.message : 'Unable to contact the local portfolio bridge.';
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		void refreshPortfolio();
		const interval = window.setInterval(() => void refreshPortfolio(), refreshEveryMs);
		return () => window.clearInterval(interval);
	});
</script>

<svelte:head>
	<title>Portfolio | Nova Quant Club</title>
	<meta
		name="description"
		content="Live local view of the Nova Quant Club IBKR paper-trading portfolio."
	/>
</svelte:head>

<section class="relative pt-32 pb-20">
	<div class="absolute inset-0 -z-10 [background:radial-gradient(90%_60%_at_50%_0%,hsla(225,70%,55%,.13),transparent_60%)]"></div>
	<div class="mx-auto max-w-7xl px-6">
		<div class="flex flex-col gap-6 border-b pb-10 md:flex-row md:items-end md:justify-between">
			<div>
				<p class="text-sm font-medium tracking-wide text-primary uppercase">Local paper trading</p>
				<h1 class="mt-3 font-heading text-5xl md:text-6xl">Portfolio</h1>
				<p class="mt-4 max-w-2xl text-lg text-muted-foreground">
					A read-only view of the connected IBKR paper account and Nova strategy attribution.
				</p>
			</div>
			<div class="flex items-center gap-3 text-sm text-muted-foreground">
				<Wifi class={['size-4', error ? 'text-destructive' : 'text-primary']} />
				<span>{error ? 'Local bridge unavailable' : 'Updating in place every 15 seconds'}</span>
				<button
					onclick={() => void refreshPortfolio()}
					class="inline-flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-foreground transition-colors hover:bg-muted"
				>
					<RefreshCw class={['size-4', loading && 'animate-spin']} />
					Refresh now
				</button>
			</div>
		</div>

		{#if error}
			<div class="mt-8 rounded-xl border border-destructive/40 bg-destructive/10 p-5 text-sm text-destructive">
				<strong>Could not load real local portfolio data.</strong>
				<p class="mt-2">{error}</p>
				<p class="mt-2 text-muted-foreground">
					Start <code class="rounded bg-background px-1.5 py-0.5">python -m dashboard.portfolio_api</code>
					from the IBKR project while paper TWS is running.
				</p>
			</div>
		{/if}

		<div class="mt-8 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
			{#each [
				{ label: 'Paper equity', value: money(portfolio?.account.equity) },
				{ label: 'Cash', value: money(portfolio?.account.cash) },
				{ label: 'Buying power', value: money(portfolio?.account.buying_power) },
				{ label: 'Open positions', value: number(portfolio?.positions.length) }
			] as item}
				<Card.Root class="border bg-card/50">
					<Card.Header class="pb-2"><Card.Description>{item.label}</Card.Description></Card.Header>
					<Card.Content><p class="text-3xl font-semibold tracking-tight">{item.value}</p></Card.Content>
				</Card.Root>
			{/each}
		</div>

		<p class="mt-4 text-sm text-muted-foreground">Last local snapshot: {timestamp(portfolio?.loaded_at)}</p>

		<div class="mt-10 grid gap-6 xl:grid-cols-2">
			<Card.Root class="border bg-card/50">
				<Card.Header>
					<Card.Title>Total paper-account equity</Card.Title>
					<Card.Description>TWS account-level source of truth. The vertical scale is zoomed to the observed range.</Card.Description>
				</Card.Header>
				<Card.Content>
					{#if totalSeries.length > 1}
						<Chart.Container config={totalChartConfig} class="h-80">
							<LineChart
								data={totalSeries}
								x="date"
								xScale={scaleUtc()}
								yDomain={totalEquityDomain}
								axis="x"
								series={[{ key: 'equity', label: 'Account equity', color: totalChartConfig.equity.color }]}
								props={{ spline: { strokeWidth: 2 } }}
							/>
						</Chart.Container>
					{:else}
						<p class="flex h-80 items-center justify-center text-sm text-muted-foreground">
							Equity history will appear as local snapshots accumulate.
						</p>
					{/if}
				</Card.Content>
			</Card.Root>

			<Card.Root class="border bg-card/50">
				<Card.Header>
					<div class="flex flex-wrap items-start justify-between gap-4">
						<div>
							<Card.Title>Strategy-attributed performance</Card.Title>
							<Card.Description>Gross P&L from Nova-tagged fills and external marks. Same-timestamp currency sleeves are aggregated.</Card.Description>
						</div>
						<select bind:value={selectedStrategy} class="rounded-lg border bg-background px-3 py-2 text-sm">
							<option>All strategies</option>
							{#each strategies as strategy}<option value={strategy}>{strategy}</option>{/each}
						</select>
					</div>
				</Card.Header>
				<Card.Content>
					{#if strategySeries.length > 1}
						<Chart.Container config={strategyChartConfig} class="h-80">
							<LineChart
								data={strategySeries}
								x="date"
								xScale={scaleUtc()}
								yDomain={strategyPnlDomain}
								axis="x"
								series={[{ key: 'pnl', label: 'Gross P&L', color: strategyChartConfig.pnl.color }]}
								props={{ spline: { strokeWidth: 2 } }}
							/>
						</Chart.Container>
					{:else}
						<p class="flex h-80 items-center justify-center text-sm text-muted-foreground">
							Strategy P&L history will appear as Nova fills are marked.
						</p>
					{/if}
				</Card.Content>
			</Card.Root>
		</div>

		<Card.Root class="mt-6 border bg-card/50">
			<Card.Header>
				<Card.Title>Current account positions</Card.Title>
				<Card.Description>Current quantities are reported by TWS; negative quantities are short positions.</Card.Description>
			</Card.Header>
			<Card.Content class="overflow-x-auto">
				{#if (portfolio?.positions.length ?? 0) > 0}
					<table class="w-full min-w-[880px] text-left text-sm">
						<thead class="border-b text-muted-foreground">
							<tr><th class="px-3 py-3 font-medium">Symbol</th><th class="px-3 py-3 font-medium">Exchange</th><th class="px-3 py-3 font-medium">Currency</th><th class="px-3 py-3 text-right font-medium">Quantity</th><th class="px-3 py-3 text-right font-medium">Price</th><th class="px-3 py-3 text-right font-medium">Market value</th><th class="px-3 py-3 text-right font-medium">Unrealized P&L</th></tr>
						</thead>
						<tbody>
							{#each portfolio?.positions ?? [] as position (String(position.symbol) + String(position.exchange))}
								<tr class="border-b border-border/60 last:border-0"><td class="px-3 py-3 font-medium">{position.symbol}</td><td class="px-3 py-3">{position.market ?? position.exchange}</td><td class="px-3 py-3">{position.currency}</td><td class="px-3 py-3 text-right" class:text-destructive={Number(position.quantity) < 0}>{number(position.quantity)}</td><td class="px-3 py-3 text-right">{money(position.current_price, String(position.currency))}</td><td class="px-3 py-3 text-right">{money(position.market_value, String(position.currency))}</td><td class="px-3 py-3 text-right" class:text-destructive={Number(position.unrealized_pl) < 0}>{money(position.unrealized_pl, String(position.currency))}</td></tr>
							{/each}
						</tbody>
					</table>
				{:else}
					<p class="py-12 text-center text-sm text-muted-foreground">No open positions are currently reported by the local paper account.</p>
				{/if}
			</Card.Content>
		</Card.Root>
	</div>
</section>
