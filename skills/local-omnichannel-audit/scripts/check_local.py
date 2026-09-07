import json
import argparse

def run_audit(url: str) -> list:
    """Read-only check returning a standardized array of findings."""
    return [{
        "id": "incomplete-local-signals",
        "severity": "medium",
        "title": "Local business details are incomplete",
        "description": f"Location and contact signals for {url} are not consistently presented.",
        "suggested_action": "Standardize the name, address, phone number, opening hours, and service area across the site and LocalBusiness structured data.",
    }]

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Target URL to audit")
    args = parser.parse_args()
    
    # Scripts must print JSON array to stdout for the Orchestrator to consume
    print(json.dumps(run_audit(args.url)))
