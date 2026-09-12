# Brand AI-Readiness Audit

A modular auditing toolkit that evaluates how well a website is prepared for discovery and engagement by AI systems — search engines, LLM-based assistants, autonomous agents, and AI crawlers.

The tool crawls a target website, runs **9 independent audit checks** concurrently, and produces a single consolidated JSON report with severity-ranked findings and actionable recommendations.

## What It Checks

| Audit Skill | What It Does |
|---|---|
| **Network Accessibility** | Checks `robots.txt` blocks on AI/search bots, validates HTTP security headers, and inspects TLS certificate health |
| **Feed Syndication** | Detects and validates RSS/Atom/JSON feeds and XML sitemaps for machine-readable URL exposure |
| **Crawl & Render** | Audits whether pages render usable content for non-JavaScript crawlers; detects bot-challenge walls and empty pages |
| **Entity Graph Validator** | Validates JSON-LD and Schema.org structured data (Organization, Product, Article, etc.) for completeness and correctness |
| **Category Isolation** | Flags duplicate/boilerplate metadata across category pages — title tags, meta descriptions, headings, and canonicals |
| **Content Extractability** | Checks for semantic HTML landmarks (`<main>`, `<article>`), heading hierarchy, image alt text, and link descriptiveness |
| **Intent Continuity** | Audits internal linking quality, breadcrumb markup, anchor text, and whether CTAs use descriptive labels |
| **Local Omnichannel** | Validates LocalBusiness structured data, NAP (Name/Address/Phone) consistency, store locator pages, and geo meta tags |
| **Content Freshness** | Checks for `datePublished`/`dateModified` in structured data, `Last-Modified` headers, and sitemap `<lastmod>` entries |

## Architecture

```
marketplace.json          ← Registers all skills
│
├── audit-orchestrator    ← Entrypoint: resolves URL, crawls, dispatches checks, compiles report
│
├── network-accessibility-audit ─┐
├── feed-syndication-audit       │
├── crawl-render-audit           │
├── entity-graph-validator       ├── 9 independent audit skills (run concurrently)
├── category-isolation-audit     │
├── content-extractability-audit │
├── intent-continuity-audit      │
├── local-omnichannel-audit      │
├── content-freshness-audit    ──┘
│
└── utils/evidence_gate.py  ← Shared page-evidence classification logic
```

## Prerequisites

- **Python 3.10+** — [Download Python](https://www.python.org/downloads/)
  - Make sure to check **"Add Python to PATH"** during installation on Windows

## Installation & Setup

### 1. Clone the repository

```bash
git clone https://github.com/<your-username>/brand-ai-readiness-audit.git
cd brand-ai-readiness-audit
```

### 2. Create a virtual environment

```bash
python -m venv venv
```

### 3. Activate the virtual environment

**Windows (PowerShell):**

```powershell
.\venv\Scripts\Activate.ps1
```

**Windows (Command Prompt):**

```cmd
venv\Scripts\activate.bat
```

**macOS / Linux:**

```bash
source venv/bin/activate
```

> [!TIP]
> You'll know it's active when you see `(venv)` at the beginning of your terminal prompt.

### 4. Install dependencies

```bash
pip install -r requirements.txt
```

That's it! All packages install **inside** the `venv/` folder — not globally — so your system Python stays clean.

## Usage

With the virtual environment activated, run:

```bash
python skills/audit-orchestrator/scripts/compile_report.py --url example.com
```

You can pass a bare domain, a full URL, or anything in between:

```bash
# Bare domain
python skills/audit-orchestrator/scripts/compile_report.py --url amazon.com

# Full URL
python skills/audit-orchestrator/scripts/compile_report.py --url https://www.bata.com/

# Specific page
python skills/audit-orchestrator/scripts/compile_report.py --url https://reddit.com/r/technology
```

### Save output to a file

```bash
python skills/audit-orchestrator/scripts/compile_report.py --url example.com > report.json
```

## Output Format

The tool outputs a JSON report with this structure:

```json
{
  "site": "https://www.example.com/",
  "audited_at": "2026-09-12T14:00:00Z",
  "audit_confidence": {
    "execution": "HIGH",
    "evidence": "HIGH"
  },
  "crawl_summary": {
    "status": "success",
    "pages_requested": 12,
    "pages_usable": 12,
    "pages_challenge": 0,
    "usable_page_ratio": 1.0,
    "sitemap_present": true,
    "robots_present": true
  },
  "summary": {
    "total_findings": 5,
    "critical": 0,
    "high": 1,
    "medium": 3,
    "low": 1
  },
  "findings": [ ... ],
  "audit_notes": [ ... ]
}
```

Findings are sorted by severity: **critical → high → medium → low → info**.

## Test URLs

A [test_urls.txt](test_urls.txt) file is included with sample URLs you can use for testing.

## Project Structure

```
brand-ai-readiness-audit/
├── marketplace.json        # Skill registry — defines which audits to run
├── requirements.txt        # Python dependencies
├── test_urls.txt           # Sample URLs for testing
├── README.md
├── .gitignore
└── skills/
    ├── audit-orchestrator/     # Main entrypoint & crawler
    ├── network-accessibility-audit/
    ├── feed-syndication-audit/
    ├── crawl-render-audit/
    ├── entity-graph-validator/
    ├── category-isolation-audit/
    ├── content-extractability-audit/
    ├── intent-continuity-audit/
    ├── local-omnichannel-audit/
    ├── content-freshness-audit/
    └── utils/                  # Shared utilities
```

## Troubleshooting

| Problem | Solution |
|---|---|
| `Activate.ps1 cannot be loaded because running scripts is disabled` | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` in PowerShell and try again |
| `python is not recognized` | Make sure Python is installed and added to PATH |
| `ModuleNotFoundError: No module named 'requests'` | You forgot to activate the venv or install dependencies. Run steps 3 and 4 above |

## License

This project is provided as-is for auditing and educational purposes.
