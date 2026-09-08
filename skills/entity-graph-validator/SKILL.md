---
name: entity-graph-validator
description: Validates JSON-LD Organization schemas, sameAs offsite authority links, and machine-readable entity graph completeness.
license: MIT
allowed-tools:
  - python
---

# Entity Graph Validator

## When to use
Use this skill during an AI readiness audit to evaluate Vector 3 (Entity Graph & Semantic Authority). It determines whether a brand explicitly defines its identity using JSON-LD schema and establishes offsite entity consensus via `sameAs` links to prevent AI model hallucinations.

## Inputs
- `url` (string, required): The target website URL to audit.
- `cache-dir` (string, optional): Path to the directory containing cached crawl artifacts (`index.html`). Defaults to `audit-cache`.

## Procedure
1. Consult `references/schema_rules.md` for schema validation heuristics and targeted entity types.
2. Execute the validation script from the repository root:
   ```bash
   python skills/entity-graph-validator/scripts/validate_entity.py --url <TARGET_URL> --cache-dir <CACHE_DIR>