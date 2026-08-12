<script lang="ts">
	import Calendar from "@lucide/svelte/icons/calendar"
	import Clock from "@lucide/svelte/icons/clock"
	import Share2 from "@lucide/svelte/icons/share-2"

	import { Badge } from "$lib/components/ui/badge"
	import { Button } from "$lib/components/ui/button"
	import { page } from "$app/state"

	let { data } = $props()

	let {
		Content,
		meta: { date, title, readTime, categories }
	} = data
</script>

<article class="container mx-auto px-4 py-12 md:py-32">
	<!-- Article Header -->
	<header class="mx-auto max-w-3xl">
		<div class="my-8 flex items-center justify-between align-middle">
			<h1 class="mb-6 text-4xl font-bold leading-tight tracking-tighter sm:text-5xl">
				{title}
			</h1>

			<div class="flex gap-2">
				<Button
					variant="outline"
					size="icon"
					onclick={() =>
						navigator.share({
							url: page.url.toString()
						})}
				>
					<Share2 class="h-4 w-4" />
				</Button>
			</div>
		</div>

		<div class="mb-8 flex flex-wrap items-center gap-6">
			<div class="flex items-center gap-2 text-muted-foreground">
				<Calendar class="h-4 w-4" />
				<span>{date}</span>
			</div>
			<div class="flex items-center gap-2 text-muted-foreground">
				<Clock class="h-4 w-4" />
				<span>{readTime}</span>
			</div>
		</div>

		<div class="mb-8 flex flex-wrap gap-2">
			{#each categories as tag, index (index)}
				<Badge variant="secondary">{tag}</Badge>
			{/each}
		</div>
	</header>

	<!-- Article Content -->
	<div class="prose prose-slate mx-auto max-w-3xl dark:prose-invert">
		{@render Content()}
	</div>
</article>
