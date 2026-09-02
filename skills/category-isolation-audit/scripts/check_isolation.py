import json
import argparse

def run_audit(url: str) -> list:
    """Read-only check returning a standardized array of findings."""
    # TODO: Implement specific audit logic for this skill
    findings = []
    return findings

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Target URL to audit")
    args = parser.parse_args()
    
    # Scripts must print JSON array to stdout for the Orchestrator to consume
    print(json.dumps(run_audit(args.url)))
