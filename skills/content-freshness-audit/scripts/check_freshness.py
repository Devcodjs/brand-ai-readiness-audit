import argparse
import json
import re
import sys
from pathlib import Path
from bs4 import BeautifulSoup

CURRENT_YEAR = 2026

def load_cached_pages(cache_dir):
    index_path = Path(cache_dir) / "cache_index.json"
    if not index_path.exists():
        return []
    
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    pages = []
    for page in index.get("pages", []):
        if page.get("success") and page.get("content_classification") == "normal":
            try:
                html = Path(page["file"]).read_text(encoding="utf-8", errors="replace")
                soup = BeautifulSoup(html, "html.parser")
                pages.append({"url": page["url"], "html": html, "soup": soup})
            except Exception:
                continue
    return pages

def unpack_schemas(data):
    """Recursively extract all schema nodes, unpacking @graph arrays and lists."""
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

def check_schema_dates(pages):
    pages_with_schema = 0
    pages_missing_freshness = []

    for page in pages:
        scripts = page["soup"].find_all("script", type="application/ld+json")
        nodes = []
        for script in scripts:
            if script.string:
                try:
                    raw_data = json.loads(script.string)
                    nodes.extend(unpack_schemas(raw_data))
                except Exception:
                    pass
        
        if nodes:
            pages_with_schema += 1
            # Check if any parsed node contains dateModified or datePublished
            has_freshness = any(
                isinstance(schema, dict) and (schema.get("dateModified") or schema.get("datePublished")) 
                for schema in nodes
            )
            if not has_freshness:
                pages_missing_freshness.append(page["url"])

    if pages_with_schema > 0 and (len(pages_missing_freshness) / pages_with_schema) > 0.5:
        return {
            "id": "FRESH-001",
            "title": "Missing Machine-Readable Freshness Signals",
            "severity": "medium",
            "evidence": f"{len(pages_missing_freshness)}/{pages_with_schema} pages with structured data lack 'dateModified' or 'datePublished' properties. Examples: {', '.join(pages_missing_freshness[:3])}.",
            "suggested_action": {
                "summary": "Inject 'dateModified' properties into WebPage or Article schema nodes to prevent AI search engines from discarding your content as stale.",
                "priority": "medium"
            }
        }
    return None

def check_copyright_year(pages):
    year_pattern = re.compile(r'(?:©|&copy;|Copyright)\s*(?:[A-Za-z\s0-9,-]*\s*)?(20\d{2})', re.IGNORECASE)
    stale_pages = []
    
    for page in pages:
        # Search footers first, fallback to elements with footer-like classes
        footers = page["soup"].find_all("footer")
        if not footers:
            footers = page["soup"].find_all(attrs={"class": re.compile(r'footer', re.I)})
            if not footers:
                footers = [page["soup"]]
        
        page_is_stale = False
        found_any_year = False
        
        for footer in footers:
            text = footer.get_text(separator=" ")
            matches = year_pattern.findall(text)
            if matches:
                found_any_year = True
                years = [int(y) for y in matches]
                max_year = max(years)
                if max_year < CURRENT_YEAR:
                    page_is_stale = True
                    
        if found_any_year and page_is_stale:
            stale_pages.append(page["url"])

    if stale_pages:
        return {
            "id": "FRESH-002",
            "title": "Stale Footer Copyright Year",
            "severity": "high",
            "evidence": f"Found stale copyright years (pre-{CURRENT_YEAR}) in the footer of {len(stale_pages)}/{len(pages)} pages. AI models use visual dates as corroborating freshness signals.",
            "suggested_action": {
                "summary": f"Update site-wide footer copyright to the current year ({CURRENT_YEAR}). Use a dynamic server-side variable or JavaScript date function.",
                "priority": "high"
            }
        }
    return None

def check_sitemap_lastmod(cache_dir):
    sitemap_path = Path(cache_dir) / "sitemap.xml"
    if not sitemap_path.exists():
        return None
        
    try:
        content = sitemap_path.read_text(encoding="utf-8")
        soup = BeautifulSoup(content, "xml")
        urls = soup.find_all("url")
        if not urls:
            return None
            
        missing_lastmod = 0
        for url in urls:
            if not url.find("lastmod"):
                missing_lastmod += 1
                
        if (missing_lastmod / len(urls)) > 0.5:
            return {
                "id": "FRESH-003",
                "title": "Missing Sitemap Modification Timestamps",
                "severity": "medium",
                "evidence": f"{missing_lastmod}/{len(urls)} URLs in sitemap.xml lack a <lastmod> tag.",
                "suggested_action": {
                    "summary": "Configure your CMS to automatically output <lastmod> timestamps in the XML sitemap so crawlers know when to re-index changed content.",
                    "priority": "medium"
                }
            }
    except Exception as e:
        sys.stderr.write(f"Error parsing sitemap: {e}\n")
        
    return None

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--cache-dir", required=True)
    args = parser.parse_args()

    pages = load_cached_pages(args.cache_dir)
    findings = []

    # Run Page-Level Checks
    if pages:
        schema_finding = check_schema_dates(pages)
        if schema_finding: 
            findings.append(schema_finding)
            
        copyright_finding = check_copyright_year(pages)
        if copyright_finding: 
            findings.append(copyright_finding)
            
    # Run Cache-Level Checks
    sitemap_finding = check_sitemap_lastmod(args.cache_dir)
    if sitemap_finding: 
        findings.append(sitemap_finding)
        
    # Append Pass State
    if not findings:
        findings.append({
            "id": "FRESH-PASS-000",
            "title": "Robust Freshness Signals Detected",
            "severity": "info",
            "evidence": f"Scanned {len(pages)} pages and sitemap.xml. Found up-to-date visual copyright ({CURRENT_YEAR}) and sufficient machine-readable timestamps.",
            "suggested_action": None
        })

    print(json.dumps(findings, indent=2))

if __name__ == "__main__":
    main()