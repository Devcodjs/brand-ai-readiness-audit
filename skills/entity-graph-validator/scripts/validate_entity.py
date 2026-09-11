#!/usr/bin/env python3
"""Entity / structured-data audit for the Brand AI Readiness marketplace.

Design goals:
- Evidence first: never turn a blocked/challenge/network-failed fetch into a
  semantic defect such as "missing schema".
- Site-wide entity checks over a crawl manifest, with conservative fallbacks.
- Detect Organization/Brand, sameAs, naming conflicts, Product coverage and
  malformed JSON-LD.
- Return confidence/coverage metadata so the entrypoint can suppress weak
  findings.
- Recommend-only and read-only; no site mutations.

Compatible with the existing cache_index.json shape used by this marketplace.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import ssl
from collections import Counter, defaultdict
from html import unescape
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


ORG_TYPES = {"Organization", "Brand", "Corporation", "LocalBusiness"}
PRODUCT_TYPES = {"Product", "ProductGroup"}
PRODUCT_PAGE_TYPES = {"product"}
REQUIRED_ORG_PROPS = ("name", "url", "logo")
MIN_SAME_AS = 2
USER_AGENT = "BrandAIReadinessAudit/2.0 (+read-only; no-site-changes)"
DEFAULT_TIMEOUT = 12

# URL/path patterns and body fragments that strongly indicate that the crawler
# did not receive the intended page. These are deliberately conservative.
BLOCKED_PATH_MARKERS = (
    "/blocked",
    "/captcha",
    "/challenge",
    "/verify",
    "/security-check",
    "/robot-check",
    "/access-denied",
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
    """Recursively unpack JSON-LD arrays and @graph objects."""
    out: List[dict] = []
    if isinstance(data, list):
        for item in data:
            out.extend(unpack_schemas(item))
    elif isinstance(data, dict):
        graph = data.get("@graph")
        if isinstance(graph, list):
            out.extend(unpack_schemas(graph))
            # A top-level object may still itself be a schema node.
            if "@type" in data:
                out.append(data)
        else:
            out.append(data)
    return out


def extract_page_schemas(html: str) -> Tuple[List[dict], int]:
    """Return parsed JSON-LD nodes and count of malformed JSON-LD scripts."""
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
    """Lightweight signals used to decide whether a page is semantically usable."""
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
    """Detect common anti-bot/challenge/interstitial pages, including HTTP 200 ones."""
    urls = [url or "", final_url or ""]
    path_hit = any(any(marker in (urlparse(u).path or "").lower() for marker in BLOCKED_PATH_MARKERS) for u in urls)
    if path_hit:
        return True, "challenge_path"

    if classification.lower() in {"challenge", "blocked", "access_denied", "captcha"}:
        return True, classification.lower()

    signals = extract_page_signals(html)
    if signals["challenge_markers"] and signals["body_len"] < 20000:
        return True, "challenge_text"

    # Very short HTML + empty title is a common interstitial symptom. Keep it
    # low-confidence so it doesn't over-classify legitimate utility pages.
    if signals["body_len"] < 250 and not signals["title"]:
        return True, "empty_interstitial"

    return False, ""


def _host_matches(url: str, expected_host: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    expected = (expected_host or "").lower()
    return bool(host and expected and (host == expected or host.endswith("." + expected)))


def _safe_fetch(url: str, timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Fetch read-only and classify transport vs challenge failures."""
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
    except Exception as exc:  # defensive; never crash the marketplace
        return {"ok": False, "kind": "unknown_error", "error": str(exc)}

    content_type = (resp.headers.get("content-type") or "").lower()
    text = resp.text if "text" in content_type or "html" in content_type or not content_type else resp.text
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
    classification = _norm(page.get("content_classification"))
    if classification and classification.lower() != "normal":
        return False, classification.lower()
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


def _org_schema_summary(nodes: Sequence[dict]) -> Dict[str, Any]:
    org_nodes = []
    for node in nodes:
        if _types_of(node) & ORG_TYPES:
            org_nodes.append(node)
    names = {_norm(n.get("name")) for n in org_nodes if _norm(n.get("name"))}
    same_as = set()
    missing = defaultdict(int)
    urls = set()
    for node in org_nodes:
        same_as.update(_same_as_values(node))
        if _norm(node.get("url")):
            urls.add(_norm(node.get("url")))
        for prop in REQUIRED_ORG_PROPS:
            if not node.get(prop):
                missing[prop] += 1
    return {
        "count": len(org_nodes),
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
            "Add machine-readable JSON-LD for the page's core entity (Organization/Brand) and relevant content types; keep the values consistent with visible page content.",
            "high",
        ))
    elif malformed:
        findings.append(_finding(
            "ENTITY-MED-001",
            "Malformed JSON-LD Detected",
            "medium",
            f"The fetched page contains {malformed} application/ld+json script(s) that could not be parsed as JSON.",
            "Fix JSON syntax and validate the final JSON-LD after deployment so machine readers can parse the entity graph.",
            "medium",
        ))

    summary = _org_schema_summary(nodes)
    if summary["count"] == 0 and nodes:
        findings.append(_finding(
            "ENTITY-HIGH-002",
            "Core Organization/Brand Entity Not Declared",
            "high",
            f"Structured data was present on the normal page, but no Organization, Brand, Corporation, or LocalBusiness node was found ({len(nodes)} schema node(s) parsed).",
            "Add a canonical Organization/Brand node with stable name, URL and logo, and link it consistently from the site's relevant structured-data nodes.",
            "high",
        ))

    if not findings:
        findings.append({
            "id": "ENTITY-PASS-000",
            "title": "Core Entity Schema Observed",
            "severity": "info",
            "evidence": f"A normal page was fetched and machine-readable schema was parsed successfully; {summary['count']} organization/brand node(s) and {len(summary['same_as'])} sameAs link(s) were observed.",
            "suggested_action": None,
        })
    return findings, meta


def validate_entity_graph(target_url: str, cache_dir: str = "audit-cache") -> Dict[str, Any]:
    """Validate entity semantics and return findings plus evidence metadata.

    The returned object intentionally includes evidence metadata in addition to
    the traditional findings list. The orchestrator can use it to suppress
    downstream semantic checks when crawl evidence is weak.
    """
    manifest = load_manifest(cache_dir)
    raw_pages = manifest.get("pages", []) if isinstance(manifest, dict) else []

    if not raw_pages:
        findings, meta = _fallback_single_page(target_url)
        return {"findings": findings, "evidence": meta}

    pages: List[Tuple[dict, str]] = []
    challenge_pages: List[dict] = []
    unusable_reasons = Counter()
    for page in raw_pages:
        html, load_error = _load_page_html(page)
        usable, reason = _page_is_usable(page, html)
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

    # Critical safety rule: if no normal pages are available, never report
    # "missing JSON-LD" from the manifest. Report the evidence problem instead.
    if not pages:
        return {
            "findings": [
                _finding(
                    "ENTITY-EVIDENCE-001",
                    "Entity Audit Suppressed: No Normal HTML Evidence",
                    "high",
                    f"The crawl manifest contained {len(raw_pages)} page(s), but 0 were usable normal HTML. Reasons observed: {dict(unusable_reasons) or 'unknown'}.",
                    "Restore access to representative normal pages before drawing conclusions about JSON-LD, Organization schema, Product schema, or sameAs coverage.",
                    "high",
                )
            ],
            "evidence": evidence,
        }

    all_same_as: Set[str] = set()
    org_names: Set[str] = set()
    org_missing_props: defaultdict[str, List[str]] = defaultdict(list)
    product_pages_checked = 0
    product_pages_missing_schema: List[str] = []
    product_pages_with_schema = 0
    malformed_pages: List[str] = []
    pages_with_jsonld = 0
    pages_with_org = 0
    page_schema_counts = Counter()
    org_urls: Set[str] = set()

    for page, html in pages:
        page_url = page.get("final_url") or page.get("url") or "unknown"
        nodes, malformed = extract_page_schemas(html)
        if nodes:
            pages_with_jsonld += 1
            for node in nodes:
                for t in _types_of(node):
                    page_schema_counts[t] += 1
        if malformed:
            malformed_pages.append(page_url)

        org = _org_schema_summary(nodes)
        if org["count"]:
            pages_with_org += 1
            all_same_as.update(org["same_as"])
            org_names.update(org["names"])
            org_urls.update(org["urls"])
            for prop in REQUIRED_ORG_PROPS:
                if org["missing"].get(prop):
                    org_missing_props[prop].append(page_url)

        page_type = _norm(page.get("page_type")).lower()
        # Use the crawler's page_type as primary signal, but do not punish a
        # product-like page merely because its type label is absent.
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

    evidence.update({
        "pages_with_jsonld": pages_with_jsonld,
        "pages_with_org_schema": pages_with_org,
        "product_pages_checked": product_pages_checked,
        "product_pages_with_schema": product_pages_with_schema,
        "schema_type_counts": dict(page_schema_counts),
    })

    findings: List[dict] = []

    # 1. Overall JSON-LD coverage. Avoid calling this critical: a valid HTML
    # page can use microdata/RDFa, and the rubric is about usefulness not one
    # syntax. But for this marketplace we can still recommend JSON-LD.
    if pages_with_jsonld == 0:
        findings.append(_finding(
            "ENTITY-HIGH-001",
            "No JSON-LD Structured Data Observed",
            "high",
            f"Scanned {len(pages)} normal HTML pages; 0 contained parseable application/ld+json nodes.",
            "Add JSON-LD for the site's core entity and key page types, with values that match visible page content. Prioritize Organization/Brand, WebSite/WebPage and Product where applicable.",
            "high",
        ))

    # 2. Core entity graph.
    if pages_with_org == 0 and pages_with_jsonld > 0:
        findings.append(_finding(
            "ENTITY-HIGH-002",
            "No Core Organization/Brand Entity in Structured Data",
            "high",
            f"Structured data was observed on {pages_with_jsonld}/{len(pages)} normal pages, but 0 Organization/Brand/Corporation/LocalBusiness nodes were found.",
            "Declare one canonical Organization/Brand node with stable name, URL and logo, and reference that entity consistently from relevant page schemas.",
            "high",
        ))

    if pages_with_org > 0:
        if len(all_same_as) < MIN_SAME_AS:
            findings.append(_finding(
                "ENTITY-MED-003",
                "Weak External Entity Consensus",
                "medium",
                f"Organization/Brand schema was found on {pages_with_org}/{len(pages)} normal pages, but only {len(all_same_as)} unique sameAs link(s) were declared.",
                "Add only unambiguous official identity links such as the brand's official social profiles and, where the entity is clearly matched, Wikidata/Wikipedia or another authoritative profile. Do not add unrelated third-party pages merely to increase the count.",
                "medium",
            ))

        if org_names and len(org_names) > 1:
            findings.append(_finding(
                "ENTITY-MED-005",
                "Inconsistent Organization Name Across Pages",
                "medium",
                f"Organization/Brand nodes declare {len(org_names)} distinct name value(s): {sorted(org_names)}.",
                "Standardize the canonical Organization/Brand name across templates. Preserve legitimate brand/sub-brand distinctions by using separate entities rather than alternating names on one entity.",
                "medium",
            ))

        missing_prop_list = [prop for prop in REQUIRED_ORG_PROPS if org_missing_props.get(prop)]
        if missing_prop_list:
            evidence_parts = []
            for prop in missing_prop_list:
                evidence_parts.append(f"'{prop}' missing on {len(org_missing_props[prop])} page(s), e.g. {org_missing_props[prop][0]}")
            findings.append(_finding(
                "ENTITY-MED-004",
                "Organization Entity Nodes Are Incomplete",
                "medium",
                "; ".join(evidence_parts),
                "Keep the canonical Organization node complete with name, URL and logo where applicable, and avoid emitting partial duplicate organization nodes across templates.",
                "medium",
            ))

    # 3. Product coverage only where product evidence exists.
    if product_pages_checked:
        missing = len(product_pages_missing_schema)
        if missing:
            severity = "high" if missing >= max(1, product_pages_checked // 2) else "medium"
            findings.append(_finding(
                "ENTITY-PRODUCT-006",
                "Product Pages Missing Product Structured Data",
                severity,
                f"{missing}/{product_pages_checked} product-type pages lacked a Product/ProductGroup JSON-LD node. Examples: {', '.join(product_pages_missing_schema[:5])}.",
                "Add Product/ProductGroup structured data to product templates with truthful product name, image, SKU/identifier and Offer data where available. Keep availability and price synchronized with the rendered page.",
                severity,
            ))

    if malformed_pages:
        findings.append(_finding(
            "ENTITY-MED-007",
            "Malformed JSON-LD Found",
            "medium",
            f"{len(malformed_pages)} normal page(s) contained at least one application/ld+json block that could not be parsed as JSON. Example: {malformed_pages[0]}.",
            "Fix malformed JSON-LD blocks and validate them in CI so the machine-readable graph remains parseable after template changes.",
            "medium",
        ))

    # 4. Proactive improvements are explicitly allowed by the contest brief.
    # They are info-level notes so they don't inflate defect counts.
    if pages_with_org and len(all_same_as) >= MIN_SAME_AS and not org_names:
        findings.append(_finding(
            "ENTITY-PROACTIVE-008",
            "Entity Coverage Is Present but Could Be Cross-Linked More Strongly",
            "info",
            f"A core Organization/Brand schema was observed with {len(all_same_as)} sameAs link(s), but no non-empty Organization name value was parsed for the sampled nodes.",
            "Make the canonical entity name explicit and stable, and keep identity links consistent across templates.",
            "low",
        ))

    if not findings:
        findings.append({
            "id": "ENTITY-PASS-000",
            "title": "Robust Semantic Entity Graph Observed",
            "severity": "info",
            "evidence": (
                f"Verified {pages_with_org} page(s) with Organization/Brand schema across {len(pages)} normal pages, "
                f"{len(all_same_as)} unique sameAs link(s), consistent organization naming, and no malformed JSON-LD on sampled pages."
            ),
            "suggested_action": None,
        })

    # Recommended next steps for the entrypoint, separate from findings.
    proactive = []
    if pages_with_jsonld and not page_schema_counts.get("WebPage"):
        proactive.append({
            "id": "PROACTIVE-WEBPAGE-001",
            "title": "Add WebPage-level structured context",
            "summary": "Use WebPage/CollectionPage/Article/Product schema where appropriate so page purpose is explicit and machine-readable.",
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate site entity graph from a crawl manifest.")
    parser.add_argument("--url", required=True, help="Target website URL")
    parser.add_argument("--cache-dir", default="audit-cache", help="Directory containing cache_index.json")
    args = parser.parse_args()

    result = validate_entity_graph(args.url, cache_dir=args.cache_dir)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
