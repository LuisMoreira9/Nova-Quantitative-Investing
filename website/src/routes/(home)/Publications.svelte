<script lang="ts">
	import type { Post } from '$lib/types';
	import * as Card from '$lib/components/ui/card/index.js';
	import { Badge } from '$lib/components/ui/badge/index.js';
	import { Button } from '$lib/components/ui/button/index.js';

	import Calendar from '@lucide/svelte/icons/calendar';
	import Clock from '@lucide/svelte/icons/clock';

	let { posts }: { posts: Post[] } = $props();
</script>

<section class="py-12 md:py-32">
	<div class="mx-auto max-w-7xl px-8 lg:px-0">
		<h2 class="mb-8 font-heading text-4xl font-bold md:mb-16 lg:text-5xl">Latest Publications</h2>

		<div class="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
			{#each posts as post, index (index)}
				{@render postCard(post)}
			{/each}
		</div>
	</div>
</section>

{#snippet postCard(post: Post)}
	<Card.Root>
		<Card.Header>
			<Card.Title class="line-clamp-2 text-xl">
				<h2>{post.title}</h2>
			</Card.Title>
			<Card.Description class="flex items-center gap-4 text-sm">
				<span class="flex items-center gap-1">
					<Calendar class="h-4 w-4" />
					{post.date}
				</span>
				<span class="flex items-center gap-1">
					<Clock class="h-4 w-4" />
					{post.readTime}
				</span>
			</Card.Description>
		</Card.Header>
		<Card.Content>
			<p class="line-clamp-3 text-sm text-muted-foreground">{post.description}</p>
			<div class="mt-4 flex flex-wrap gap-2">
				{#each post.categories as tag, index (index)}
					<Badge variant="secondary">{tag}</Badge>
				{/each}
			</div>
		</Card.Content>
		<Card.Footer>
			<Button variant="outline" class="w-full  overflow-hidden" href={`/blog/${post?.slug}`}
				>Read More</Button
			>
		</Card.Footer>
	</Card.Root>
{/snippet}
