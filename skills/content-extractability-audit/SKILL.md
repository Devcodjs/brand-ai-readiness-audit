---
name: content-extractability-audit
description: >
  Scans DOM for informational content locked in raster images, canvas elements,
  SVGs, videos, and iframes that lack semantic text equivalents. Detects when
  transactional facts (prices, specs, availability) are conveyed visually
  without machine-readable fallbacks like alt text, <dl> tables, or captions.
  Use when auditing whether AI systems and users can extract key information
  from a page without visual rendering.
license: MIT
metadata:
  author: team-c-cube
  version: "1.0.0"
---

# Content Extractability Audit

## When to use
Run this skill when you need to determine whether a website's informational content is locked inside non-textual UI elements (images, canvas, SVGs, video, iframes) that are invisible to AI crawlers and screen readers.

## Inputs
- `--url` (string): The target website URL being audited.
- `--cache-dir` (string): Absolute path to the audit-cache directory containing crawled HTML files.

## Procedure
1. Load all cached HTML pages from the cache directory.
2. For each page, extract the content area and run 8 checks:
   - CEA-001: Images without meaningful alt text
   - CEA-002: Canvas elements without text fallbacks
   - CEA-003: SVGs without accessible text (<title>/<desc>/aria-label)
   - CEA-004: Video/audio without captions or transcripts
   - CEA-005: Data tables rendered as images instead of HTML
   - CEA-006: External iframes without title attributes
   - CEA-007: Noscript content suggesting JS-locked information
   - CEA-008: CSS background images used for informational content
3. Aggregate findings across all pages — one finding per issue type.
4. Print JSON array of findings to stdout.

## Output
Returns a JSON array of findings, each with: id, title, severity, evidence, suggested_action.
