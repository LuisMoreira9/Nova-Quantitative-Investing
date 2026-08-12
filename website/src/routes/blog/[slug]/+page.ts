import type { PageLoad } from "./$types"

export const load = (async ({ params }) => {
	const { slug } = params

	try {
		const post = await import(`../../../posts/${slug}.svx`)

		if (post?.metadata?.published) {
			
			return {
				Content: post?.default,
				meta: { ...post?.metadata, slug },
				title: post?.metadata?.title,
				description: post?.metadata?.description,
				keywords: post?.metadata?.categories,
				type: 'article'
				
			}
		}


	} catch (err) {
		// console.error("Error loading the post:", err)
		return {
			status: 500,
			error: `Could not load the post: ${err?.message || err}`
		}
	}
}) satisfies PageLoad


export const prerender = false