<script lang="ts">
	import { members } from '$lib/config';
	import { resolve } from '$app/paths';

	const membersValues = Object.values(members);
	const membersList = [
		...membersValues[0].board,
		...membersValues[0].research,
		...membersValues[0].macro,
		...membersValues[0].operations
	];
</script>

<section class="py-16 md:py-24">
	<div class="mx-auto max-w-5xl px-6">
		<div class="text-center">
			<a href={resolve('/team')}>
				<h2 class="font-heading text-3xl font-semibold">Meet Our Team</h2>
			</a>
			<p class="mt-6">Get to know the talented individuals who make our community possible.</p>
		</div>
		<div class="mx-auto mt-12 flex max-w-lg flex-wrap justify-center gap-3">
			{#each membersList as member, index (index)}
				<a
					href={member.linkedin}
					rel="external"
					target="_blank"
					title={member.name}
					class="size-16 rounded-full border *:size-full *:rounded-full *:object-cover"
				>
					<img alt={member.name} src={member.avatar} loading="lazy" width={120} height={120} />
				</a>
			{/each}
		</div>
	</div>
</section>
