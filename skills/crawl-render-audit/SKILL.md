---
name: crawl-render-audit
description: Audits cached web pages for server-side vs client-side rendering (CSR/SSR), noindex directives, and robots.txt rules that affect AI discoverability.
license: MIT
allowed-tools: [python]
metadata:
  author: C_Cube
  version: "1.0.0"
---

# crawl-render-audit

## When to use
When determining if a brand's website is readily discoverable by pure AI search indexing agents (like Perplexity, ChatGPT Search, Claude-SearchBot) and not heavily reliant on client-side JavaScript execution to render core content.

## Inputs
- `--url`: The target URL to audit (used for context in `robots.txt` checking).
- `--cache-dir`: Directory containing the crawl manifest (`cache_index.json`) and downloaded HTML artifacts. (Defaults to `audit-cache`).
- `--no-live-render`: Optional flag to skip live rendering checks (though live render is largely disabled/stubbed out).

## Procedure
1. Reads `cache_index.json` to discover crawled pages.
2. Checks `robots.txt` for disallow rules targeting known AI and discoverability bots.
3. Checks each cached HTML file for `noindex` headers/meta tags.
4. Performs a heuristic static analysis on the raw HTML of sampled normal pages to detect CSR (empty root IDs, loading placeholders, heavy JS bundlers with thin text).
5. Compiles findings detailing any barriers to AI discoverability.

## Output
A JSON array of findings. Each finding object contains:
- `id`: Finding identifier.
- `title`: Short description.
- `severity`: "info", "low", "medium", or "high".
- `evidence`: Detailed string with affected URLs and metrics.
- `suggested_action`: Actionable advice with `summary` and `priority` (if applicable).

**Detection Logic:**
- **Robots Blocking:** Parses `robots.txt` using `urllib.robotparser` to see if bots like `GPTBot`, `Claude-SearchBot`, `PerplexityBot` are disallowed.
- **Noindex:** Searches HTML `meta` tags and response headers for `noindex` values.
- **CSR Heuristics:**
  - Looks for standard framework root elements (e.g., `id="root"`, `id="__next"`).
  - Checks if root elements contain loading text (e.g., "loading", "please wait") or are very short (< 80 chars).
  - Flags explicit `<noscript>` tags asking users to enable JavaScript.
  - Detects low text-to-HTML ratios combined with framework script injections (React, Vue, Angular, Nuxt, Next).

**Severity:**
- **High**: Explicit AI agents blocked in `robots.txt`. Widespread `noindex` tags. Clear loading placeholders or completely empty framework mount points requiring JS. No crawlable evidence available.
- **Medium**: `<noscript>` JS requirements. Bundler scripts with very thin text. Low text-to-HTML ratios. Access-restricted (auth wall) pages.
- **Info**: Verified static/server-rendered content, or explicitly allowed by `robots.txt`.

**Suggested Actions:**
Recommendations are provided, such as migrating to Server-Side Rendering (SSR) or Static Site Generation (SSG), removing `noindex` tags, or adjusting `robots.txt` rules for intended AI search engines.
