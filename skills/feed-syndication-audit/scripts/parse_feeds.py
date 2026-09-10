import json
import argparse
import xml.etree.ElementTree as ET
from pathlib import Path

def audit_syndication(cache_dir: str) -> list[dict]:
    findings = []
    sitemap_path = Path(cache_dir) / "sitemap.xml"
    index_file = Path(cache_dir) / "cache_index.json"

    # Read the orchestrator's state to contextualize the missing sitemap
    crawl_status = "unknown"
    if index_file.exists():
        try:
            manifest = json.loads(index_file.read_text(encoding="utf-8"))
            crawl_status = manifest.get("meta", {}).get("crawl_status", "unknown")
        except json.JSONDecodeError:
            pass

    if not sitemap_path.exists():
        # If the site was blocked by a WAF/robots, the network skill already reported it.
        # But if the site is openly crawlable and just lacks a sitemap, we flag it here.
        if crawl_status == "success":
            findings.append({
                "id": "FEED-SYNC-MISSING",
                "title": "Missing Public XML Sitemap",
                "severity": "medium",
                "evidence": "The site allows direct crawling, but no standard XML sitemap was found at the root or declared in robots.txt.",
                "suggested_action": {
                    "summary": "Even when direct HTML crawling is permitted, AI search engines rely on XML sitemaps to discover new content efficiently without brute-forcing your site architecture. Publish a sitemap.xml to guarantee rapid indexing.",
                    "priority": "medium"
                }
            })
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
                    "summary": "The site exposes a substantial public information architecture. Ensure this sitemap is submitted via Google Search Console and Bing Webmaster Tools to feed AI grounding indices.",
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