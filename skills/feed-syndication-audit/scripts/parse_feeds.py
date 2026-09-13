import argparse
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse, urljoin
import requests

def audit_syndication(url: str, cache_dir: str) -> list[dict]:
    findings = []
    sitemap_path = Path(cache_dir) / "sitemap.xml"
    index_file = Path(cache_dir) / "cache_index.json"

    # Read the orchestrator's state to contextualize the sitemap
    crawl_status = "unknown"
    crawled_pages_count = 0
    candidate_url_total = 0
    
    if index_file.exists():
        try:
            manifest = json.loads(index_file.read_text(encoding="utf-8"))
            meta = manifest.get("meta", {})
            crawl_status = meta.get("crawl_status", "unknown")
            crawled_pages_count = len(manifest.get("pages", []))
            candidate_url_total = meta.get("candidate_url_total", crawled_pages_count)
        except json.JSONDecodeError:
            pass

    # --- 1. XML SITEMAP AUDIT ---
    if not sitemap_path.exists():
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
    else:
        try:
            # Strip namespaces from tags for easier parsing
            it = ET.iterparse(sitemap_path)
            for _, el in it:
                _, _, el.tag = el.tag.rpartition('}')
            root = it.root

            urls = [elem.text for elem in root.iter('loc') if elem.text]
            url_count = len(urls)

            if url_count > 0:
                if url_count < crawled_pages_count:
                    severity = "high"
                    findings.append({
                        "id": "FEED-COVERAGE-001",
                        "title": "Sitemap is Severely Incomplete",
                        "severity": severity,
                        "evidence": (
                            f"The declared XML sitemap contains only {url_count} URL(s), while a "
                            f"shallow diagnostic crawl easily extracted {crawled_pages_count} public pages (and discovered "
                            f"{candidate_url_total} internal links). An incomplete sitemap actively hides your deeper content from AI indices."
                        ),
                        "suggested_action": {
                            "summary": (
                                "Switch from a manual XML file to a dynamic sitemap generator in your CMS or framework "
                                "(e.g., next-sitemap, Yoast). It must automatically synchronize with your published canonical URLs."
                            ),
                            "priority": severity
                        }
                    })
                else:
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

    # --- 2. AI-SPECIFIC MANIFEST AUDIT (/llms.txt, /agents.md) ---
    parsed_url = urlparse(url)
    domain_root = f"{parsed_url.scheme}://{parsed_url.netloc}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0",
        "Accept": "text/markdown, text/plain, */*"
    }
    
    manifest_specs = [
        {"url": f"{domain_root}/llms.txt",   "label": "/llms.txt",   "id_suffix": "LLMSTXT"},
        {"url": f"{domain_root}/agents.md",  "label": "/agents.md",  "id_suffix": "AGENTSMD"},
    ]

    # Fetch every manifest independently — no early break
    found_manifests = []  # list of {"spec": ..., "body": ...}
    for spec in manifest_specs:
        try:
            resp = requests.get(spec["url"], headers=headers, timeout=4, allow_redirects=True)
            if resp.status_code == 200:
                body = resp.text.strip()
                ct = resp.headers.get("Content-Type", "").lower()
                
                # Reject SPA catch-alls that return HTML for everything
                if "text/html" in ct or body.startswith("<!doctype") or "<html" in body[:200].lower():
                    continue
                    
                found_manifests.append({"spec": spec, "body": body})
        except requests.RequestException:
            continue

    if not found_manifests:
        findings.append({
            "id": "FEED-PROACTIVE-LLMSTXT",
            "title": "Publish an AI-Specific Agent Manifest (/llms.txt)",
            "severity": "low",
            "evidence": "No AI-facing markdown manifest (/llms.txt or /agents.md) was found at the domain root.",
            "suggested_action": {
                "summary": (
                    "Publish a standardized /llms.txt file at your domain root following the llms.txt specification. "
                    "Include an H1 title, a blockquote summary of your brand, and curated markdown links to "
                    "your core documentation or product categories to reduce the token cost of live AI retrieval."
                ),
                "priority": "low"
            }
        })
        return findings

    # Evaluate EACH manifest independently
    for manifest in found_manifests:
        spec = manifest["spec"]
        content = manifest["body"]
        m_url = spec["url"]
        id_suffix = spec["id_suffix"]
        is_agents_md = id_suffix == "AGENTSMD"

        has_h1 = bool(re.search(r"^#\s+[^\n]+", content, re.MULTILINE))

        # Extract markdown links: [Title](URL)
        raw_links = re.findall(r"\[([^\]]+)\]\((https?://[^\s\)]+|/[^\s\)]+)\)", content)

        issues = []
        if not has_h1:
            issues.append("missing H1 entity title ('# Brand Name')")

        if is_agents_md:
            # --- agents.md format-specific checks ---
            section_headers = re.findall(r"^##\s+[^\n]+", content, re.MULTILINE)
            has_sections = len(section_headers) >= 2

            # Check for endpoint declarations (URLs with path patterns like /products/, /api/, /.well-known/)
            endpoint_urls = re.findall(
                r"(?:GET|POST|PUT|DELETE|PATCH)\s+(https?://[^\s]+|/[^\s]+)", content
            )
            inline_endpoints = re.findall(
                r"`(?:GET|POST)\s+(/[^`]+)`", content
            )
            has_endpoints = len(endpoint_urls) > 0 or len(inline_endpoints) > 0

            # Check for policy / store-metadata links
            policy_links = [
                lnk for _, lnk in raw_links
                if any(kw in lnk.lower() for kw in ("policy", "policies", "terms", "privacy", "refund", "shipping"))
            ]

            # Check for commerce protocol signals (UCP, MCP, Shop)
            has_commerce_protocol = bool(
                re.search(r"(universal commerce protocol|UCP|MCP|shop\.app|commerce protocol)", content, re.I)
            )

            if not has_sections:
                issues.append("missing structured '## Section' headers to organize agent instructions")
            if not has_endpoints and not raw_links:
                issues.append("contains no API endpoint declarations or resource links for agent discovery")
            if not policy_links and not has_commerce_protocol:
                issues.append("missing store policy links or commerce protocol declaration")

            # Build evidence summary for valid agents.md
            evidence_features = []
            if has_sections:
                evidence_features.append(f"{len(section_headers)} structured sections")
            if has_endpoints:
                evidence_features.append(f"{len(endpoint_urls) + len(inline_endpoints)} API endpoint declarations")
            if raw_links:
                evidence_features.append(f"{len(raw_links)} resource links")
            if policy_links:
                evidence_features.append(f"{len(policy_links)} policy links")
            if has_commerce_protocol:
                evidence_features.append("commerce protocol (UCP/MCP) support declared")
        else:
            # --- llms.txt format-specific checks ---
            has_summary_quote = bool(re.search(r"^>\s+[^\n]+", content, re.MULTILINE))
            if not has_summary_quote:
                issues.append("missing blockquote summary ('> Brand Overview')")
            if not raw_links:
                issues.append("contains no structured markdown links to core resources")

            evidence_features = []
            if raw_links:
                evidence_features.append(f"{len(raw_links)} structured resource links")

        # Sample link targets to ensure they don't 404
        dead_links = []
        for title, link in raw_links[:3]:
            full_link = urljoin(domain_root, link)
            try:
                chk = requests.head(full_link, headers=headers, timeout=3, allow_redirects=True)
                if chk.status_code in {404, 410}:
                    dead_links.append(f"{title} ({full_link})")
            except requests.RequestException:
                pass

        if dead_links:
            issues.append(f"contains dead internal links: {', '.join(dead_links)}")

        # Check for companion llms-full.txt (only relevant for /llms.txt)
        has_full_companion = False
        if m_url.endswith("/llms.txt"):
            try:
                full_resp = requests.head(f"{domain_root}/llms-full.txt", headers=headers, timeout=2)
                has_full_companion = (full_resp.status_code == 200)
            except requests.RequestException:
                pass

        # Surface findings based on audit quality
        if issues:
            if is_agents_md:
                action_summary = (
                    f"Structure your {spec['label']} with a clear '# Store Name' heading, "
                    "'## Section' headers for browsing endpoints, commerce protocols, and store policies. "
                    "Include API endpoint declarations (e.g., 'GET /products/{{handle}}.json') and "
                    "links to privacy/terms/refund policies."
                )
            else:
                action_summary = (
                    f"Align your {spec['label']} with the standardized format: start with '# Brand' and a '> Summary' quote, "
                    "followed by '## Section' headers and valid bulleted links: '- [Page Title](url): Description'."
                )

            findings.append({
                "id": f"FEED-AI-MANIFEST-DEFECT-{id_suffix}",
                "title": f"AI Manifest Found but Incomplete or Malformed ({spec['label']})",
                "severity": "medium",
                "evidence": f"Found manifest at {m_url}, but structural issues were detected: {'; '.join(issues)}.",
                "suggested_action": {
                    "summary": action_summary,
                    "priority": "medium"
                }
            })
        else:
            companion_note = " Companion /llms-full.txt detected." if has_full_companion else ""
            features_str = ", ".join(evidence_features) if evidence_features else "valid structure"
            findings.append({
                "id": f"FEED-AI-MANIFEST-VALID-{id_suffix}",
                "title": f"Standardized AI Manifest Verified ({spec['label']})",
                "severity": "info",
                "evidence": (
                    f"Successfully parsed {m_url}. Declares clean canonical H1, "
                    f"{features_str}.{companion_note}"
                ),
                "suggested_action": None
            })

    return findings

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--cache-dir", required=True)
    args = parser.parse_args()
    
    print(json.dumps(audit_syndication(args.url, args.cache_dir), indent=2))