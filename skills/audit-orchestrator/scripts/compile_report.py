import json
import argparse
import concurrent.futures
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MARKETPLACE_FILE = REPOSITORY_ROOT / "marketplace.json"
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


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


def _run_check(check: tuple[str, Path], url: str) -> list[dict]:
    skill_name, script = check
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
    checks = _load_checks()
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(checks)) as executor:
        results = list(executor.map(lambda check: _run_check(check, url), checks))

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
