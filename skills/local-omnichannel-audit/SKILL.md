---
name: local-omnichannel-audit
description: Validates physical store and local business presence for AI discoverability. Checks for LocalBusiness schema, Address completeness, GeoCoordinates, NAP consistency, and store locator accessibility.
license: MIT
allowed-tools: [python]
metadata:
  author: C_Cube
  version: "1.0.0"
---

# local-omnichannel-audit

## When to use
Run this skill when auditing whether AI assistants can reliably answer questions about a business's physical locations, operating hours, and contact information. Use it for brands with brick-and-mortar stores, restaurants, local services, or dealer networks.

## Inputs
- `--url` (string): The target website URL being audited.
- `--cache-dir` (string): Absolute path to the audit-cache directory containing crawled HTML files.

## Procedure
1. Load all cached HTML pages from the cache directory.
2. Filter for usable, normal pages (ignoring bot challenges).
3. Execute gate check 0: Bypass audit if the site is a reference/wiki platform.
4. Execute gate check 1: Bypass audit if no physical footprint signals are detected (digital-first brand).
5. For each page, extract JSON-LD structured data and visible text, then run 8 core checks (LOA-001 through LOA-008).
6. Run 3 proactive recommendations (LOA-PRO-001 through LOA-PRO-003).
7. Aggregate findings and apply dynamic severity scaling.
8. Print a JSON array of findings to standard output.

## Output
Returns a JSON array of findings to standard output. Each finding object contains:
- `id`, `title`, `severity`, `evidence`, and `suggested_action` (with `summary` and `priority`).

**Detection Logic:**
- **LOA-001**: Missing or sparse LocalBusiness / Store schema.org markup across sampled pages.
- **LOA-002**: Incomplete PostalAddress in structured data (missing streetAddress, Locality, Region, etc.).
- **LOA-003**: Missing GeoCoordinates (latitude/longitude) on LocalBusiness nodes.
- **LOA-004**: Missing openingHours or openingHoursSpecification in structured data.
- **LOA-005**: Physical address not present in HTML text alongside map embeds (meaning data is locked in the iframe).
- **LOA-006**: NAP (Name, Address, Phone) inconsistency (e.g., differing `tel:` links) across pages.
- **LOA-007**: No dedicated contact or location page detected despite physical brand signals.
- **LOA-008**: Store locator content is locked in client-side JS widgets, rendering it invisible to static crawlers.

**Severity:**
- **High**: Complete absence of LocalBusiness schema for a physical brand, severe NAP inconsistency, missing dedicated location pages, or JS-locked store locators.
- **Medium**: Sparse schema coverage, incomplete addresses, or missing geo/hours affecting a subset of locations.
- **Low**: Proactive recommendations (e.g., adding areaServed, potentialAction, or GBP cross-links).
- **Info**: Skipped audits (digital-only brands or reference sites) or passing scenarios.
- **Downgrade Logic**: If the *only* physical signal is a bare embedded map, severity is downgraded to prevent false positives on editorial pages.

**Suggested Actions:**
Specific remediations are provided, such as:
- Ensuring JSON-LD LocalBusiness schema is embedded consistently.
- Providing complete PostalAddress fields.
- Centralizing NAP data in CMS variables.
- Providing static HTML fallbacks for JS-driven store locators.
