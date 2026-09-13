# output-schema.md

This document defines the schema of the final JSON report output by the `audit-orchestrator`'s `compile_report.py` script.

## JSON Schema

```json
{
  "site": "string",
  "audited_at": "string (ISO 8601 timestamp)",
  "audit_confidence": {
    "execution": "string (e.g., 'HIGH', 'LOW')",
    "evidence": "string (e.g., 'HIGH', 'MEDIUM', 'LOW')"
  },
  "crawl_summary": {
    "status": "string",
    "subdomain_pivot": "object or null",
    "pages_requested": "integer",
    "pages_usable": "integer",
    "pages_challenge": "integer",
    "pages_non_html": "integer",
    "pages_duplicate": "integer",
    "non_html_details": "array",
    "usable_page_ratio": "number",
    "page_type_counts": "object",
    "pages_by_classification": "object",
    "sitemap_present": "boolean",
    "robots_present": "boolean",
    "blocked_search_bots": "array of strings"
  },
  "summary": {
    "total_findings": "integer",
    "critical": "integer",
    "high": "integer",
    "medium": "integer",
    "low": "integer"
  },
  "findings": [
    {
      "id": "string",
      "skill": "string",
      "title": "string",
      "severity": "string",
      "evidence": "string",
      "suggested_action": {
        "summary": "string",
        "priority": "string"
      }
    }
  ],
  "audit_notes": [
    {
      "id": "string",
      "skill": "string",
      "title": "string",
      "severity": "string (Always 'info')",
      "evidence": "string",
      "suggested_action": "object or null"
    }
  ]
}
```

## Description of Fields

- **site**: The resolved and validated target URL.
- **audited_at**: Timestamp indicating when the audit was performed.
- **audit_confidence**: Confidence levels for the execution of the audit and the quality of evidence collected.
- **crawl_summary**: Details about the crawling process, including page counts, ratios, blockages, and robot restrictions.
- **summary**: Aggregate counts of the findings grouped by severity (excluding 'info').
- **findings**: An array of actionable findings discovered by the executed skills. Only findings with 'critical', 'high', 'medium', or 'low' severity are included here. 
- **audit_notes**: An array of informational findings ('info' severity) providing diagnostic context rather than actionable defects.
