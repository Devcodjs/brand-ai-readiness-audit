name: network-accessibility-audit
description: Evaluates crawl accessibility and search/index discoverability by interpreting the network pipeline results (robots.txt, WAF, HTTP status) and checking for fallback data syndication like sitemaps.
license: MIT

# Network Accessibility & Discoverability Audit

## When to use
Use this skill to determine if AI agents can directly fetch a website, record exactly why it failed (e.g., 403 WAF, robots disallow), and check if the site can still be grounded through search infrastructure via sitemaps.

## Inputs
- `url` (string): The target website URL.
- `cache-dir` (string): The path to the local crawler cache containing `cache_index.json`.

## Procedure
1. Read the `cache_index.json` file from the provided `cache-dir`.
2. Evaluate `meta.crawl_status` to determine if the site blocked headless fetching via WAF (403), rate limiting (503), or robots.txt rules.
3. Evaluate `meta.sitemap_present` to determine if secondary search/index discoverability is possible when direct crawling fails.
4. Output a deterministic JSON array of findings with targeted mitigation strategies.

## Output
Returns a JSON array of findings, each containing an `id`, `title`, `severity`, `evidence`, and `suggested_action`.