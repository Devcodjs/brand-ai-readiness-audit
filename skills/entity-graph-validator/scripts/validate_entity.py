import sys
import json
import argparse
import requests
from bs4 import BeautifulSoup

def validate_entity_graph(target_url: str) -> list:
    findings = []
    
    try:
        resp = requests.get(target_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        
        json_ld_scripts = soup.find_all("script", type="application/ld+json")
        
        if not json_ld_scripts:
            return [{
                "id": "ENTITY-CRIT-001",
                "title": "Missing Machine-Readable Entity Graph",
                "severity": "critical",
                "evidence": "Zero application/ld+json structured data scripts found in the DOM.",
                "suggested_action": {
                    "summary": "Implement an Organization or Brand JSON-LD schema to explicitly define your identity for AI assistants.",
                    "priority": "critical"
                }
            }]

        has_org_schema = False
        has_same_as_consensus = False
        same_as_count = 0

        for script in json_ld_scripts:
            if not script.string:
                continue
            try:
                data = json.loads(script.string)
                schemas = data if isinstance(data, list) else [data]
                
                for schema in schemas:
                    schema_type = schema.get("@type", "")
                    if schema_type in ["Organization", "Brand", "Corporation", "LocalBusiness"]:
                        has_org_schema = True
                        
                        same_as = schema.get("sameAs", [])
                        if isinstance(same_as, str):
                            same_as = [same_as]
                            
                        same_as_count = len(same_as)
                        if same_as_count >= 2:
                            has_same_as_consensus = True
            except (json.JSONDecodeError, TypeError):
                continue

        if not has_org_schema:
            findings.append({
                "id": "ENTITY-HIGH-002",
                "title": "No Core Organization/Brand Schema",
                "severity": "high",
                "evidence": "Structured data exists, but lacks a explicit Organization or Brand entity node.",
                "suggested_action": {
                    "summary": "Embed an Organization JSON-LD node containing your name, logo, and primary URL.",
                    "priority": "high"
                }
            })
        elif not has_same_as_consensus:
            findings.append({
                "id": "ENTITY-MED-003",
                "title": "Weak External Entity Consensus (sameAs Links)",
                "severity": "medium",
                "evidence": f"Organization schema found, but only contains {same_as_count} 'sameAs' external authority link(s).",
                "suggested_action": {
                    "summary": "Add 'sameAs' links to Wikidata, Crunchbase, and official social channels to prevent AI entity hallucinations.",
                    "priority": "medium"
                }
            })

    except Exception as err:
        findings.append({
            "id": "ENTITY-ERR-000",
            "title": "Entity Validation Execution Error",
            "severity": "medium",
            "evidence": f"Failed to parse target URL for structured data: {str(err)}",
            "suggested_action": None
        })

    return findings if findings else [{
        "id": "ENTITY-PASS-000",
        "title": "Robust Semantic Entity Graph",
        "severity": "none",
        "evidence": "Verified presence of Brand/Organization schema with multi-source sameAs authority links.",
        "suggested_action": None
    }]

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    
    results = validate_entity_graph(args.url)
    print(json.dumps(results, indent=2))