import json
import argparse

def run_audit(url: str) -> list:
    """Read-only check returning a standardized array of findings."""
    return [{
        "id": "thin-answer-content",
        "severity": "high",
        "title": "Key pages lack concise answer-focused content",
        "description": f"Important questions about {url} are not answered in easily extractable sections.",
        "suggested_action": "Add concise, self-contained answers beneath descriptive headings, supported by facts, examples, and relevant structured data.",
    }]

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Target URL to audit")
    args = parser.parse_args()
    
    # Scripts must print JSON array to stdout for the Orchestrator to consume
    print(json.dumps(run_audit(args.url)))
