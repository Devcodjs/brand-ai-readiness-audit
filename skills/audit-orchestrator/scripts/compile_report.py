import json
import argparse
import concurrent.futures
import subprocess
import sys
import socket
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Local crawler import
from crawler import SiteCrawler

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
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


def resolve_https_target(raw_input: str) -> str:
    """
    Normalizes domain or URL inputs and verifies reachable HTTPS transport.

    Distinguishes:
      - DNS resolution failures
      - connection refusal
      - connection timeouts
      - TLS/SSL failures
      - HTTP responses, including anti-bot/WAF challenges
    """
    cleaned = raw_input.strip()
    if "://" not in cleaned:
        cleaned = f"https://{cleaned}"

    parsed = urlparse(cleaned)
    host = parsed.netloc or parsed.path.split("/")[0]
    host = host.split(":")[0]

    if not host:
        raise ValueError(f"Invalid target '{raw_input}': no hostname found.")

    base_domain = host[4:] if host.startswith("www.") else host

    subpath = (
        parsed.path
        if parsed.netloc
        else (
            "/" + "/".join(parsed.path.split("/")[1:])
            if "/" in parsed.path
            else ""
        )
    )

    if parsed.query:
        subpath += f"?{parsed.query}"

    candidates = [
        f"https://www.{base_domain}{subpath}",
        f"https://{base_domain}{subpath}",
    ]

    session = requests.Session()

    retries = Retry(
        total=2,
        backoff_factor=0.5,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"],
    )

    session.mount(
        "https://",
        HTTPAdapter(max_retries=retries),
    )

    last_error = "Unknown connection failure."

    for candidate in candidates:
        try:
            resp = session.get(
                candidate,
                headers=HEADERS,
                timeout=15.0,
                allow_redirects=True,
            )

            # A server that responds is reachable, even when it returns
            # an anti-bot/WAF challenge such as 403 or 503.
            if (
                resp.url.startswith("https://")
                and resp.status_code not in (404, 410, 502)
            ):
                return resp.url

            last_error = (
                f"HTTP {resp.status_code} response received "
                f"without usable page content."
            )

        except requests.exceptions.SSLError:
            last_error = "TLS/SSL certificate validation failed."

        except requests.exceptions.ConnectTimeout:
            last_error = "Connection attempt timed out."

        except requests.exceptions.ReadTimeout:
            last_error = "Server response timed out (possible WAF tarpit or rate limiting)."

        except requests.exceptions.ConnectionError as exc:
            cause = exc.__cause__ or exc.__context__

            if isinstance(cause, socket.gaierror):
                last_error = "DNS resolution failed."

            elif isinstance(cause, ConnectionRefusedError):
                last_error = "Remote host refused the connection."

            elif isinstance(cause, TimeoutError):
                last_error = "Connection timed out."

            else:
                # urllib3 often nests the useful socket exception deeper,
                # so fall back to inspecting the exception chain text.
                error_text = str(exc).lower()

                if "name or service not known" in error_text:
                    last_error = "DNS resolution failed."

                elif "nodename nor servname" in error_text:
                    last_error = "DNS resolution failed."

                elif "temporary failure in name resolution" in error_text:
                    last_error = "DNS resolution failed."

                elif "connection refused" in error_text:
                    last_error = "Remote host refused the connection."

                elif "connection timed out" in error_text:
                    last_error = "Connection attempt timed out."

                else:
                    last_error = f"Connection error: {exc}"

        except requests.exceptions.RequestException as exc:
            last_error = f"Network exception: {exc}"

    raise ValueError(
        f"Target '{raw_input}' could not be reached via HTTPS "
        f"on www.{base_domain} or {base_domain}: {last_error}"
    )


def _load_checks() -> tuple[tuple[str, Path], ...]:
    """Loads all non-entrypoint skills declared in the marketplace manifest."""
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
            raise RuntimeError("Each marketplace skill must define 'id' and 'path' strings")

        scripts = sorted((REPOSITORY_ROOT / skill_path / "scripts").glob("*.py"))
        if len(scripts) != 1:
            raise RuntimeError(
                f"Expected exactly one Python script for {skill_name}, found {len(scripts)}"
            )
        checks.append((skill_name, scripts[0]))
    return tuple(checks)


def _run_check(check: tuple[str, Path], url: str, cache_dir: str) -> list[dict]:
    """Executes an individual sub-skill script against the local crawl cache."""
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
    """Coordinates resolution, caching, dynamic circuit-breaking, and finding aggregation."""

    audited_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. Transport & Network Resolution
    try:
        resolved_url = resolve_https_target(raw_url)
    except ValueError as err:
        return {
            "site": raw_url,
            "audited_at": audited_at,
            "audit_confidence": "LOW (Network Unreachable)",
            "summary": {
                "total_findings": 1,
                "critical": 0,
                "high": 0,
                "medium": 1,
            },
            "findings": [{
                "id": "NET-PIPELINE-FAIL",
                "skill": "audit-orchestrator",
                "title": "Network Pipeline Resolution Failed",
                "severity": "medium",
                "evidence": str(err),
                "suggested_action": {
                    "summary": (
                        "Verify DNS resolution and HTTPS availability "
                        "before evaluating the site's AI discoverability."
                    ),
                    "priority": "medium",
                },
            }],
        }

    # 2. Crawler Cache Execution
    try:
        crawler = SiteCrawler(resolved_url)
        cache_dir = crawler.build_cache()
    except Exception as err:
        return {
            "site": resolved_url,
            "audited_at": audited_at,
            "audit_confidence": "LOW (Crawler Crash)",
            "summary": {
                "total_findings": 1,
                "critical": 1,
                "high": 0,
                "medium": 0,
            },
            "findings": [{
                "id": "SYS-CRAWLER-CRASH",
                "skill": "audit-orchestrator",
                "title": "Local Crawler Initialization Failed",
                "severity": "critical",
                "evidence": f"Crawler execution halted unexpectedly: {err}",
                "suggested_action": {
                    "summary": (
                        "Review the local crawler runtime, filesystem permissions, "
                        "and network interface restrictions."
                    ),
                    "priority": "critical",
                },
            }],
        }

    # 3. Read Crawl Metadata for Dynamic Routing
    cache_index_path = Path(cache_dir) / "cache_index.json"
    crawl_status = "unknown"
    sitemap_present = False

    if cache_index_path.exists():
        try:
            manifest = json.loads(
                cache_index_path.read_text(encoding="utf-8")
            )
            meta = manifest.get("meta", {})
            crawl_status = meta.get("crawl_status", "unknown")
            sitemap_present = meta.get("sitemap_present", False)
        except (json.JSONDecodeError, OSError):
            pass

    checks = _load_checks()

    # CIRCUIT BREAKER:
    # If direct crawling was blocked (WAF, 503, or robots), only execute
    # network and syndication audits. This prevents DOM-dependent skills
    # from failing on empty/incomplete cache folders.
    if crawl_status != "success":
        allowed_skills = {
            "network-accessibility-audit",
            "feed-syndication-audit",
        }
        checks = [
            check for check in checks
            if check[0] in allowed_skills
        ]

    # 4. Concurrent Skill Execution
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=len(checks) or 1
    ) as executor:
        results = list(
            executor.map(
                lambda check: _run_check(
                    check,
                    resolved_url,
                    cache_dir,
                ),
                checks,
            )
        )

    findings = [
        finding
        for result in results
        for finding in result
    ]

    findings.sort(
        key=lambda finding: (
            SEVERITY_ORDER.get(
                str(finding.get("severity", "")).lower(),
                99,
            ),
            str(finding.get("id", "")),
        )
    )

    # 5. Evidence-Calibrated Confidence Scoring
    confidence = "HIGH (Full Pipeline Execution)"

    if crawl_status == "blocked_by_robots":
        confidence = "HIGH (Explicitly Disallowed in robots.txt)"

    elif (
        crawl_status in ("blocked_by_waf", "rate_limited")
        and sitemap_present
    ):
        confidence = (
            "MEDIUM "
            "(DOM Scrape Blocked, Evaluating via XML Syndication)"
        )

    elif crawl_status != "success":
        confidence = "LOW (No Public AI-Accessible Surface Observed)"

    return {
        "site": resolved_url,
        "audited_at": audited_at,
        "audit_confidence": confidence,
        "summary": {
            "total_findings": len(findings),
            "critical": sum(
                str(f.get("severity", "")).lower() == "critical"
                for f in findings
            ),
            "high": sum(
                str(f.get("severity", "")).lower() == "high"
                for f in findings
            ),
            "medium": sum(
                str(f.get("severity", "")).lower() == "medium"
                for f in findings
            ),
        },
        "findings": findings,
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Target URL or domain to audit")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(orchestrate_audit(args.url), indent=2))