<script lang="ts">
	import { LineChart } from 'layerchart';
	import TrendingUpIcon from '@lucide/svelte/icons/trending-up';
	import { scaleUtc } from 'd3-scale';
	import { curveNatural } from 'd3-shape';
	import * as Chart from '$lib/components/ui/chart/index.js';
	import * as Card from '$lib/components/ui/card/index.js';

	const chartData = [
		{ date: new Date('2024-01-01'), desktop: 186 },
		{ date: new Date('2024-02-01'), desktop: 305 },
		{ date: new Date('2024-03-01'), desktop: 237 },
		{ date: new Date('2024-04-01'), desktop: 73 },
		{ date: new Date('2024-05-01'), desktop: 209 },
		{ date: new Date('2024-06-01'), desktop: 214 }
	];

	const chartConfig = {
		desktop: { label: 'Desktop', color: 'var(--chart-1)' }
	} satisfies Chart.ChartConfig;
</script>

<Card.Root
	class="relative mx-auto max-w-6xl overflow-hidden border shadow-lg inset-shadow-2xs shadow-zinc-950/15 ring-background dark:inset-shadow-white/20"
>
	<Card.Header>
		<Card.Title>Portfolio Performance</Card.Title>
		<Card.Description>Showing performance over the last 6 months</Card.Description>
	</Card.Header>
	<Card.Content>
		<Chart.Container config={chartConfig}>
			<LineChart
				data={chartData}
				x="date"
				xScale={scaleUtc()}
				axis="x"
				series={[
					{
						key: 'desktop',
						label: 'Desktop',
						color: chartConfig.desktop.color
					}
				]}
				props={{
					spline: { curve: curveNatural, motion: 'tween', strokeWidth: 2 },
					xAxis: {
						format: (v: Date) => v.toLocaleDateString('pt-PT', { day: 'numeric', month: 'numeric' })
					},
					highlight: { points: { r: 4 } }
				}}
			>
				{#snippet tooltip()}
					<Chart.Tooltip hideLabel />
				{/snippet}
			</LineChart>
		</Chart.Container>
	</Card.Content>
</Card.Root>
