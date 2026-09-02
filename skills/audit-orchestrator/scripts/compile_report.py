import json
import argparse
import concurrent.futures
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

CHECKS = (
    ("crawl-render-audit", "check_render.py"),
    ("entity-graph-validator", "validate_entity.py"),
    ("category-isolation-audit", "check_isolation.py"),
    ("content-extractability-audit", "check_extractability.py"),
    ("intent-continuity-audit", "check_intent.py"),
    ("local-omnichannel-audit", "check_local.py"),
)
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _run_check(check: tuple[str, str], url: str) -> list[dict]:
    skill_name, script_name = check
    script = Path(__file__).resolve().parents[3] / "skills" / skill_name / "scripts" / script_name
    result = subprocess.run(
        [sys.executable, script, "--url", url],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"{skill_name} failed with exit code {result.returncode}: "
            f"{result.stderr.strip()}"
        )
    try:
        findings = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{skill_name} returned invalid JSON") from error
    if not isinstance(findings, list):
        raise RuntimeError(f"{skill_name} returned a JSON object instead of an array")
    for finding in findings:
        if not isinstance(finding, dict):
            raise RuntimeError(f"{skill_name} returned a non-object finding")
        finding.setdefault("skill", skill_name)
    return findings


def orchestrate_audit(url: str) -> dict:
    """Run every audit check and return one deterministic, aggregated report."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(CHECKS)) as executor:
        results = list(executor.map(lambda check: _run_check(check, url), CHECKS))

    findings = [finding for result in results for finding in result]
    findings.sort(
        key=lambda finding: (
            SEVERITY_ORDER.get(str(finding.get("severity", "")).lower(), 99),
            str(finding.get("id", "")),
        )
    )
    final_report = {
        "site": url,
        "audited_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_findings": len(findings),
        "critical": sum(str(f.get("severity", "")).lower() == "critical" for f in findings),
        "high": sum(str(f.get("severity", "")).lower() == "high" for f in findings),
        "medium": sum(str(f.get("severity", "")).lower() == "medium" for f in findings),
        "findings": findings,
    }
    return final_report

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Target URL to audit")
    args = parser.parse_args()
    print(json.dumps(orchestrate_audit(args.url), indent=2))
