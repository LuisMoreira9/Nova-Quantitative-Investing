<script lang="ts">
	import Button from '$lib/components/ui/button/button.svelte';
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import PortfolioChart from './PortfolioChart.svelte';
</script>

<div class="absolute inset-0 isolate hidden opacity-65 contain-strict lg:block">
	<div
		class="absolute top-0 left-0 h-320 w-140 -translate-y-87.5 -rotate-45 rounded-full bg-[radial-gradient(68.54%_68.72%_at_55.02%_31.46%,hsla(0,0%,85%,.08)_0,hsla(0,0%,55%,.02)_50%,hsla(0,0%,45%,0)_80%)]"
	></div>
	<div
		class="absolute top-0 left-0 h-320 w-60 [translate:5%_-50%] -rotate-45 rounded-full bg-[radial-gradient(50%_50%_at_50%_50%,hsla(0,0%,85%,.06)_0,hsla(0,0%,45%,.02)_80%,transparent_100%)]"
	></div>
	<div
		class="absolute top-0 left-0 h-320 w-60 -translate-y-87.5 -rotate-45 bg-[radial-gradient(50%_50%_at_50%_50%,hsla(0,0%,85%,.04)_0,hsla(0,0%,45%,.02)_80%,transparent_100%)]"
	></div>
</div>
<section>
	<div class="relative pt-24 pb-16">
		<div
			class="absolute inset-0 -z-10 size-full [background:radial-gradient(125%_125%_at_50%_100%,transparent_0%,var(--color-background)_75%)]"
		></div>
		<div class="mx-auto max-w-7xl px-6">
			<div class="text-center sm:mx-auto lg:mt-0 lg:mr-auto">
				<div>
					<a
						href="#link"
						class="group mx-auto flex w-fit items-center gap-4 rounded-full border bg-muted p-1 pl-4 shadow-md shadow-zinc-950/5 transition-colors duration-300 hover:bg-background dark:border-t-white/5 dark:shadow-zinc-950 dark:hover:border-t-border"
					>
						<span class="text-sm text-foreground">Recruitment Now Open</span>
						<span class="block h-4 w-0.5 border-l bg-white dark:border-background dark:bg-zinc-700"
						></span>

						<div
							class="size-6 overflow-hidden rounded-full bg-background duration-500 group-hover:bg-muted"
						>
							<div
								class="flex w-12 -translate-x-1/2 duration-500 ease-in-out group-hover:translate-x-0"
							>
								<span class="flex size-6">
									<ArrowRight class="m-auto size-3" />
								</span>
								<span class="flex size-6">
									<ArrowRight class="m-auto size-3" />
								</span>
							</div>
						</div>
					</a>
				</div>

				<h1 class="mt-8 font-heading text-6xl text-balance md:text-7xl lg:mt-16 xl:text-[5.25rem]">
					Nova Quant Club
				</h1>
				<p class="mx-auto mt-8 max-w-3xl text-lg text-balance">
					Join our community of passionate investors and researchers, where we share insights,
					strategies, and resources to help you succeed in the world of quantitative investing.
				</p>

				<div class="mt-12 flex flex-col items-center justify-center gap-2 md:flex-row">
					<Button href="/portfolio" size="lg" class="rounded-xl px-5 text-base">
						<span class="text-nowrap">Explore Portfolio</span>
					</Button>

					<Button href="/contact" size="lg" variant="ghost" class="rounded-xl px-5">Get in Touch</Button>
				</div>
			</div>
		</div>

		<!-- <div class="relative mt-8 -mr-56 overflow-hidden px-2 sm:mt-12 sm:mr-0 md:mt-20">
			<PortfolioChart />
		</div> -->
	</div>
</section>
