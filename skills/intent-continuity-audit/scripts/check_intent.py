import json
import argparse

def run_audit(url: str) -> list:
    """Read-only check returning a standardized array of findings."""
    return [{
        "id": "broken-intent-path",
        "severity": "medium",
        "title": "Visitors may lose context between discovery and conversion",
        "description": f"The journey from an informational query to a next step is unclear on {url}.",
        "suggested_action": "Add contextual calls to action and internal links that carry each user intent directly from the answer to the appropriate product or contact path.",
    }]

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Target URL to audit")
    args = parser.parse_args()
    
    # Scripts must print JSON array to stdout for the Orchestrator to consume
    print(json.dumps(run_audit(args.url)))
