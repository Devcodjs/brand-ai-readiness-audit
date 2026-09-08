import sys
import json
import argparse
import os
import requests
from bs4 import BeautifulSoup

def unpack_schemas(data):
    """Recursively extracts all schema nodes, unpacking @graph arrays."""
    schemas = []
    if isinstance(data, list):
        for item in data:
            schemas.extend(unpack_schemas(item))
    elif isinstance(data, dict):
        if "@graph" in data and isinstance(data["@graph"], list):
            schemas.extend(unpack_schemas(data["@graph"]))
        else:
            schemas.append(data)
    return schemas

def validate_entity_graph(target_url: str, cache_dir: str = "audit-cache") -> list:
    findings = []
    html_content = ""
    
    # 1. Read from audit-cache first to satisfy offline hackathon requirements
    cached_file = os.path.join(cache_dir, "index.html")
    if os.path.exists(cached_file):
        with open(cached_file, "r", encoding="utf-8") as f:
            html_content = f.read()
    else:
        try:
            resp = requests.get(target_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            html_content = resp.text
        except Exception as err:
            return [{
                "id": "ENTITY-ERR-000",
                "title": "Entity Validation Execution Error",
                "severity": "medium",
                "evidence": f"Failed to parse target URL for structured data: {str(err)}",
                "suggested_action": None
            }]

    soup = BeautifulSoup(html_content, "html.parser")
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
    unique_same_as = set()
    target_types = {"Organization", "Brand", "Corporation", "LocalBusiness"}

    for script in json_ld_scripts:
        if not script.string:
            continue
        try:
            raw_data = json.loads(script.string)
            schemas = unpack_schemas(raw_data)
            
            for schema in schemas:
                raw_type = schema.get("@type", [])
                types = {raw_type} if isinstance(raw_type, str) else set(raw_type)
                
                # Check for matching organization entity
                if types.intersection(target_types):
                    has_org_schema = True
                    
                    same_as = schema.get("sameAs", [])
                    if isinstance(same_as, str):
                        same_as = [same_as]
                        
                    for link in same_as:
                        if isinstance(link, str) and link.startswith("http"):
                            unique_same_as.add(link)
        except (json.JSONDecodeError, TypeError):
            continue

    same_as_count = len(unique_same_as)

    # 2. Apply diagnostic rules
    if not has_org_schema:
        findings.append({
            "id": "ENTITY-HIGH-002",
            "title": "No Core Organization/Brand Schema",
            "severity": "high",
            "evidence": "Structured data exists, but lacks an explicit Organization or Brand entity node.",
            "suggested_action": {
                "summary": "Embed an Organization JSON-LD node containing your name, logo, and primary URL.",
                "priority": "high"
            }
        })
    elif same_as_count < 2:
        findings.append({
            "id": "ENTITY-MED-003",
            "title": "Weak External Entity Consensus (sameAs Links)",
            "severity": "medium",
            "evidence": f"Organization schema found, but only contains {same_as_count} unique external 'sameAs' authority link(s).",
            "suggested_action": {
                "summary": "Add 'sameAs' links to Wikidata, Crunchbase, and official social channels to prevent AI entity hallucinations.",
                "priority": "medium"
            }
        })

    return findings if findings else [{
        "id": "ENTITY-PASS-000",
        "title": "Robust Semantic Entity Graph",
        "severity": "none",
        "evidence": f"Verified presence of Brand/Organization schema with {same_as_count} unique sameAs authority links.",
        "suggested_action": None
    }]

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--cache-dir", default="audit-cache")
    args = parser.parse_args()
    
    results = validate_entity_graph(args.url, cache_dir=args.cache_dir)
    print(json.dumps(results, indent=2))