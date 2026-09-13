#!/usr/bin/env python3
"""Entity / structured-data audit for the Brand AI Readiness marketplace.

Design goals:
- Evidence first: never turn a blocked/challenge/network-failed fetch into a
  semantic defect such as "missing schema".
- Site-wide entity checks over a crawl manifest, with conservative fallbacks.
- Detect Organization/Brand/Person/WebSite, sameAs, naming conflicts, Product coverage and
  malformed JSON-LD.
- Return confidence/coverage metadata so the entrypoint can suppress weak
  findings.
- Recommend-only and read-only; no site mutations.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
from urllib.parse import urlparse

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None  # type: ignore[assignment]


# Broadened to support non-business sites (personal portfolios, wikis, editorial sites)
CORE_ENTITY_TYPES = {"Organization", "Brand", "Corporation", "LocalBusiness", "Person", "WebSite", "NewsMediaOrganization"}
PRODUCT_TYPES = {"Product", "ProductGroup"}
PRODUCT_PAGE_TYPES = {"product"}

# Stripped down to universally required props for ANY core entity (Person/WebSite may not require a logo)
REQUIRED_CORE_PROPS = ("name", "url")
MIN_SAME_AS = 2
USER_AGENT = "BrandAIReadinessAudit/2.0 (+read-only; no-site-changes)"
DEFAULT_TIMEOUT = 12

BLOCKED_PATH_MARKERS = (
    "/blocked",
    "/captcha",
    "/challenge",
    "/verify",
    "/security-check",
    "/robot-check",
    "/access-denied",
    "/px/captcha",
)
CHALLENGE_TEXT_MARKERS = (
    "verify you are human",
    "verify you are a human",
    "access denied",
    "request blocked",
    "checking your browser",
    "enable javascript and cookies",
    "unusual traffic",
    "robot check",
    "captcha",
    "security verification",
    "automated access",
    "pardon our interruption",
    "automated bot activity",
    "perimeterx",
)


def _norm(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _types_of(schema: dict) -> Set[str]:
    raw_type = schema.get("@type", [])
    if isinstance(raw_type, str):
        return {raw_type}
    if isinstance(raw_type, list):
        return {str(x) for x in raw_type if isinstance(x, (str, int, float))}
    return set()


def unpack_schemas(data: Any) -> List[dict]:
    out: List[dict] = []
    if isinstance(data, list):
        for item in data:
            out.extend(unpack_schemas(item))
    elif isinstance(data, dict):
        graph = data.get("@graph")
        if isinstance(graph, list):
            out.extend(unpack_schemas(graph))
            if "@type" in data:
                out.append(data)
        else:
            out.append(data)
    return out


def extract_page_schemas(html: str) -> Tuple[List[dict], int]:
    soup = BeautifulSoup(html or "", "html.parser")
    nodes: List[dict] = []
    malformed = 0
    for script in soup.find_all("script", type="application/ld+json"):
        text = script.string if script.string is not None else script.get_text()
        if not text or not text.strip():
            continue
        try:
            raw = json.loads(text)
        except (json.JSONDecodeError, TypeError, ValueError):
            malformed += 1
            continue
        nodes.extend(unpack_schemas(raw))
    return nodes, malformed


def extract_page_signals(html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html or "", "html.parser")
    title = _norm(soup.title.get_text(" ", strip=True)) if soup.title else ""
    body_text = _norm(soup.get_text(" ", strip=True))
    lower = body_text.lower()
    canonical = ""
    canonical_tag = soup.find("link", rel=lambda x: x and "canonical" in x)
    if canonical_tag and canonical_tag.get("href"):
        canonical = canonical_tag.get("href")

    markers = [m for m in CHALLENGE_TEXT_MARKERS if m in lower]
    return {
        "title": title,
        "body_len": len(body_text),
        "canonical": canonical,
        "challenge_markers": markers,
    }


def looks_like_challenge(
    *,
    url: str = "",
    final_url: str = "",
    html: str = "",
    classification: str = "",
) -> Tuple[bool, str]:
    urls = [url or "", final_url or ""]
    path_hit = any(any(marker in (urlparse(u).path or "").lower() for marker in BLOCKED_PATH_MARKERS) for u in urls)
    if path_hit:
        return True, "challenge_path"

    if classification.lower() in {"challenge", "blocked", "bot_challenge", "auth_wall", "access_denied", "captcha"}:
        return True, classification.lower()

    signals = extract_page_signals(html)
    if signals["challenge_markers"] and signals["body_len"] < 20000:
        return True, "challenge_text"

    if signals["body_len"] < 250 and not signals["title"]:
        return True, "empty_interstitial"

    return False, ""


def _safe_fetch(url: str, timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"},
            timeout=timeout,
            allow_redirects=True,
        )
    except requests.exceptions.SSLError as exc:
        return {"ok": False, "kind": "tls_error", "error": str(exc)}
    except requests.exceptions.Timeout as exc:
        return {"ok": False, "kind": "timeout", "error": str(exc)}
    except requests.exceptions.ConnectionError as exc:
        return {"ok": False, "kind": "connection_error", "error": str(exc)}
    except requests.RequestException as exc:
        return {"ok": False, "kind": "request_error", "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "kind": "unknown_error", "error": str(exc)}

    content_type = (resp.headers.get("content-type") or "").lower()
    text = resp.text
    challenged, reason = looks_like_challenge(url=url, final_url=resp.url, html=text)
    if challenged:
        return {
            "ok": False,
            "kind": "challenge",
            "reason": reason,
            "status": resp.status_code,
            "final_url": resp.url,
        }

    if not (200 <= resp.status_code < 400):
        return {
            "ok": False,
            "kind": "http_error",
            "status": resp.status_code,
            "final_url": resp.url,
        }

    if "html" not in content_type and "xhtml" not in content_type and not text.lstrip().startswith("<"):
        return {
            "ok": False,
            "kind": "non_html",
            "status": resp.status_code,
            "content_type": content_type,
            "final_url": resp.url,
        }

    return {
        "ok": True,
        "status": resp.status_code,
        "final_url": resp.url,
        "html": text,
        "content_type": content_type,
    }

def is_reference_or_wiki_page(html: str, url: str) -> bool:
    """Positive-evidence check for encyclopedic/reference platforms."""
    url_lower = (url or "").lower()
    reference_domains = [
        "wikipedia.org", "wikimedia.org", "wiktionary.org", 
        "wikidata.org", "fandom.com", "wikivoyage.org"
    ]
    if any(d in url_lower for d in reference_domains):
        return True

    soup = BeautifulSoup(html or "", "html.parser")
    generator = soup.find("meta", attrs={"name": "generator"})
    if generator and "mediawiki" in (generator.get("content") or "").lower():
        return True

    body = soup.find("body")
    if body:
        body_classes = " ".join(body.get("class", [])).lower()
        if "mediawiki" in body_classes:
            return True

    return False

def load_manifest(cache_dir: str) -> dict:
    path = Path(cache_dir) / "cache_index.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _page_is_usable(page: dict, html: Optional[str]) -> Tuple[bool, str]:
    if not page:
        return False, "missing_page_record"
    classification = _norm(page.get("content_classification")).lower()
    
    if classification in {"blocked", "bot_challenge", "challenge", "auth_wall", "access_denied", "captcha"}:
        return False, f"classified_as_{classification}"
    
    if classification and classification != "normal":
        return False, classification
        
    url = page.get("url", "")
    final_url = page.get("final_url", "")
    if html is not None:
        challenged, reason = looks_like_challenge(url=url, final_url=final_url, html=html, classification=classification)
        if challenged:
            return False, reason
    return True, "normal"


def _load_page_html(page: dict) -> Tuple[Optional[str], Optional[str]]:
    path = page.get("file")
    if path and os.path.exists(path):
        try:
            return Path(path).read_text(encoding="utf-8", errors="ignore"), None
        except OSError as exc:
            return None, f"file_read_error:{exc}"
    return None, "missing_cached_html"


def _same_as_values(schema: dict) -> Set[str]:
    raw = schema.get("sameAs", [])
    if isinstance(raw, str):
        raw = [raw]
    out = set()
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, str) and item.startswith(("http://", "https://")):
            out.add(item.rstrip("/"))
    return out


def _core_schema_summary(nodes: Sequence[dict]) -> Dict[str, Any]:
    core_nodes = []
    for node in nodes:
        if _types_of(node) & CORE_ENTITY_TYPES:
            core_nodes.append(node)
    names = {_norm(n.get("name")) for n in core_nodes if _norm(n.get("name"))}
    same_as = set()
    missing = defaultdict(int)
    urls = set()
    for node in core_nodes:
        same_as.update(_same_as_values(node))
        if _norm(node.get("url")):
            urls.add(_norm(node.get("url")))
        for prop in REQUIRED_CORE_PROPS:
            if not node.get(prop):
                missing[prop] += 1
    return {
        "count": len(core_nodes),
        "names": names,
        "same_as": same_as,
        "missing": dict(missing),
        "urls": urls,
    }


def _finding(fid: str, title: str, severity: str, evidence: str, summary: str, priority: str) -> Dict[str, Any]:
    return {
        "id": fid,
        "title": title,
        "severity": severity,
        "evidence": evidence,
        "suggested_action": {
            "summary": summary,
            "priority": priority,
        },
    }


def _fallback_single_page(target_url: str) -> Tuple[List[dict], Dict[str, Any]]:
    fetched = _safe_fetch(target_url)
    meta = {
        "mode": "single_page_fallback",
        "pages_seen": 0,
        "pages_usable": 0,
        "pages_challenge": 0,
        "network_status": "ok" if fetched.get("ok") else fetched.get("kind", "failed"),
        "evidence_confidence": "LOW",
    }
    if not fetched.get("ok"):
        return [
            _finding(
                "ENTITY-EVIDENCE-001",
                "Entity Audit Suppressed: Target Not Semantically Reachable",
                "medium",
                f"The entity validator could not retrieve a normal HTML page from {target_url}; transport/challenge result: {fetched.get('kind')}. No missing-schema conclusion was made.",
                "Restore normal public HTML access before evaluating JSON-LD. Treat network/challenge failures as crawl evidence, not as missing entity data.",
                "high",
            )
        ], meta

    html = fetched["html"]
    nodes, malformed = extract_page_schemas(html)
    meta.update({"pages_seen": 1, "pages_usable": 1})

    findings: List[dict] = []
    if not nodes and malformed == 0:
        findings.append(_finding(
            "ENTITY-HIGH-001",
            "No JSON-LD Structured Data Observed",
            "high",
            f"1 normal HTML page was fetched successfully from {target_url}, but no application/ld+json nodes were found.",
            "Install an SEO plugin or configure your global <Head> component to auto-inject a standard core JSON-LD schema (e.g. Organization, Person, or WebSite) sitewide.",
            "high",
        ))
    elif malformed:
        findings.append(_finding(
            "ENTITY-MED-001",
            "Malformed JSON-LD Detected",
            "medium",
            f"The fetched page contains {malformed} application/ld+json script(s) that could not be parsed as JSON.",
            "If maintaining raw JSON strings is causing syntax errors, use a schema-builder library (like schema-dts for React/TypeScript) or your framework's native SEO module to programmatically generate type-safe JSON-LD.",
            "medium",
        ))

    summary = _core_schema_summary(nodes)
    if summary["count"] == 0 and nodes:
        findings.append(_finding(
            "ENTITY-HIGH-002",
            "Core Canonical Entity Not Declared",
            "high",
            f"Structured data was present on the normal page, but no Organization, Brand, Person, or WebSite node was found ({len(nodes)} schema node(s) parsed).",
            "Update your global template header or CMS settings to emit a sitewide core JSON-LD node (e.g. Organization or Person). Centralize this data so AI agents have a stable canonical identity to map across the knowledge graph.",
            "high",
        ))

    if not findings:
        findings.append({
            "id": "ENTITY-PASS-000",
            "title": "Core Entity Schema Observed",
            "severity": "info",
            "evidence": f"A normal page was fetched and machine-readable schema was parsed successfully; {summary['count']} core entity node(s) and {len(summary['same_as'])} sameAs link(s) were observed.",
            "suggested_action": None,
        })
    return findings, meta


def validate_entity_graph(target_url: str, cache_dir: str = "audit-cache") -> Dict[str, Any]:
    manifest = load_manifest(cache_dir)
    raw_pages = manifest.get("pages", []) if isinstance(manifest, dict) else []

    if not raw_pages:
        findings, meta = _fallback_single_page(target_url)
        return {"findings": findings, "evidence": meta, "proactive_actions": []}

    pages: List[Tuple[dict, str]] = []
    challenge_pages: List[dict] = []
    unusable_reasons = Counter()
    for page in raw_pages:
        if not isinstance(page, dict):
            unusable_reasons["invalid_page_record"] += 1
            continue
        try:
            html, load_error = _load_page_html(page)
            usable, reason = _page_is_usable(page, html)
        except Exception as exc:
            unusable_reasons[f"page_processing_error:{type(exc).__name__}"] += 1
            continue
        if usable and html is not None:
            pages.append((page, html))
        else:
            challenge_pages.append(page)
            unusable_reasons[load_error or reason] += 1

    evidence = {
        "mode": "manifest",
        "pages_seen": len(raw_pages),
        "pages_usable": len(pages),
        "pages_challenge_or_unusable": len(raw_pages) - len(pages),
        "unusable_reasons": dict(unusable_reasons),
        "evidence_confidence": "HIGH" if pages else "LOW",
    }

    if not pages:
        return {
            "findings": [
                _finding(
                    "ENTITY-EVIDENCE-001",
                    "Entity Audit Suppressed: No Normal HTML Evidence",
                    "high",
                    f"The crawl manifest contained {len(raw_pages)} page(s), but 0 were usable normal HTML. Reasons observed: {dict(unusable_reasons) or 'unknown'}.",
                    "Restore access to representative normal pages before drawing conclusions about JSON-LD or entity schema.",
                    "high",
                )
            ],
            "evidence": evidence,
            "proactive_actions": [],
        }

    all_same_as: Set[str] = set()
    core_names: Set[str] = set()
    core_missing_props: defaultdict[str, List[str]] = defaultdict(list)
    product_pages_checked = 0
    product_pages_missing_schema: List[str] = []
    product_pages_with_schema = 0
    malformed_pages: List[str] = []
    pages_with_jsonld = 0
    pages_with_core = 0
    page_schema_counts = Counter()
    core_urls: Set[str] = set()
    page_processing_errors: List[str] = []

    for page, html in pages:
        page_url = page.get("final_url") or page.get("url") or "unknown"
        try:
            nodes, malformed = extract_page_schemas(html)
            if nodes:
                pages_with_jsonld += 1
                for node in nodes:
                    for t in _types_of(node):
                        page_schema_counts[t] += 1
            if malformed:
                malformed_pages.append(page_url)

            core_ent = _core_schema_summary(nodes)
            if core_ent["count"]:
                pages_with_core += 1
                all_same_as.update(core_ent["same_as"])
                core_names.update(core_ent["names"])
                core_urls.update(core_ent["urls"])
                for prop in REQUIRED_CORE_PROPS:
                    if core_ent["missing"].get(prop):
                        core_missing_props[prop].append(page_url)

            page_type = _norm(page.get("page_type")).lower()
            is_product = page_type in PRODUCT_PAGE_TYPES
            if not is_product:
                path = (urlparse(page_url).path or "").lower()
                is_product = any(token in path for token in ("/product/", "/products/", "/dp/", "/p/"))
            if is_product:
                product_pages_checked += 1
                has_product = any(_types_of(node) & PRODUCT_TYPES for node in nodes)
                if has_product:
                    product_pages_with_schema += 1
                else:
                    product_pages_missing_schema.append(page_url)
        except Exception as exc:
            page_processing_errors.append(f"{page_url} ({type(exc).__name__})")
            continue

    evidence.update({
        "pages_with_jsonld": pages_with_jsonld,
        "pages_with_core_schema": pages_with_core,
        "product_pages_checked": product_pages_checked,
        "product_pages_with_schema": product_pages_with_schema,
        "schema_type_counts": dict(page_schema_counts),
        "page_processing_errors": page_processing_errors,
    })

    findings: List[dict] = []

    # Check if this is a wiki platform based on the first usable page
    is_wiki = False
    if pages:
        is_wiki = is_reference_or_wiki_page(pages[0][1], pages[0][0].get("url", ""))

    if pages_with_jsonld == 0:
        if is_wiki:
            findings.append({
                "id": "ENTITY-INFO-WIKI-001",
                "title": "Reference/Wiki Site Detected without JSON-LD",
                "severity": "info",
                "evidence": f"Scanned {len(pages)} normal HTML pages. No JSON-LD observed, but reference platform markers (MediaWiki/Wikipedia) were detected. AI models natively understand wiki structures without corporate schema.",
                "suggested_action": None
            })
        else:
            findings.append(_finding(
                "ENTITY-HIGH-001",
                "No JSON-LD Structured Data Observed",
                "high",
                f"Scanned {len(pages)} normal HTML pages; 0 contained parseable application/ld+json nodes.",
                "Install an SEO plugin (e.g. Yoast/RankMath) or configure your global <Head> component (Next.js/Nuxt) to auto-inject a standard Organization, Person, or WebSite schema sitewide.",
                "high",
            ))

    if pages_with_core == 0 and pages_with_jsonld > 0:
        if not is_wiki: # Only flag the defect if it's NOT a wiki
            findings.append(_finding(
                "ENTITY-HIGH-002",
                "No Canonical Brand, Organization, or Website Entity Declared",
                "high",
                f"Structured data was observed on {pages_with_jsonld}/{len(pages)} normal pages, but 0 core entity nodes (Organization, Brand, Person, or WebSite) were found.",
                "Declare one canonical core entity node with a stable name and URL. Centralize this data in your CMS configuration so AI agents have a stable canonical identity to map across the knowledge graph.",
                "high",
            ))

    if pages_with_core > 0:
        if len(all_same_as) < MIN_SAME_AS:
            findings.append(_finding(
                "ENTITY-MED-003",
                "Weak External Entity Consensus (sameAs)",
                "medium",
                f"Core schema was found on {pages_with_core}/{len(pages)} normal pages, but only {len(all_same_as)} unique sameAs link(s) were declared.",
                "Populate the sameAs array in your global JSON-LD component with your verified social profiles, Wikipedia page, and Wikidata item. This cross-linking establishes 'entity equivalence', merging your site with your broader web footprint in AI knowledge graphs.",
                "medium",
            ))

        if core_names and len(core_names) > 1:
            findings.append(_finding(
                "ENTITY-MED-005",
                "Inconsistent Entity Name Across Pages",
                "medium",
                f"Core identity nodes declare {len(core_names)} distinct name value(s): {sorted(core_names)}.",
                "Centralize your primary entity name string in a single environment variable or CMS global setting, and pass it to all schema templates. This prevents template drift from fragmenting your entity in AI knowledge graphs.",
                "medium",
            ))

        missing_prop_list = [prop for prop in REQUIRED_CORE_PROPS if core_missing_props.get(prop)]
        if missing_prop_list:
            evidence_parts = []
            for prop in missing_prop_list:
                affected_urls = core_missing_props[prop]
                evidence_parts.append(f"'{prop}' missing on {len(affected_urls)} page(s) (e.g., {affected_urls[0]})")
                
            findings.append(_finding(
                "ENTITY-MED-004",
                "Core Entity Nodes Are Missing Critical Props",
                "medium",
                "; ".join(evidence_parts) + ".",
                "Centralize your core entity definition into a single globally imported object or CMS field (Name, Canonical URL). Pass this variable to your schema generator to instantly eliminate partial or fragmented entity nodes sitewide.",
                "medium",
            ))

    if product_pages_checked:
        missing = len(product_pages_missing_schema)
        if missing:
            severity = "high" if missing >= max(1, product_pages_checked // 2) else "medium"
            findings.append(_finding(
                "ENTITY-PRODUCT-006",
                "Product Pages Missing Product Structured Data",
                severity,
                f"{missing}/{product_pages_checked} product-type pages lacked a Product/ProductGroup JSON-LD node. Examples: {', '.join(product_pages_missing_schema[:5])}.",
                "Map your PIM (Product Information Management) system or e-commerce database directly to a global <ProductSchema /> component. Auto-populate @type, price, and availability dynamically so AI agents always scrape real-time commerce data.",
                severity,
            ))

    if malformed_pages:
        findings.append(_finding(
            "ENTITY-MED-007",
            "Malformed JSON-LD Found",
            "medium",
            f"{len(malformed_pages)} normal page(s) contained at least one application/ld+json block that could not be parsed as JSON. Example: {malformed_pages[0]}.",
            "If maintaining raw JSON strings is causing syntax errors, use a schema-builder library (like schema-dts for React/TypeScript) or your framework's native SEO module to programmatically generate type-safe JSON-LD.",
            "medium",
        ))

    if pages_with_core and len(all_same_as) >= MIN_SAME_AS and not core_names:
        findings.append(_finding(
            "ENTITY-PROACTIVE-008",
            "Entity Coverage Is Present but Lacks a Concrete Name",
            "info",
            f"A core schema was observed with {len(all_same_as)} sameAs link(s), but no non-empty name value was parsed.",
            "Make the canonical entity name explicit and stable to ensure reliable AI agent attribution.",
            "low",
        ))

    if not findings:
        if is_wiki:
            findings.append({
                "id": "ENTITY-PASS-WIKI-000",
                "title": "Reference/Wiki Discoverability Verified",
                "severity": "info",
                "evidence": (
                    f"Scanned {len(pages)} normal HTML page(s). Reference platform markers were detected. "
                    "Corporate JSON-LD (Organization/Brand) is not required here; AI models natively "
                    "ingest and understand encyclopedic architecture without explicit entity schema."
                ),
                "suggested_action": None,
            })
        else:
            findings.append({
                "id": "ENTITY-PASS-000",
                "title": "Robust Semantic Entity Graph Observed",
                "severity": "info",
                "evidence": (
                    f"Verified {pages_with_core} page(s) with core entity schema across {len(pages)} normal pages, "
                    f"{len(all_same_as)} unique sameAs link(s), consistent entity naming, and no malformed JSON-LD."
                ),
                "suggested_action": None,
            })

    proactive = []
    if pages_with_jsonld and not page_schema_counts.get("WebPage"):
        proactive.append({
            "id": "PROACTIVE-WEBPAGE-001",
            "title": "Add WebPage-level structured context",
            "summary": "Use WebPage/CollectionPage/Article schema where appropriate so page purpose is explicit and machine-readable.",
            "priority": "low",
        })
    if product_pages_checked == 0:
        proactive.append({
            "id": "PROACTIVE-PRODUCT-002",
            "title": "Sample Product Templates Explicitly",
            "summary": "The current crawl contained no confidently classified product pages; include at least one representative product URL in the next audit when the site is commerce-oriented.",
            "priority": "medium",
        })

    return {
        "findings": findings,
        "evidence": evidence,
        "proactive_actions": proactive,
    }


def _fatal_result(target_url: str, exc: BaseException) -> Dict[str, Any]:
    return {
        "findings": [
            _finding(
                "ENTITY-INTERNAL-ERROR",
                "Entity Audit Failed To Run",
                "high",
                f"The entity validator raised an unhandled {type(exc).__name__} before it could inspect {target_url}: {exc}",
                "This is a validator/runtime fault, not a finding about the target site. Check invocation arguments (--url / --cache-dir) and the cache_index.json shape.",
                "high",
            )
        ],
        "evidence": {"mode": "internal_error", "evidence_confidence": "LOW", "error_type": type(exc).__name__},
        "proactive_actions": [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate site entity graph from a crawl manifest.")
    parser.add_argument("--url", "--target-url", "--site-url", dest="url", required=True, help="Target website URL")
    parser.add_argument("--cache-dir", "--cache_dir", dest="cache_dir", default="audit-cache", help="Directory containing cache_index.json")
    args, _unknown = parser.parse_known_args()

    try:
        result = validate_entity_graph(args.url, cache_dir=args.cache_dir)
    except Exception as exc: 
        result = _fatal_result(args.url, exc)

    print(json.dumps(result["findings"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()