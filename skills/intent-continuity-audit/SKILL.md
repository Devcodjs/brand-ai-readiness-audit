---
name: intent-continuity-audit
description: >
  Audits the visitor journey from AI referral through landing page to conversion.
  Checks deep-link availability, above-fold transactional anchor presence,
  call-to-action placement, overlay/interstitial blockades, meta description
  quality, URL parameter handling, breadcrumb navigation, and conversion path
  existence. Use when diagnosing why AI-referred visitors bounce before
  completing their intended action.
license: MIT
metadata:
  author: team-c-cube
  version: "1.0.0"
---

# Intent Continuity Audit

## When to use
Run this skill when you need to diagnose why visitors arriving from AI assistants (ChatGPT, Gemini, Perplexity) bounce before engaging with the site or completing an action.

## Inputs
- `--url` (string): The target website URL being audited.
- `--cache-dir` (string): Absolute path to the audit-cache directory containing crawled HTML files.

## Procedure
1. Load all cached HTML pages from the cache directory.
2. For each page, run 10 checks covering the full AI-referral journey:
   - ICA-001: Overlay / interstitial blockade detection
   - ICA-002: Missing deep-link anchor targets
   - ICA-003: No URL parameter handling for referral context
   - ICA-004: Above-fold transactional content deficit
   - ICA-005: Missing or generic page titles
   - ICA-006: Missing meta descriptions / Open Graph tags
   - ICA-007: No CTAs in main content area
   - ICA-008: No contact or conversion path across site
   - ICA-009: Missing breadcrumb navigation
   - ICA-010: Orphan pages with no internal outbound links
3. Aggregate findings across all pages.
4. Print JSON array of findings to stdout.

## Output
Returns a JSON array of findings, each with: id, title, severity, evidence, suggested_action.
