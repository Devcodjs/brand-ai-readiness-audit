---
name: entity-graph-validator
description: Validates site entity graph from a crawl manifest, checking for Organization/Brand, sameAs, naming conflicts, Product coverage, and malformed JSON-LD.
license: MIT
allowed-tools: [python]
metadata:
  author: C_Cube
  version: "1.0.0"
---

# entity-graph-validator

## When to use
Run this skill when auditing a brand's AI readiness to verify that AI systems, search engines, and LLMs can extract a canonical, unambiguous, and machine-readable understanding of the brand's identity, external consensus (sameAs links), and product catalog.

## Inputs
- `--url` (string): Target website URL.
- `--cache-dir` (string): Directory containing the crawl manifest (`cache_index.json`) and cached HTML pages. Default is `audit-cache`.

## Procedure
1. Load the crawl manifest (`cache_index.json`).
2. If manifest is missing or empty, perform a fallback single-page fetch on the target URL.
3. Filter usable, normal HTML pages, bypassing bot challenges.
4. Extract `application/ld+json` blocks from the usable HTML pages.
5. Identify Organization/Brand and Product nodes.
6. Evaluate naming consistency, sameAs link coverage, and required schema properties.
7. Detect malformed JSON-LD blocks.
8. Assess product page coverage for Product schema.
9. Aggregate findings and generate proactive recommendations.
10. Print a JSON array of findings to standard output.

## Output
Returns a JSON object containing three arrays:
- `findings`: A list of findings with `id`, `title`, `severity`, `evidence`, and `suggested_action`.
- `evidence`: Metadata about the execution, page counts, and confidence.
- `proactive_actions`: A list of proactive schema recommendations.

**Detection Logic:**
- **ENTITY-HIGH-001**: Detects the complete absence of valid JSON-LD structured data across the site.
- **ENTITY-HIGH-002**: Identifies the absence of a core Organization, Brand, Corporation, or LocalBusiness node when JSON-LD is otherwise present.
- **ENTITY-MED-003 (Weak External Consensus)**: Triggers when the Organization/Brand node has fewer than 2 unique `sameAs` links.
- **ENTITY-MED-004 (Incomplete Organization Nodes)**: Flags Organization nodes missing required properties (`name`, `url`, `logo`).
- **ENTITY-MED-005 (Inconsistent Organization Name)**: Detects varying canonical names for the Organization/Brand across different pages.
- **ENTITY-PRODUCT-006**: Flags product pages that lack corresponding Product/ProductGroup schema.
- **ENTITY-MED-007**: Detects malformed JSON-LD scripts that fail JSON parsing.
- **ENTITY-PROACTIVE-008**: Suggests explicit canonical entity naming when sameAs coverage is strong but name is missing.
- **ENTITY-EVIDENCE-001**: Emitted when the audit is suppressed due to no normal HTML evidence or a blocked/challenge response.

**Severity:**
- **High**: Severe semantic blockers such as no JSON-LD, missing core Organization/Brand entities, missing Product schema on most product pages, or no normal HTML evidence.
- **Medium**: Partial issues like weak sameAs coverage, inconsistent organization naming, missing properties, and malformed JSON-LD.
- **Low/Info**: Passing scenarios, robust entity graphs, and proactive cross-linking suggestions.

**Suggested Actions:**
Each finding provides a specific, actionable `summary` and `priority`. Actions range from adding valid JSON-LD, declaring a canonical Organization node with stable name/URL/logo, adding official social/knowledge-graph `sameAs` links, and fixing JSON syntax errors in templates.
