---
name: content-freshness-audit
description: Audits machine-readable and visible freshness signals on a website, including structured data dates, sitemap lastmod tags, and footer copyright years.
license: MIT
allowed-tools: [python]
metadata:
  author: C_Cube
  version: "1.0.0"
---

# content-freshness-audit

## When to use
Use this skill when auditing a brand's AI readiness, specifically to ensure that AI assistants and crawlers can accurately determine when content was published or last updated, preventing hallucinations regarding temporal relevance.

## Inputs
*   `--url`: The target URL of the website.
*   `--cache-dir`: Directory containing the crawled evidence (`cache_index.json`, cached HTML files, and `sitemap.xml`).

## Procedure
1. Load cached pages listed in `cache_index.json`.
2. Evaluate independent usability of pages, rejecting bot challenges or block pages via heuristic text/DOM scanning.
3. Parse JSON-LD structured data (`application/ld+json`) on valid pages to find freshness-relevant schema nodes.
4. Check for `dateModified` or `datePublished` tags in those nodes.
5. Identify any malformed JSON-LD structured data blocks.
6. Scan page footers (`<footer>` tags or elements with footer classes) for outdated copyright years.
7. Parse `sitemap.xml` to evaluate the presence of `<lastmod>` tags.
8. Output a flat JSON list of findings based on evaluated evidence thresholds.

## Output
A flat JSON array of findings containing `id`, `title`, `severity`, `evidence`, and `suggested_action` (containing `summary` and `priority`).

**Detection Logic:**
*   **FRESH-001 (Missing Machine-Readable Freshness Signals)**: Triggers if more than 50% of freshness-relevant schema nodes (on at least 2 pages) lack both `dateModified` and `datePublished`.
*   **FRESH-002 (Outdated Footer Copyright Year)**: Triggers if the parsed copyright year in the footer is older than the current year.
*   **FRESH-003 (Missing Sitemap Modification Timestamps)**: Triggers if more than 50% of `<url>` entries in `sitemap.xml` are missing a `<lastmod>` tag.
*   **FRESH-004 (Malformed JSON-LD Structured Data)**: Triggers if JSON-LD nodes fail to parse due to syntax errors, rendering them opaque to AI.
*   **FRESH-EVIDENCE-000 / 001**: Emitted when zero pages are cached, or all pages are rejected as bot challenges, respectively.
*   **FRESH-PASS-000**: Emitted when all checks pass and no defects cross reporting thresholds.

**Severity:**
*   **High**: Missing or rejected crawl evidence (prevents page-level analysis entirely).
*   **Medium**: Missing structured data freshness signals, missing sitemap `<lastmod>` tags, and malformed JSON-LD.
*   **Low**: Outdated footer copyright years (treated as a weak supporting signal).
*   **Info**: No freshness defects detected (ALL-CLEAR).

**Suggested Actions:**
Actionable guidance is provided for each defect:
*   **For missing schema dates**: Add accurate `dateModified` and `datePublished` properties to relevant schema nodes.
*   **For missing sitemap dates**: Configure the CMS to emit accurate `<lastmod>` timestamps in the XML sitemap.
*   **For malformed schemas**: Validate JSON-LD output against a JSON parser in the build/publish pipeline.
*   **For outdated copyright**: Update the site-wide footer copyright year.
