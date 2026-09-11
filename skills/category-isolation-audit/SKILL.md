Category Isolation Audit

When to use

Use this skill for ecommerce, marketplace, catalog, and collection-oriented sites where multiple category/listing pages can compete for similar user intent.

Inputs

Target URL/domain.

Crawl cache containing cache_index.json and cached HTML pages.

Procedure

Load the crawl manifest and keep only usable normal HTML pages.

Treat page_type=category, collection, listing, or catalog as strong evidence, but do not require those labels.

Recover likely category pages from category-like URL paths (/c/, /category/, /collections/, etc.) and listing-page structure such as product links, breadcrumbs, filters, and listing headings.

Exclude product-detail pages using both manifest labels and product-like URL patterns.

Require at least two detected category/collection pages before comparing boundaries. When fewer than two are available, report an evidence limitation; do not imply the category architecture passed.

Check for:

very thin category-specific copy on genuine product-grid pages;

near-duplicate descriptive copy between sibling categories;

unusually heavy product overlap across multiple categories, only when enough product-link evidence exists.

Emit evidence with sample URLs and counts. Avoid inferring a defect from page classification alone.

Return JSON findings only; never modify a live site.

Output

Each finding must contain id, title, severity, evidence, and suggested_action. Use severity info for evidence limitations and passes.