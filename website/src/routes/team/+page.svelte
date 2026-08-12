<script lang="ts">
	import * as Select from '$lib/components/ui/select/index.js';
	import * as Avatar from '$lib/components/ui/avatar/index.js';
	import { Label } from '$lib/components/ui/label/index.js';
	import { Button } from '$lib/components/ui/button/index.js';
	import { members } from '$lib/config';

	let semesters = $derived(Object.keys(members));

	const currentSemester = semesters[0] ?? '';

	let value = $state(currentSemester);

	const triggerContent = $derived(semesters.find((f) => f === value) ?? 'Select a semester');
</script>

<section class="py-12 md:py-32">
	<div class="mx-auto max-w-3xl px-8 lg:px-0">
		<h2 class="mb-8 font-heading text-4xl font-bold md:mb-16 lg:text-5xl">Our team</h2>

		<div class="pb-4">
			<Select.Root type="single" name="semester" bind:value>
				<Select.Trigger class="w-[180px]">
					{triggerContent}
				</Select.Trigger>
				<Select.Content>
					<Select.Group>
						{#each semesters as semester (semester)}
							<Select.Item value={semester} label={semester}>
								{semester}
							</Select.Item>
						{/each}
					</Select.Group>
				</Select.Content>
			</Select.Root>
		</div>

		{#if members[value]?.board.length > 0}
			<div>
				<h3 class="mb-6 text-lg font-medium">Board</h3>
				<div class="grid grid-cols-2 gap-4 border-t py-6 md:grid-cols-4">
					{#each members[value]?.board as member, index (index)}
						{@render memberCard(member)}
					{/each}
				</div>
			</div>
		{/if}

		{#if members[value]?.research.length > 0}
			<div class="mt-6">
				<h3 class="mb-6 text-lg font-medium">Research</h3>
				<div data-rounded="full" class="grid grid-cols-2 gap-4 border-t py-6 md:grid-cols-4">
					{#each members[value]?.research as member, index (index)}
						{@render memberCard(member)}
					{/each}
				</div>
			</div>
		{/if}

		{#if members[value]?.macro.length > 0}
			<div class="mt-6">
				<h3 class="mb-6 text-lg font-medium">Macro</h3>
				<div data-rounded="full" class="grid grid-cols-2 gap-4 border-t py-6 md:grid-cols-4">
					{#each members[value]?.macro as member, index (index)}
						{@render memberCard(member)}
					{/each}
				</div>
			</div>
		{/if}

		{#if members[value]?.operations.length > 0}
			<div class="mt-6">
				<h3 class="mb-6 text-lg font-medium">Operations</h3>
				<div data-rounded="full" class="grid grid-cols-2 gap-4 border-t py-6 md:grid-cols-4">
					{#each members[value]?.operations as member, index (index)}
						{@render memberCard(member)}
					{/each}
				</div>
			</div>
		{/if}
	</div>
</section>

{#snippet memberCard(member)}
	<div>
		<div class="relative size-24 rounded-full">
			<Avatar.Root class="h-24 w-24" id="avatar">
				<Avatar.Image src={member.avatar} alt={member.name} id="avatar-preview" />
				<Avatar.Fallback>{member.name.slice(0, 2)}</Avatar.Fallback>
			</Avatar.Root>
			<Label for="avatar" class="absolute -right-0.5 -bottom-0.5">
				<Button
					href={member.linkedin}
					target="_blank"
					rel="noopener noreferrer"
					variant="secondary"
					size="icon"
					class="rounded-full hover:cursor-pointer"
				>
					<svg xmlns="http://www.w3.org/2000/svg" width="1em" height="1em" viewBox="0 0 24 24">
						<path d="M0 0h24v24H0z" fill="none" />
						<path
							fill="currentColor"
							d="M19 3a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zm-.5 15.5v-5.3a3.26 3.26 0 0 0-3.26-3.26c-.85 0-1.84.52-2.32 1.3v-1.11h-2.79v8.37h2.79v-4.93c0-.77.62-1.4 1.39-1.4a1.4 1.4 0 0 1 1.4 1.4v4.93zM6.88 8.56a1.68 1.68 0 0 0 1.68-1.68c0-.93-.75-1.69-1.68-1.69a1.69 1.69 0 0 0-1.69 1.69c0 .93.76 1.68 1.69 1.68m1.39 9.94v-8.37H5.5v8.37z"
						/>
					</svg>
				</Button>
			</Label>
		</div>
		<span class="mt-2 block text-sm">{member.name}</span>
		<span class="block text-xs text-muted-foreground">{member.role}</span>
	</div>
{/snippet}
