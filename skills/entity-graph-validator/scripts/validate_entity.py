import argparse
import json
import os
from pathlib import Path
from collections import defaultdict

import requests
from bs4 import BeautifulSoup

# Schema.org types that count as a "core" brand/organization entity node.
ORG_TYPES = {"Organization", "Brand", "Corporation", "LocalBusiness"}

# page_type values (as emitted in cache_index.json) that are expected to carry
# a Product node. Adjust this if your crawler labels product pages differently.
PRODUCT_PAGE_TYPES = {"product"}

MIN_SAME_AS = 2
REQUIRED_ORG_PROPS = ["name", "url", "logo"]


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


def load_manifest(cache_dir: str) -> dict:
    index_path = Path(cache_dir) / "cache_index.json"
    if not index_path.exists():
        return {}
    try:
        return json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _types_of(schema: dict) -> set:
    raw_type = schema.get("@type", [])
    if isinstance(raw_type, str):
        return {raw_type}
    if isinstance(raw_type, list):
        return set(raw_type)
    return set()


def extract_page_schemas(html: str) -> tuple[list, int]:
    """Returns (parsed_schema_nodes, malformed_script_count) for one page."""
    soup = BeautifulSoup(html, "html.parser")
    scripts = soup.find_all("script", type="application/ld+json")
    nodes = []
    malformed = 0
    for script in scripts:
        if not script.string:
            continue
        try:
            raw_data = json.loads(script.string)
            nodes.extend(unpack_schemas(raw_data))
        except (json.JSONDecodeError, TypeError):
            malformed += 1
    return nodes, malformed


def _single_page_fallback(target_url: str) -> list:
    """No manifest available — fetch the single target URL directly (legacy mode)."""
    try:
        resp = requests.get(target_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        html_content = resp.text
    except Exception as err:
        return [{
            "id": "ENTITY-ERR-000",
            "title": "Entity Validation Execution Error",
            "severity": "medium",
            "evidence": f"Failed to fetch target URL for structured data: {str(err)}",
            "suggested_action": None,
        }]

    nodes, malformed = extract_page_schemas(html_content)
    if not nodes and not malformed:
        return [{
            "id": "ENTITY-CRIT-001",
            "title": "Missing Machine-Readable Entity Graph",
            "severity": "critical",
            "evidence": f"Zero application/ld+json structured data scripts found on {target_url}.",
            "suggested_action": {
                "summary": "Implement an Organization or Brand JSON-LD schema to explicitly define your identity for AI assistants.",
                "priority": "critical",
            },
        }]
    return [{
        "id": "ENTITY-SINGLE-PAGE-000",
        "title": "Entity Graph Checked on One Page Only",
        "severity": "info",
        "evidence": (
            f"No crawl manifest (cache_index.json) was found, so only {target_url} itself was checked "
            f"({len(nodes)} schema node(s), {malformed} malformed script(s)). Site-wide entity checks "
            f"(per-page-type coverage, cross-page sameAs, name conflicts) require a full crawl manifest."
        ),
        "suggested_action": None,
    }]


def validate_entity_graph(target_url: str, cache_dir: str = "audit-cache") -> list:
    manifest = load_manifest(cache_dir)
    all_pages = manifest.get("pages", [])
    pages = [p for p in all_pages if p.get("content_classification") == "normal"]

    if not pages:
        return _single_page_fallback(target_url)

    findings = []
    site_has_json_ld = False
    site_has_org = False
    all_same_as = set()
    org_names = set()
    org_missing_props = defaultdict(list)  # prop -> [page urls missing it]
    malformed_pages = []
    product_pages_checked = 0
    product_pages_missing_schema = []

    for page in pages:
        path = page.get("file")
        page_url = page.get("final_url") or page.get("url", "unknown")
        if not path or not os.path.exists(path):
            continue
        try:
            html = Path(path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        nodes, malformed = extract_page_schemas(html)
        if malformed:
            malformed_pages.append(page_url)
        if nodes:
            site_has_json_ld = True

        page_types = set()
        for schema in nodes:
            types = _types_of(schema)
            page_types |= types

            if types & ORG_TYPES:
                site_has_org = True
                for prop in REQUIRED_ORG_PROPS:
                    if not schema.get(prop):
                        org_missing_props[prop].append(page_url)
                name = schema.get("name")
                if isinstance(name, str) and name.strip():
                    org_names.add(name.strip())

                same_as = schema.get("sameAs", [])
                if isinstance(same_as, str):
                    same_as = [same_as]
                for link in same_as:
                    if isinstance(link, str) and link.startswith("http"):
                        all_same_as.add(link)

            if types & {"Product"}:
                pass  # presence recorded via page_types below

        if page.get("page_type") in PRODUCT_PAGE_TYPES:
            product_pages_checked += 1
            if "Product" not in page_types:
                product_pages_missing_schema.append(page_url)

    # --- Site-wide findings, from strongest to weakest signal ---

    if not site_has_json_ld:
        return [{
            "id": "ENTITY-CRIT-001",
            "title": "Missing Machine-Readable Entity Graph",
            "severity": "critical",
            "evidence": f"Scanned {len(pages)} crawled pages: zero application/ld+json scripts found on any of them.",
            "suggested_action": {
                "summary": "Implement an Organization/Brand JSON-LD schema site-wide, and Product schema on every product page.",
                "priority": "critical",
            },
        }]

    if not site_has_org:
        findings.append({
            "id": "ENTITY-HIGH-002",
            "title": "No Core Organization/Brand Schema",
            "severity": "high",
            "evidence": f"Structured data exists on {len(pages)} scanned pages, but none contains an Organization, Brand, Corporation, or LocalBusiness node.",
            "suggested_action": {
                "summary": "Embed an Organization JSON-LD node (name, logo, url) on the homepage and reference it site-wide.",
                "priority": "high",
            },
        })
    else:
        if len(all_same_as) < MIN_SAME_AS:
            findings.append({
                "id": "ENTITY-MED-003",
                "title": "Weak External Entity Consensus (sameAs Links)",
                "severity": "medium",
                "evidence": f"Organization schema found, but only {len(all_same_as)} unique external 'sameAs' authority link(s) across the whole site: {sorted(all_same_as) or 'none'}.",
                "suggested_action": {
                    "summary": "Add 'sameAs' links to Wikidata, Crunchbase, and official social channels to reduce entity-disambiguation risk.",
                    "priority": "medium",
                },
            })

        missing_prop_list = [p for p, urls in org_missing_props.items() if urls]
        if missing_prop_list:
            findings.append({
                "id": "ENTITY-INCOMPLETE-004",
                "title": "Organization Schema Missing Required Properties",
                "severity": "medium",
                "evidence": "; ".join(
                    f"'{prop}' missing on {len(urls)} page(s), e.g. {urls[0]}"
                    for prop, urls in org_missing_props.items() if urls
                ),
                "suggested_action": {
                    "summary": "Populate name, url, and logo on every Organization node — partial nodes are weaker anchors for AI assistants than they look.",
                    "priority": "medium",
                },
            })

        if len(org_names) > 1:
            findings.append({
                "id": "ENTITY-CONFLICT-005",
                "title": "Inconsistent Organization Name Across Pages",
                "severity": "medium",
                "evidence": f"Organization/Brand nodes declare {len(org_names)} different 'name' values across the site: {sorted(org_names)}.",
                "suggested_action": {
                    "summary": "Standardize the Organization 'name' (and legalName, if used) to a single canonical string across every page's structured data.",
                    "priority": "medium",
                },
            })

    if product_pages_checked and product_pages_missing_schema:
        findings.append({
            "id": "ENTITY-PRODUCT-006",
            "title": "Product Pages Missing Product Schema",
            "severity": "high" if len(product_pages_missing_schema) >= max(1, product_pages_checked // 2) else "medium",
            "evidence": (
                f"{len(product_pages_missing_schema)}/{product_pages_checked} product-type pages have no Product "
                f"JSON-LD node. Examples: {', '.join(product_pages_missing_schema[:5])}."
            ),
            "suggested_action": {
                "summary": "Add a Product node (name, image, sku, offers.price, offers.priceCurrency) to every product page template.",
                "priority": "high",
            },
        })

    if malformed_pages:
        findings.append({
            "id": "ENTITY-MALFORMED-007",
            "title": "Unparseable JSON-LD Found",
            "severity": "medium",
            "evidence": f"{len(malformed_pages)} page(s) contain an application/ld+json script that failed to parse as JSON, e.g. {malformed_pages[0]}.",
            "suggested_action": {
                "summary": "Fix JSON syntax errors in structured-data blocks — a script tag that fails to parse is invisible to schema-reading crawlers, identical to having none.",
                "priority": "medium",
            },
        })

    if not findings:
        findings.append({
            "id": "ENTITY-PASS-000",
            "title": "Robust Semantic Entity Graph",
            "severity": "info",
            "evidence": (
                f"Verified Organization/Brand schema with {len(all_same_as)} unique sameAs links, consistent naming, "
                f"and complete required properties across {len(pages)} scanned pages."
            ),
            "suggested_action": None,
        })

    return findings


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--cache-dir", default="audit-cache")
    args = parser.parse_args()

    results = validate_entity_graph(args.url, cache_dir=args.cache_dir)
    print(json.dumps(results, indent=2))