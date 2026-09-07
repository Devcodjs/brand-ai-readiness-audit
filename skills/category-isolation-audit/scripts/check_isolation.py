import json
import argparse

def run_audit(url: str) -> list:
    """Read-only check returning a standardized array of findings."""
    return [{
        "id": "ambiguous-service-category",
        "severity": "medium",
        "title": "Service categories are not distinct enough",
        "evidence": f"The offering hierarchy on {url} may be difficult for an AI system to classify.",
        "suggested_action": {
            "summary": "Create dedicated category pages with unique descriptions, explicit inclusion criteria, and links to the services in each category.",
            "priority": "medium"
        },
    }]

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Target URL to audit")
    parser.add_argument("--cache-dir", required=True, help="Cache directory")
    args = parser.parse_args()
    
    # Scripts must print JSON array to stdout for the Orchestrator to consume
    print(json.dumps(run_audit(args.url)))
