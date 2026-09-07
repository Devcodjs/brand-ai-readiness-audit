import json
import argparse
import concurrent.futures
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
import requests

# Local import of crawler module
from crawler import SiteCrawler, RobotsBlockedException

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MARKETPLACE_FILE = REPOSITORY_ROOT / "marketplace.json"
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}
REQUEST_TIMEOUT = 10


def resolve_https_target(raw_input: str) -> str:
    """
    Normalizes domain or URL inputs, tests https://www.<domain> first,
    falls back to https://<domain>, and raises ValueError if neither connects over HTTPS.
    """
    cleaned = raw_input.strip()

    # Normalize missing scheme so urlparse extracts netloc correctly
    if "://" not in cleaned:
        cleaned = f"https://{cleaned}"

    parsed = urlparse(cleaned)
    host = parsed.netloc or parsed.path.split("/")[0]
    host = host.split(":")[0]  # Strip any port

    # Base domain without leading 'www.'
    base_domain = host[4:] if host.startswith("www.") else host

    # Preserve any subpath and query parameters
    subpath = parsed.path if parsed.netloc else ("/" + "/".join(parsed.path.split("/")[1:]) if "/" in parsed.path else "")
    if parsed.query:
        subpath += f"?{parsed.query}"

    candidates = [
        f"https://www.{base_domain}{subpath}",
        f"https://{base_domain}{subpath}",
    ]

    last_error = "Unknown error"

    for candidate in candidates:
        try:
            resp = requests.get(
                candidate,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )
            # Accept if it reached an active HTTPS server (including 403/503 bot challenges)
            if resp.url.startswith("https://") and resp.status_code not in (404, 410, 502):
                return resp.url

            last_error = f"HTTP status {resp.status_code}"
        except requests.RequestException as exc:
            last_error = str(exc)
            continue

    raise ValueError(
        f"Target '{raw_input}' could not be reached via HTTPS on either "
        f"https://www.{base_domain} or https://{base_domain}. Reason: {last_error}"
    )


def _load_checks() -> tuple[tuple[str, Path], ...]:
    try:
        marketplace = json.loads(MARKETPLACE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Unable to read {MARKETPLACE_FILE}: {error}") from error

    checks = []
    for skill in marketplace.get("skills", []):
        if skill.get("entrypoint"):
            continue
        skill_name = skill.get("id")
        skill_path = skill.get("path")
        if not isinstance(skill_name, str) or not isinstance(skill_path, str):
            raise RuntimeError("Each marketplace skill must define string 'id' and 'path' values")

        scripts = sorted((REPOSITORY_ROOT / skill_path / "scripts").glob("*.py"))
        if len(scripts) != 1:
            raise RuntimeError(
                f"Expected exactly one Python script for {skill_name}, found {len(scripts)}"
            )
        checks.append((skill_name, scripts[0]))
    return tuple(checks)


def _run_check(check: tuple[str, Path], url: str, cache_dir: str) -> list[dict]:
    skill_name, script = check
    result = subprocess.run(
        [sys.executable, str(script), "--url", url, "--cache-dir", cache_dir],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        return [{
            "id": f"SYS-ERR-{skill_name[:4].upper()}",
            "title": f"Skill Execution Failed: {skill_name}",
            "severity": "medium",
            "evidence": result.stderr.strip() or f"Exited with code {result.returncode}",
            "suggested_action": {
                "summary": "Check sub-skill dependencies and syntax.",
                "priority": "low",
            },
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
    return findings

def orchestrate_audit(raw_url: str) -> dict:
    try:
        resolved_url = resolve_https_target(raw_url)
    except ValueError as err:
        return {
            "site": raw_url,
            "status": "aborted",
            "audited_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "error": str(err),
            "summary": {"total_findings": 0, "critical": 0, "high": 0, "medium": 0},
            "findings": [],
        }

    # Execute crawler with robots enforcement
    try:
        crawler = SiteCrawler(resolved_url)
        cache_dir = crawler.build_cache()
    except RobotsBlockedException as err:
        return {
            "site": resolved_url,
            "original_input": raw_url,
            "status": "blocked_by_robots",
            "audited_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "summary": {"total_findings": 1, "critical": 1, "high": 0, "medium": 0},
            "findings": [{
                "id": "AI-READINESS-ROBOTS-DISALLOWED",
                "skill": "crawler",
                "title": "Site Disallows Automated Crawlers via robots.txt",
                "severity": "critical",
                "evidence": str(err),
                "suggested_action": {
                    "summary": "Update robots.txt to explicitly permit AI/audit bots if discoverability is desired.",
                    "priority": "high"
                }
            }],
        }
    except Exception as err:
        return {
            "site": resolved_url,
            "status": "crawler_failed",
            "audited_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "error": f"Crawler failed during cache execution: {err}",
            "summary": {"total_findings": 0, "critical": 0, "high": 0, "medium": 0},
            "findings": [],
        }

    # Phase 2: Run sub-skills concurrently against local cache
    checks = _load_checks()
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(checks) or 1) as executor:
        results = list(executor.map(lambda check: _run_check(check, resolved_url, cache_dir), checks))

    findings = [finding for result in results for finding in result]
    findings.sort(
        key=lambda finding: (
            SEVERITY_ORDER.get(str(finding.get("severity", "")).lower(), 99),
            str(finding.get("id", "")),
        )
    )

    return {
        "site": resolved_url,
        "original_input": raw_url,
        "status": "completed",
        "audited_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": {
            "total_findings": len(findings),
            "critical": sum(str(f.get("severity", "")).lower() == "critical" for f in findings),
            "high": sum(str(f.get("severity", "")).lower() == "high" for f in findings),
            "medium": sum(str(f.get("severity", "")).lower() == "medium" for f in findings),
        },
        "findings": findings,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Target URL or domain to audit")
    args = parser.parse_args()
    print(json.dumps(orchestrate_audit(args.url), indent=2))