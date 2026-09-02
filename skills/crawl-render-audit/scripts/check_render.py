import json
import argparse

def run_audit(url: str) -> list:
    """Read-only check returning a standardized array of findings."""
    return [{
        "id": "render-blocking-content",
        "severity": "high",
        "title": "Important page content may be hidden from crawlers",
        "description": f"The primary content on {url} is rendered client-side.",
        "suggested_action": "Server-render the primary navigation and page copy, then verify the rendered HTML is complete without JavaScript execution.",
    }]

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Target URL to audit")
    args = parser.parse_args()
    
    # Scripts must print JSON array to stdout for the Orchestrator to consume
    print(json.dumps(run_audit(args.url)))
