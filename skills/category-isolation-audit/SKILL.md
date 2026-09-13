---
name: category-isolation-audit
description: Audits ecommerce, marketplace, and catalog sites for category isolation, ensuring category boundaries are distinct and not cannibalizing intent.
license: MIT
allowed-tools: [python]
metadata:
  author: C_Cube
  version: "1.0.0"
---

# category-isolation-audit

## When to use
Use this skill for ecommerce, marketplace, catalog, and collection-oriented sites where multiple category/listing pages exist and may inadvertently compete for similar user intent or confuse AI search summarizations.

## Inputs
- `--url` (string): Target website URL.
- `--cache-dir` (string): Directory containing the crawl manifest (`cache_index.json`) and cached HTML pages.

## Procedure
1. Load the crawl manifest (`cache_index.json`) and keep only usable normal HTML pages.
2. Exclude pages classified as product, home, or search, as well as URLs matching product patterns.
3. Identify category/collection candidate pages by examining explicit manifest labels (`page_type`), category-like URL paths, breadcrumbs, and listing-page structural signals (e.g., product links, headings).
4. Require at least two detected category/collection pages to perform boundary comparisons.
5. Extract textual descriptions from candidate pages, stripping out generic boilerplate copy.
6. Check for very thin category-specific copy on product-grid pages.
7. Compare descriptive copy between category pages to find near-duplicate text.
8. Analyze product link overlap across multiple categories to detect heavy cross-listing.
9. Aggregate findings and output them as a JSON array to standard output.

## Output
Returns a JSON array of finding objects to standard output. Each finding contains `id`, `title`, `severity`, `evidence`, and a `suggested_action` (with `summary` and `priority`).

**Detection Logic:**
- **CIA-THIN-001 (Thin Copy)**: Flags category pages that have a product grid but contain fewer than a threshold of unique descriptive words (e.g., < 20 words).
- **CIA-OVERLAP-002 (Duplicate Copy)**: Identifies pairs of category pages whose normalized descriptive copy exceeds a high similarity threshold (e.g., > 82%).
- **CIA-CROSSLIST-003 (Heavy Cross-Listing)**: Detects when a significant percentage of unique products appear across an unusually high number of distinct category pages, suggesting semantic cannibalization.
- **CIA-INSUFFICIENT-000**: Emitted as an info-level finding when fewer than two category candidates are found, preventing the audit from running.
- **CIA-DETECTION-FALLBACK-000**: Emitted as info when category pages are detected heuristically rather than via explicit crawler labels.

**Severity:**
- **High**: Widespread thin copy (>60% of categories), pervasive duplicate copy affecting more than half of the categories, or extreme product overlap (>80%).
- **Medium**: Moderate occurrences of thin copy, duplicate copy, or product cross-listing that dilutes semantic relevance.
- **Info**: Passing scenarios (no strong isolation issues), detection fallbacks, or insufficient category pages to run the audit.

**Suggested Actions:**
Actionable recommendations are provided for each defect:
- **Thin Copy**: Use PIM data or automated pipelines to inject keyword-rich category descriptions.
- **Duplicate Copy**: Consolidate utility facets with canonical tags or use dynamic templates to pull unique attributes for distinct categories.
- **Heavy Cross-Listing**: Convert overlapping facet routes into URL parameters (e.g., `?color=red`) that canonicalize to the parent category.
