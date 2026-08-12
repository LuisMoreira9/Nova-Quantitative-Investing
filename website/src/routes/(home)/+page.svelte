<script>
	import Hero from './Hero.svelte';
	import Partners from './Partners.svelte';
	import Publications from './Publications.svelte';
	import Team from './Team.svelte';
	import Testimonial from './Testimonial.svelte';

	let { data } = $props();

	const posts = $derived(data.posts);
</script>

<Hero />
<Publications {posts} />
<!-- <Partners /> -->
<Testimonial />
<Team />
