---
name: engagement-audit
description: Audits on-site engagement signals to ensure users arriving from AI assistants maintain context and can interact effectively.
license: MIT
allowed-tools: [python]
metadata:
  author: C_Cube
  version: "1.0.0"
---

# engagement-audit

## When to use
Use this skill to evaluate post-click user engagement barriers. It checks if visitors arriving from AI assistants receive a continuous, interactive, and clear experience rather than getting stuck on static, un-navigable pages.

## Inputs
- `--url`: The target website URL.
- `--cache-dir`: The absolute path to the orchestrator's local cache directory containing crawled page HTML.

## Procedure
1. Reads all cached HTML payloads from the `audit-cache` directory.
2. Evaluates the density of transactional elements and interactive widgets.
3. Checks for immediate popups, intrusive interstitials, or layout shifts that harm engagement.
4. Analyzes the clarity of primary calls-to-action (CTAs) above the fold.
5. Emits engagement-related findings to stdout.

## Output
Returns a JSON array of findings to stdout. Each finding contains:
- `id`: Unique identifier for the finding type (e.g., `ENG-001`).
- `title`: Human-readable summary of the issue.
- `severity`: high, medium, or info.
- `evidence`: Specific details explaining why the finding was flagged.
- `suggested_action`: An object containing a `summary` of steps to take and a `priority` level.
