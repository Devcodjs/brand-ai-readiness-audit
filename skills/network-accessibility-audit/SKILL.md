---
name: Network Accessibility Audit
description: Analyzes how a brand's web properties handle access from AI search and training agents.
license: MIT
allowed-tools: [python]
metadata:
  author: C_Cube
  version: "1.0.0"
---

# Network Accessibility Audit

## When to use
Use this skill during an AI readiness audit to determine if a brand's technical infrastructure (WAF, robots.txt, rate limits) supports or hinders AI conversational search platforms from indexing or answering questions about the brand.

## Inputs
- `--url`: The target website URL.
- `--cache-dir`: The directory containing the output of a prior crawling phase (specifically `cache_index.json`).

## Procedure
1. Reads the `cache_index.json` from the provided cache directory.
2. Extracts metadata regarding the crawl status, failure reasons, blocked training bots, blocked search bots, and sitemap presence.
3. Evaluates these attributes against a set of predefined conditions to generate findings.
4. Outputs the findings in JSON format to `stdout`.

## Output
A JSON array of finding objects, each containing an `id`, `title`, `severity`, `evidence`, and `suggested_action` (with `summary` and `priority`).

**Detection Logic:**
- **NET-ACCESS-SEARCH-BLOCKED**: Triggered if `crawl_status == "blocked_by_robots"`. This indicates real-time AI search bots are explicitly blocked in `robots.txt`.
- **NET-ACCESS-WAF-INTERCEPT**: Triggered if `crawl_status == "blocked_by_waf"`. This indicates a WAF/CAPTCHA intercepted the request to the root URL (HTTP 403).
- **NET-ACCESS-RATE-LIMIT**: Triggered if `crawl_status == "rate_limited"`. This implies headless connections were dropped due to timeout or rate limit.
- **NET-ACCESS-TRAINING-BLOCKED**: Triggered if training bots are blocked, but search bots are not.
- **NET-DISC-NONE-OBSERVED**: Triggered if direct access failed (`blocked_by_waf`, `rate_limited`, `failed_root_fetch`, or `blocked_by_robots`) AND no sitemap is present.

**Severity:**
- **Critical**: Real-Time AI Search Bots Explicitly Blocked.
- **High**: Homepage Access Intercepted by WAF/CAPTCHA.
- **Medium**: Crawl Dropped via Timeout or Rate Limit, or Public Syndication Surface Not Observed.
- **Info**: LLM Training Bots Blocked (good IP protection).

**Suggested Actions:**
- **Search Blocked**: Review robots.txt restrictions and consider allowlisting agents like OAI-SearchBot and PerplexityBot.
- **WAF Intercept**: Verify WAF is configured to permit requests from verified AI search agents.
- **Rate Limit**: Monitor traffic rules to ensure AI IP ranges are not inadvertently tarpitted.
- **Training Blocked**: No immediate action required, configuration protects IP while maintaining search discoverability.
- **No Discovery**: Consider making an XML sitemap publicly accessible to improve retrievability.
