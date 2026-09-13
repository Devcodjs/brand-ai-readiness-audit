---
name: feed-syndication-audit
description: Analyzes the site's XML sitemaps to verify syndication feeds are properly exposed for AI indexing.
license: MIT
allowed-tools: [python]
metadata:
  author: C_Cube
  version: "1.0.0"
---

# feed-syndication-audit

## When to use
This skill should be executed after a domain has been successfully crawled (or attempted to crawl) and the output is stored in a local cache directory.

## Inputs
- `--url`: The target website URL (used for logging or context, not actively fetched in this script).
- `--cache-dir`: The absolute path to the orchestrator's local cache directory containing `sitemap.xml` and `cache_index.json`.

## Procedure
1. Reads `cache_index.json` from the cache directory to check the overall `crawl_status`.
2. Checks for the existence of `sitemap.xml`.
3. If missing, but `crawl_status` is `"success"`, flags the missing sitemap.
4. If present, attempts to parse the XML.
5. Extracts all `<loc>` tags to count the total exposed URLs.
6. Assesses whether the sitemap is populated or empty, generating findings based on the results.

## Output
Returns a JSON array of findings to stdout. Each finding contains:
- `id`: Unique identifier for the finding type.
- `title`: Human-readable summary of the issue.
- `severity`: high, medium, or info.
- `evidence`: Specific details explaining why the finding was flagged.
- `suggested_action`: An object containing a `summary` of steps to take and a `priority` level.

**Detection Logic:**
- **Missing Public XML Sitemap (`FEED-SYNC-MISSING`)**: Triggered if `sitemap.xml` is missing but the domain was openly crawlable (`crawl_status == "success"`).
- **Machine-Readable URL Graph Detected (`FEED-SYNC-VALID`)**: Triggered if the sitemap parses successfully and contains one or more `<loc>` tags.
- **Sitemap Exists but is Empty (`FEED-SYNC-EMPTY`)**: Triggered if the sitemap parses successfully but contains zero `<loc>` tags.
- **Malformed XML Sitemap (`FEED-SYNC-MALFORMED`)**: Triggered if standard XML parsing fails on `sitemap.xml`.

**Severity:**
- **High**: Empty sitemap. The syndication feed is fundamentally broken.
- **Medium**: Missing sitemap on a successfully crawled site, or a malformed XML sitemap preventing parsing.
- **Info**: Valid sitemap correctly parsed.

**Suggested Actions:**
- **Missing**: Publish a standard `sitemap.xml` to guarantee rapid indexing, as AI engines rely on this to avoid brute-forcing.
- **Valid**: Ensure the sitemap is submitted via Google Search Console and Bing Webmaster Tools.
- **Empty**: Regenerate the sitemap to populate it with valid canonical URLs.
- **Malformed**: Correct the sitemap syntax to strictly follow XML standards so AI indexers won't drop it.
