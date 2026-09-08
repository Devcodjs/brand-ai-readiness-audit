import json
import argparse
import xml.etree.ElementTree as ET
from pathlib import Path

def audit_syndication(cache_dir: str) -> list[dict]:
    findings = []
    sitemap_path = Path(cache_dir) / "sitemap.xml"
    
    if not sitemap_path.exists():
        # Do not output an error here; the network-accessibility skill already handled the "None Observed" finding.
        return findings

    try:
        xml_content = sitemap_path.read_text(encoding="utf-8")
        
        # Strip namespaces from tags for easier parsing
        it = ET.iterparse(sitemap_path)
        for _, el in it:
            _, _, el.tag = el.tag.rpartition('}')
        root = it.root

        # Find all location tags (works for both <urlset> and <sitemapindex>)
        urls = [elem.text for elem in root.iter('loc') if elem.text]
        url_count = len(urls)

        if url_count > 0:
            findings.append({
                "id": "FEED-SYNC-VALID",
                "title": "Machine-Readable URL Graph Detected",
                "severity": "info",
                "evidence": f"Successfully parsed {url_count} URLs from the local sitemap XML.",
                "suggested_action": {
                    "summary": "Excellent. The site exposes a substantial public information architecture. Ensure this sitemap is automatically submitted to major search providers.",
                    "priority": "low"
                }
            })
        else:
            findings.append({
                "id": "FEED-SYNC-EMPTY",
                "title": "Sitemap Exists but is Empty",
                "severity": "high",
                "evidence": "A sitemap file was found and parsed, but contained zero valid <loc> URL tags.",
                "suggested_action": {
                    "summary": "Your syndication feed is broken. Regenerate the sitemap to populate it with valid canonical URLs.",
                    "priority": "high"
                }
            })

    except ET.ParseError as e:
        findings.append({
            "id": "FEED-SYNC-MALFORMED",
            "title": "Malformed XML Sitemap",
            "severity": "medium",
            "evidence": f"The sitemap file could not be parsed: {e}",
            "suggested_action": {
                "summary": "Ensure the sitemap strictly follows standard XML syntax. Malformed sitemaps are dropped by AI search indexers.",
                "priority": "medium"
            }
        })

    return findings

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--cache-dir", required=True)
    args = parser.parse_args()
    
    print(json.dumps(audit_syndication(args.cache_dir), indent=2))