---
name: intent-continuity-audit
description: Evaluates how well a website maintains user intent and supports AI-driven discovery, deep-linking, and extraction.
license: MIT
allowed-tools: [python]
metadata:
  author: C_Cube
  version: "1.0.0"
---

# intent-continuity-audit

## When to use
Use this skill when assessing a brand's AI readiness, specifically to determine if AI-generated summaries, deep links, or user referrals will encounter roadblocks (e.g., popups, lack of anchor tags, or missing meta information) that disrupt the user's intent or the AI's ability to interpret the page.

## Inputs
*   `--url`: The target URL of the website being audited.
*   `--cache-dir`: The directory path containing the cached HTML pages and `cache_index.json`.

## Procedure
1.  **Load Pages**: Reads `cache_index.json` and loads valid HTML files from the cache directory.
2.  **Site Typology Detection**: Analyzes text content across pages to classify the site as `commercial`, `informational`, or `general` based on keyword density.
3.  **Execute Core Checks**: Runs 10 distinct heuristic checks across all valid pages, skipping irrelevant checks based on site typology.
4.  **Execute Proactive Checks**: Evaluates the presence of proactive features like Schema.org markup and smart 404 handling.
5.  **Report**: Outputs a JSON array of findings with evidence, severity, and suggested actions.

## Output
A JSON array printed to standard output containing finding objects. Each object includes `id`, `title`, `severity`, `evidence`, and `suggested_action` (which contains `summary` and `priority`).

**Detection Logic:**
*   **ICA-OVERLAY-001 (Overlay & Interstitial Blockade)**: Detects fixed/absolute positioned overlays, dialogs, and paywalls that obscure content.
*   **ICA-ANCHOR-001 (Deep-Link Anchor Targets)**: Checks for the presence of `id` attributes on headings and section elements for deep-linking.
*   **ICA-URLPARAM-001 (Referral Context Handling)**: Looks for client-side scripts processing URL parameters (e.g., UTMs) or modern app bundlers.
*   **ICA-TRANSACTIONAL-001 (Above-Fold Transactional Content)**: For commercial sites, ensures primary transaction keywords appear in the top 25% of the body text.
*   **ICA-TITLE-001 (Title Uniqueness & Specificity)**: Identifies missing or duplicate `<title>` tags across pages.
*   **ICA-META-001 (Meta Descriptions)**: Checks for the presence of standard or Open Graph description meta tags.
*   **ICA-CTA-001 (Actionable Next Steps / CTAs)**: Evaluates the presence of action-oriented links or buttons in the main content area.
*   **ICA-CONVERSION-001 (Primary Conversion Endpoints)**: For commercial domains, ensures interactive conversion paths (forms, tel links, mailto, checkout links) exist.
*   **ICA-BREADCRUMB-001 (Breadcrumb Taxonomy)**: Checks sub-pages for breadcrumb navigation elements or `BreadcrumbList` schema.
*   **ICA-ORPHAN-001 (Contextual Internal Linking)**: Identifies "orphan" pages lacking contextual internal links within the primary body text.
*   **Proactive Checks**: Recommends `SearchAction` schema, `FAQPage` schema, and recovery-oriented 404 pages.

**Severity:**
Findings are classified into standard severity levels:
*   `critical`: Severe roadblocks like widespread missing titles, login-walls on multiple pages, or zero conversion endpoints on commercial sites.
*   `high`: Significant issues like prevalent overlays, lack of referral context parsing, or missing meta descriptions on most pages.
*   `medium`: Moderate issues like sparse deep-link targets, buried transactional content, or missing breadcrumbs.
*   `low`: Minor optimizations or missing proactive features.
*   `info`: No issues found or script skipped due to missing data.

**Suggested Actions:**
Each finding provides a specific, actionable summary. Examples include configuring edge routers to bypass overlays for AI bots, auto-generating heading IDs, updating CMS templates to include dynamic meta descriptions, and adding specific JSON-LD schema markup.
