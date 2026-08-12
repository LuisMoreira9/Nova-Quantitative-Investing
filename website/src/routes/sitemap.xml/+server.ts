const pages = ['about', 'team', 'portfolio', 'blog', 'resources', 'contact'];

const posts = import.meta.glob('/src/posts/*.svx', {
	eager: true
});

const publishedPosts: { slug: string; lastModified: string }[] = [];

for (const post in posts) {
	const postMeta = posts[post].metadata;
	if (postMeta.published) {
		const slug = post.split('/').at(-1)?.replace('.svx', '');
		const lastModified = postMeta.date;
		publishedPosts.push({ slug, lastModified });
	}
}
console.log(publishedPosts); // an array of posts {slug, lastModified}

const site = 'https://novaquantclub.com';

/** @type {import('./$types').RequestHandler} */
export async function GET({ url }) {
	const body = sitemap(posts, pages);
	const response = new Response(body);
	response.headers.set('Cache-Control', 'max-age=0, s-maxage=3600');
	response.headers.set('Content-Type', 'application/xml');
	return response;
}

const sitemap = (
	posts: { slug: string; lastMod: string }[],
	pages: string[]
) => `<?xml version="1.0" encoding="UTF-8" ?>

<urlset
  xmlns="https://www.sitemaps.org/schemas/sitemap/0.9"
  xmlns:news="https://www.google.com/schemas/sitemap-news/0.9"
  xmlns:xhtml="https://www.w3.org/1999/xhtml"
  xmlns:mobile="https://www.google.com/schemas/sitemap-mobile/1.0"
  xmlns:image="https://www.google.com/schemas/sitemap-image/1.1"
  xmlns:video="https://www.google.com/schemas/sitemap-video/1.1"
>
  <url>
    <loc>${site}</loc>
    <changefreq>daily</changefreq>
    <priority>0.5</priority>
  </url>
  
  ${pages
		.map(
			(page) => `
  <url>
    <loc>${site}/${page}</loc>
    <changefreq>daily</changefreq>
    <priority>0.5</priority>
  </url>
  `
		)
		.join('')}

  ${publishedPosts
		.map(
			(post) => `
  <url>
    <loc>${site}/blog/${post.slug}</loc>
    <changefreq>weekly</changefreq>
    <lastmod>${post.lastModified}</lastmod>
    <priority>0.5</priority>
  </url>
  `
		)
		.join('')}
</urlset>`;
