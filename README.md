# Brand AI-Readiness Audit Marketplace
This marketplace audits websites for AI discoverability and on-site engagement problems.
The `audit-orchestrator` acts as the entrypoint, composing checks from 6 modular sub-skills.

## Run the orchestrator

From the repository root:

```sh
python skills/audit-orchestrator/scripts/compile_report.py --url example.com
```

The orchestrator runs all checks concurrently and prints a JSON report. Findings
are sorted from critical to informational severity, with aggregate counts in the
`total_findings`, `critical`, `high`, and `medium` fields.
