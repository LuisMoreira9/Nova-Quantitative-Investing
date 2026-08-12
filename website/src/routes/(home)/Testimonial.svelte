<script>
	import { Avatar, AvatarImage, AvatarFallback } from "$lib/components/ui/avatar";
</script>

<section class="py-16 md:py-24">
	<div class="mx-auto max-w-5xl px-6">
		<div class="mx-auto max-w-2xl text-center">
			<blockquote>
				<p class="font-heading text-lg font-medium sm:text-xl md:text-3xl">
					Using TailsUI has been like unlocking a secret design superpower. It's the
					perfect fusion of simplicity and versatility, enabling us to create UIs that are
					as stunning as they are user-friendly.
				</p>

				<div class="mt-12 flex items-center justify-center gap-6">
					<Avatar class="size-12">
						<AvatarImage
							src="/members/guilherme-cruz.jpeg"
							alt="Guilherme Cruz"
							height="400"
							width="400"
							loading="lazy"
						/>
						<AvatarFallback>GUI</AvatarFallback>
					</Avatar>

					<div class="space-y-1 border-l pl-6">
						<cite class="font-medium">Guilherme Cruz</cite>
						<span class="block text-sm text-muted-foreground">President</span>
					</div>
				</div>
			</blockquote>
		</div>
	</div>
</section>
