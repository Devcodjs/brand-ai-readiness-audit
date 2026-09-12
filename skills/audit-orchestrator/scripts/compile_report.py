import argparse
import concurrent.futures
import json
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from crawler import SiteCrawler

import sys
from pathlib import Path

# Points directly to 'skills/utils'
UTILS_DIR = Path(__file__).resolve().parents[2] / "utils"
if str(UTILS_DIR) not in sys.path:
    sys.path.insert(0, str(UTILS_DIR))

from evidence_gate import apply_evidence_gate, summarize_blocked_url_targets

def find_repository_root() -> Path:
    """Find the marketplace root without relying on a fixed directory depth."""
    here = Path(__file__).resolve()
    for candidate in [here.parent, *here.parents]:
        if (candidate / "marketplace.json").exists() and (candidate / "skills").exists():
            return candidate
    return here.parents[3]


REPOSITORY_ROOT = find_repository_root()
MARKETPLACE_FILE = REPOSITORY_ROOT / "marketplace.json"
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

ALWAYS_SAFE_SKILLS = {
    "network-accessibility-audit",
    "feed-syndication-audit",
    "crawl-render-audit",
}

PAGE_DEPENDENT_SKILLS = {
    "entity-graph-validator",
    "category-isolation-audit",
    "content-extractability-audit",
    "intent-continuity-audit",
    "local-omnichannel-audit",
    "content-freshness-audit"
}


def resolve_https_target(raw_input: str) -> str:
    """Resolve a usable HTTPS target; reachable is not the same as auditable."""
    cleaned = raw_input.strip()
    if not cleaned:
        raise ValueError("Target URL is empty.")
    if "://" not in cleaned:
        cleaned = f"https://{cleaned}"

    parsed = urlparse(cleaned)
    host = parsed.netloc or parsed.path.split("/")[0]
    host = host.split(":")[0]
    if not host:
        raise ValueError(f"Invalid target '{raw_input}': no hostname found.")

    base_domain = host[4:] if host.startswith("www.") else host
    subpath = parsed.path if parsed.netloc else ""
    if parsed.query:
        subpath += f"?{parsed.query}"

    candidates = [
        f"https://www.{base_domain}{subpath}",
        f"https://{base_domain}{subpath}",
    ]
    session = requests.Session()
    session.mount(
        "https://",
        HTTPAdapter(max_retries=Retry(
            total=2,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET"],
        )),
    )

    last_error = "Unknown connection failure."
    for candidate in dict.fromkeys(candidates):
        try:
            resp = session.get(candidate, headers=HEADERS, timeout=15, allow_redirects=True)
            if resp.url.startswith("https://") and resp.status_code not in (404, 410):
                return resp.url
            last_error = f"HTTP {resp.status_code} response received."
        except requests.exceptions.SSLError:
            last_error = "TLS/SSL certificate validation failed."
        except requests.exceptions.ConnectTimeout:
            last_error = "Connection attempt timed out."
        except requests.exceptions.ReadTimeout:
            last_error = "Server response timed out (possible WAF/rate limiting)."
        except requests.exceptions.ConnectionError as exc:
            cause = exc.__cause__ or exc.__context__
            text = str(exc).lower()
            if isinstance(cause, socket.gaierror) or "name or service not known" in text:
                last_error = "DNS resolution failed."
            elif isinstance(cause, ConnectionRefusedError) or "connection refused" in text:
                last_error = "Remote host refused the connection."
            elif isinstance(cause, TimeoutError) or "timed out" in text:
                last_error = "Connection timed out."
            else:
                last_error = f"Connection error: {exc}"
        except requests.exceptions.RequestException as exc:
            last_error = f"Network exception: {exc}"

    raise ValueError(
        f"Target '{raw_input}' could not be reached via HTTPS on {base_domain}: {last_error}"
    )


def _load_checks() -> tuple[tuple[str, Path], ...]:
    try:
        marketplace = json.loads(MARKETPLACE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Unable to read {MARKETPLACE_FILE}: {error}") from error

    checks = []
    entrypoints = 0
    for skill in marketplace.get("skills", []):
        if skill.get("entrypoint"):
            entrypoints += 1
            continue
        skill_name = skill.get("id")
        skill_path = skill.get("path")
        if not isinstance(skill_name, str) or not isinstance(skill_path, str):
            raise RuntimeError("Each marketplace skill must define 'id' and 'path' strings")

        scripts = sorted((REPOSITORY_ROOT / skill_path / "scripts").glob("*.py"))
        if len(scripts) != 1:
            raise RuntimeError(
                f"Expected exactly one Python script for {skill_name}, found {len(scripts)}"
            )
        checks.append((skill_name, scripts[0]))

    if entrypoints != 1:
        raise RuntimeError(f"Marketplace must declare exactly one entrypoint; found {entrypoints}")
    return tuple(checks)


def _run_check(check: tuple[str, Path], url: str, cache_dir: str) -> list[dict]:
    skill_name, script = check
    result = subprocess.run(
        [sys.executable, str(script), "--url", url, "--cache-dir", cache_dir],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return [{
            "id": f"SYS-ERR-{skill_name[:4].upper()}",
            "title": f"Skill Execution Failed: {skill_name}",
            "severity": "medium",
            "evidence": result.stderr.strip() or f"Exited with code {result.returncode}",
            "suggested_action": {
                "summary": "Check sub-skill script dependencies, syntax, or arguments.",
                "priority": "low",
            },
            "skill": skill_name,
        }]

    try:
        findings = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    if not isinstance(findings, list):
        return []

    for finding in findings:
        if isinstance(finding, dict):
            finding.setdefault("skill", skill_name)
    return [f for f in findings if isinstance(f, dict)]


def _load_cache_manifest(cache_dir: str) -> dict:
    path = Path(cache_dir) / "cache_index.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _blocked_page_urls(pages: list[dict]) -> list[str]:
    return [
        p.get("final_url") or p.get("url", "")
        for p in pages
        if p.get("content_classification") in {"bot_challenge", "blocked", "auth_wall"}
    ]


def _recovered_paths_note(pages: list[dict]) -> str:
    """
    Blocked-redirect URLs (e.g. Walmart's /blocked?url=...) often still carry
    the originally requested path, base64-encoded. Recovering it costs
    nothing and gives a human reader some sense of what the crawler was
    trying to reach. This is explicitly NOT confirmed page content — only
    the intended destination path — and must always be framed that way.
    """
    recovered = summarize_blocked_url_targets(_blocked_page_urls(pages))
    if not recovered:
        return ""
    return (
        " Intended destination paths recovered from blocked-redirect URLs "
        "(structure only — actual page content was never retrieved, so this "
        "is NOT confirmed evidence of what those pages contain): "
        + ", ".join(recovered) + "."
    )


def _eligible_skills(checks, meta: dict, pages: list[dict]) -> tuple[list[tuple[str, Path]], list[dict]]:
    usable_ratio = float(meta.get("usable_page_ratio", 0.0) or 0.0)
    usable_pages = int(meta.get("pages_usable", 0) or 0)
    challenge_pages = int(meta.get("pages_challenge", 0) or 0)
    recovered_note = _recovered_paths_note(pages)

    evidence_notes = []
    if challenge_pages:
        evidence_notes.append({
            "id": "CRAWL-DATA-QUALITY",
            "title": "Crawl Evidence Quality Reduced by Challenge Responses",
            "severity": "medium" if usable_pages else "high",
            "evidence": (
                f"{challenge_pages} sampled pages returned security challenges; "
                f"{usable_pages} pages were normal HTML "
                f"(usable-page ratio {usable_ratio:.0%})."
                + recovered_note
            ),
            "suggested_action": {
                "summary": (
                    "Enterprise bot mitigation is intercepting diagnostic crawlers. If you syndicate "
                    "catalog data via private APIs or Merchant Center feeds, this barrier is expected. "
                    "However, if you depend on zero-shot discovery from autonomous AI user-agents (e.g., GPTBot, Perplexity), "
                    "consider implementing verified bot allowlists at your WAF/edge layer."
                ),
                "priority": "high" if not usable_pages else "medium",
            },
            "skill": "audit-orchestrator",
        })

    if usable_pages == 0 or usable_ratio < 0.40:
        selected = [c for c in checks if c[0] in ALWAYS_SAFE_SKILLS]
        evidence_notes.append({
            "id": "CRAWL-SUPPRESS-001",
            "title": "Page-Level Audits Suppressed Due to Insufficient Evidence",
            "severity": "medium",
            "evidence": (
                f"Only {usable_pages} of {meta.get('pages_requested', 0)} sampled pages were normal HTML; "
                "page-dependent checks were suppressed to avoid false positives."
                + recovered_note
            ),
            "suggested_action": {
                "summary": (
                    "In-depth semantic checks (schemas, titles, layout) were deferred because edge challenges "
                    "obfuscated page content. If this platform relies on public web indexing rather than dedicated "
                    "syndication feeds, ensure diagnostic and AI user-agents can access representative public landing pages."
                ),
                "priority": "medium",
            },
            "skill": "audit-orchestrator",
        })
        return selected, evidence_notes

    return list(checks), evidence_notes


def _dedupe_findings(findings: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for finding in findings:
        fid = str(finding.get("id", ""))
        title = str(finding.get("title", ""))
        evidence = str(finding.get("evidence", ""))
        key = (fid, title, evidence[:300])
        if key in seen:
            continue
        seen.add(key)
        result.append(finding)
    return result


def orchestrate_audit(raw_url: str) -> dict:
    audited_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        resolved_url = resolve_https_target(raw_url)
    except ValueError as err:
        return {
            "site": raw_url,
            "audited_at": audited_at,
            "audit_confidence": {"execution": "HIGH", "evidence": "LOW"},
            "summary": {"total_findings": 1, "critical": 0, "high": 0, "medium": 1, "low": 0, "info": 0},
            "findings": [{
                "id": "NET-PIPELINE-FAIL",
                "skill": "audit-orchestrator",
                "title": "Network Pipeline Resolution Failed",
                "severity": "medium",
                "evidence": str(err),
                "suggested_action": {
                    "summary": "Verify DNS resolution and HTTPS availability before evaluating the site's AI discoverability.",
                    "priority": "medium",
                },
            }],
            "audit_notes": []
        }

    try:
        crawler = SiteCrawler(resolved_url)
        cache_dir = crawler.build_cache()
    except Exception as err:
        return {
            "site": resolved_url,
            "audited_at": audited_at,
            "audit_confidence": {"execution": "LOW", "evidence": "LOW"},
            "summary": {"total_findings": 1, "critical": 1, "high": 0, "medium": 0, "low": 0, "info": 0},
            "findings": [{
                "id": "SYS-CRAWLER-CRASH",
                "skill": "audit-orchestrator",
                "title": "Local Crawler Initialization Failed",
                "severity": "critical",
                "evidence": f"Crawler execution halted unexpectedly: {err}",
                "suggested_action": {
                    "summary": "Review the local crawler runtime, filesystem permissions, and network interface restrictions.",
                    "priority": "critical",
                },
            }],
            "audit_notes": []
        }

    manifest = _load_cache_manifest(cache_dir)

    # Run the ONE authoritative usability pass over every cached page here,
    # synchronously, before any check is scheduled — and persist the
    # correction immediately. Previously this reclassification only happened
    # inside crawl-render-audit's own check, running concurrently with every
    # other skill via the thread pool below; whichever skill's subprocess
    # happened to read cache_index.json before that correction landed saw
    # the crawler's original (sometimes wrong) classification instead. That
    # produced a report where crawl_summary claimed 12/12 pages usable while
    # a finding two lines below said 11/12 were blocked interstitials, and
    # let those 11 blocked pages silently contaminate every other page-level
    # check. Doing it once, here, before dispatch removes the ordering
    # dependency entirely: every consumer below — the suppression decision,
    # the final crawl_summary, every dispatched skill's own fresh read of
    # cache_index.json — now sees the same already-corrected picture.
    manifest = apply_evidence_gate(manifest)
    try:
        (Path(cache_dir) / "cache_index.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
    except OSError:
        pass

    meta = manifest.get("meta", {})
    pages = manifest.get("pages", [])
    crawl_status = meta.get("crawl_status", "unknown")

    checks = _load_checks()
    checks, evidence_notes = _eligible_skills(checks, meta, pages)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(6, len(checks)))) as executor:
        results = list(executor.map(
            lambda check: _run_check(check, resolved_url, cache_dir),
            checks,
        ))

    # Combine and deduplicate all findings
    raw_findings = [finding for result in results for finding in result]
    raw_findings.extend(evidence_notes)
    raw_findings = _dedupe_findings(raw_findings)

    # Sort everything
    raw_findings.sort(key=lambda f: (
        SEVERITY_ORDER.get(str(f.get("severity", "")).lower(), 99),
        str(f.get("id", "")),
    ))

    # Separate actionable findings from info/pass diagnostics
    actionable_findings = []
    audit_notes = []
    
    for f in raw_findings:
        if str(f.get("severity", "")).lower() == "info":
            audit_notes.append(f)
        else:
            actionable_findings.append(f)

    # Calculate evidence confidence
    usable = int(meta.get("pages_usable", 0) or 0)
    requested = int(meta.get("pages_requested", 0) or 0)
    challenge = int(meta.get("pages_challenge", 0) or 0)
    ratio = usable / max(1, requested)

    if usable >= 6 and ratio >= 0.70 and challenge == 0:
        evidence_level = "HIGH"
    elif usable >= 3 and ratio >= 0.40:
        evidence_level = "MEDIUM"
    else:
        evidence_level = "LOW"

    if crawl_status in {"blocked_by_waf", "rate_limited", "failed_root_fetch"} and usable == 0:
        evidence_level = "LOW"
    if crawl_status == "blocked_by_robots" and usable > 0:
        evidence_level = "MEDIUM"

    confidence = {
        "execution": "HIGH",
        "evidence": evidence_level,
    }

    # Summary now exclusively counts actionable defects
    summary = {
        "total_findings": len(actionable_findings),
        "critical": sum(str(f.get("severity", "")).lower() == "critical" for f in actionable_findings),
        "high": sum(str(f.get("severity", "")).lower() == "high" for f in actionable_findings),
        "medium": sum(str(f.get("severity", "")).lower() == "medium" for f in actionable_findings),
        "low": sum(str(f.get("severity", "")).lower() == "low" for f in actionable_findings),
    }

    return {
        "site": resolved_url,
        "audited_at": audited_at,
        "audit_confidence": confidence,
        "crawl_summary": {
            "status": crawl_status,
            "pages_requested": requested,
            "pages_usable": usable,
            "pages_challenge": challenge,
            "usable_page_ratio": round(ratio, 3),
            "page_type_counts": meta.get("page_type_counts", {}),
            "pages_by_classification": meta.get("pages_by_classification", {}),
            "sitemap_present": bool(meta.get("sitemap_present")),
            "robots_present": bool(meta.get("robots_present")),
            "blocked_search_bots": meta.get("blocked_search_bots", []),
        },
        "summary": summary,
        "findings": actionable_findings,
        "audit_notes": audit_notes,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Target URL or domain to audit")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(orchestrate_audit(args.url), indent=2))