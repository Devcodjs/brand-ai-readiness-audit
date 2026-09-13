---
name: content-extractability-audit
description: Scans DOM for informational content locked in non-semantic elements without text fallbacks. Detects when data is conveyed visually without machine-readable equivalents.
license: MIT
allowed-tools: [python]
metadata:
  author: C_Cube
  version: "1.0.0"
---

# content-extractability-audit

## When to use
Run this skill when auditing a website's readiness for AI consumption, focusing on whether data (like specifications, pricing, tables, and visual explanations) is accessible in a machine-readable text format.

## Inputs
- `--url` (string): The target website URL being audited.
- `--cache-dir` (string): Absolute path to the audit-cache directory containing crawled HTML files and `cache_index.json`.

## Procedure
1. Load all successfully cached HTML pages from the `cache_dir` via `cache_index.json`.
2. Extract the primary content area (`<main>`, `<article>`, or `<body>`).
3. Execute standard checks (`CEA-001` through `CEA-009`) against the parsed DOM.
4. Execute proactive checks (`CEA-PRO-001` through `CEA-PRO-003`).
5. Aggregate findings across all parsed pages, outputting one unique finding per issue ID.
6. Print a JSON array of findings to standard output.

## Output
Returns a JSON array of findings to standard output. Each finding object contains `id`, `title`, `severity`, `evidence`, and `suggested_action` (with `summary` and `priority`).

**Detection Logic:**
- **CEA-001 (Images Without Alt Text)**: Scans informational images (skipping decorative, tiny, or structural UI elements) missing `alt` attributes.
- **CEA-002 (Canvas Without Fallbacks)**: Detects `<canvas>` lacking adjacent text equivalents (e.g., `<table>`, `<dl>`, `<figcaption>`).
- **CEA-003 (SVGs Without Accessible Text)**: Identifies informational `<svg>` tags lacking `<title>`, `<desc>`, or ARIA labels.
- **CEA-004 (Video/Audio Without Captions)**: Checks `<video>` and `<audio>` tags for `<track kind="captions">` or adjacent transcript links.
- **CEA-005 (Data Tables as Images)**: Flags images with data-suggestive names/alt-text (e.g., "table", "spec") without semantic table markup.
- **CEA-006 (Iframes Without Title)**: Spots external `<iframe>` embeds missing a `title` attribute.
- **CEA-007 (Noscript JS-Locked Content)**: Evaluates `<noscript>` blocks for substantive content that implies key information requires JavaScript.
- **CEA-008 (Background Images for Informational Content)**: Looks for elements using CSS `background-image` in content areas with no text children.
- **CEA-009 (Missing JSON-LD)**: Checks product pages for `Product` structured data.
- **Proactive Checks**: Recommends `speakable` schema, wrapping images in `<figure>` with `<figcaption>`, and avoiding `data-nosnippet` on useful content.

**Severity:**
- **Critical/High**: When a large percentage (e.g., >40-50%) of elements or pages are missing crucial fallbacks (e.g., missing alt text, canvas without tables, missing JSON-LD).
- **Medium**: Moderate occurrences (e.g., >20-30%) of missing semantic fallbacks.
- **Low/Info**: Proactive best practices (e.g., `speakable` schema, wrapping in `<figure>`).

**Suggested Actions:**
Each finding includes a `suggested_action.summary` with specific remediation steps (e.g., "Add descriptive alt text," "Replace image-based data presentations with semantic HTML <table>," or "Add WebVTT caption tracks").
