<script lang="ts">
	import Calendar from '@lucide/svelte/icons/calendar';
	import Clock from '@lucide/svelte/icons/clock';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import * as Card from '$lib/components/ui/card';
	import { fade } from 'svelte/transition';
	import { Input } from '$lib/components/ui/input/index.js';

	import type { PageProps } from './$types';
	import Search from '@lucide/svelte/icons/search';
	import type { Post } from '$lib/types';

	let { data }: PageProps = $props();

	const posts = $derived(data.posts);

	// Search query
	let input: string = $state('');
	let query: string = $derived(input.trim());
</script>

<section class="container mx-auto px-4 py-12 md:py-32">
	<!-- Header -->
	<div class="mx-auto max-w-3xl text-center">
		<h1 class="mb-4 text-4xl font-bold tracking-tight sm:text-5xl font-heading">Blog</h1>
		<p class="mb-12 text-lg text-muted-foreground">
			Discover our latest posts about quantitative finance, trading and investing.
		</p>
	</div>

	<!-- Search -->
	<div class="mx-auto mb-12 flex max-w-lg items-center gap-2">
		<div class="relative flex-1">
			<Search class="absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
			<Input bind:value={input} type="search" placeholder="Search posts..." class="pl-10" />
		</div>
	</div>

	{#if posts.length > 0}
		{@const filtered = posts.filter(
			(post: Post) =>
				post.title.toLowerCase().includes(query.toLowerCase()) ||
				post.description.toLowerCase().includes(query.toLowerCase()) ||
				post.categories.some((tag) => tag.toLowerCase().includes(query.toLowerCase()))
		)}

		{#if filtered.length > 0}
			<div class="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
				{#each filtered as post, index (index)}
					<div in:fade class="overflow-hidden">
						{@render postCard(post)}
					</div>
				{/each}
			</div>
		{:else}
			<p class="text-center text-muted-foreground">
				No posts found matching "<strong>{query}</strong>".
                <br />
                Try adjusting your search or explore our latest posts.
			</p>
		{/if}
	{/if}
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
