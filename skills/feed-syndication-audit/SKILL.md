name: feed-syndication-audit
description: Analyzes XML sitemaps to verify if the site exposes a machine-readable URL graph for search infrastructure grounding when direct crawling is restricted.
license: MIT

## Procedure
1. Reads `sitemap.xml` from the local audit-cache.
2. Uses standard `xml.etree.ElementTree` to parse the file without external API dependencies.
3. Counts the `<loc>` tags to establish the size of the publicly exposed information architecture.
4. Outputs findings based on the presence and scale of the syndication graph.