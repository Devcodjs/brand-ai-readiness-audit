---
name: local-omnichannel-audit
description: >
  Validates physical store and local business presence for AI discoverability.
  Checks for LocalBusiness/Store schema.org markup, PostalAddress completeness,
  GeoCoordinates, opening hours in structured data, NAP (Name, Address, Phone)
  consistency, and whether store location data is available in HTML text or
  locked inside client-side map widgets. Use when auditing whether AI assistants
  can answer questions about a business's physical locations, hours, and contact
  information.
license: MIT
metadata:
  author: team-c-cube
  version: "1.0.0"
---

# Local Omnichannel Audit

## When to use
Run this skill when you need to determine whether AI assistants can answer questions about a business's physical locations, operating hours, and contact information.

## Inputs
- `--url` (string): The target website URL being audited.
- `--cache-dir` (string): Absolute path to the audit-cache directory containing crawled HTML files.

## Procedure
1. Load all cached HTML pages from the cache directory.
2. For each page, extract JSON-LD structured data and visible text, then run 8 checks:
   - LOA-001: Missing LocalBusiness / Store schema.org markup
   - LOA-002: Incomplete PostalAddress in structured data
   - LOA-003: Missing GeoCoordinates
   - LOA-004: Missing opening hours in structured data
   - LOA-005: Physical address not in HTML text
   - LOA-006: NAP (Name, Address, Phone) inconsistency across pages
   - LOA-007: No contact or location page
   - LOA-008: Store locator content locked in client-side JS widgets
3. Aggregate findings across all pages.
4. Print JSON array of findings to stdout.

## Output
Returns a JSON array of findings, each with: id, title, severity, evidence, suggested_action.
