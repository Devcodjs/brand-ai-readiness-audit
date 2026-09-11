import argparse
import json
import os
import re
from collections import defaultdict
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag

from bs4 import BeautifulSoup


# The crawler's classifier is useful evidence, but it is not authoritative.
# Many modern commerce sites expose category pages as /c/<slug>, /category/<slug>,
# /collections/<slug>, etc. while the crawler may label those pages as "other".
EXPLICIT_CATEGORY_TYPES = {"category", "collection", "listing", "catalog"}
EXCLUDED_PAGE_TYPES = {"product", "home", "search", "other"}

CATEGORY_URL_PATTERNS = [
    re.compile(r"/(?:c|category|categories|collection|collections|catalog|shop)/[^/?#]+", re.I),
]

PRODUCT_URL_PATTERNS = [
    re.compile(r"/(?:p|product|products)/[^/?#]+", re.I),
    re.compile(r"/(?:p|product|products)/", re.I),
]

# These are intentionally generic structural terms rather than brand-specific words.
LISTING_TEXT_HINTS = re.compile(
    r"\b(sort|filter|filters|price|category|categories|results|products|items|shop|collection|collections)\b",
    re.I,
)

GENERIC_COPY_PATTERNS = [
    re.compile(r"\b(sort by|filter by|showing|results|items|products)\b", re.I),
    re.compile(r"\b(add to cart|wishlist|buy now|quick view)\b", re.I),
]

THIN_COPY_WORD_THRESHOLD = 20
DUPLICATE_SIMILARITY_THRESHOLD = 0.82
MIN_PRODUCT_LINKS_FOR_GRID = 3
CROSS_LIST_MIN_CATEGORIES = 3
CROSS_LIST_MIN_SHARE = 0.60


def load_manifest(cache_dir: str) -> dict:
    index_path = Path(cache_dir) / "cache_index.json"
    if not index_path.exists():
        return {}
    try:
        return json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def normalize_url(base_url: str, href: str) -> str:
    try:
        absolute = urljoin(base_url, href)
        absolute, _ = urldefrag(absolute)
        parsed = urlparse(absolute)
        clean_path = parsed.path.rstrip("/") or "/"
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{clean_path}"
    except Exception:
        return href.split("?")[0].rstrip("/")


def is_probable_product_url(href: str) -> bool:
    return any(p.search(href) for p in PRODUCT_URL_PATTERNS)


def has_category_url_signal(url: str) -> bool:
    return any(p.search(url) for p in CATEGORY_URL_PATTERNS)


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_text_for_similarity(text: str) -> str:
    text = text.lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    words = [w for w in text.split() if len(w) > 2]
    return " ".join(words)


def remove_generic_copy(text: str) -> str:
    result = text
    for pattern in GENERIC_COPY_PATTERNS:
        result = pattern.sub(" ", result)
    return clean_text(result)


def extract_category_signals(html: str, page_url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    # Capture useful metadata before removing page chrome.
    title = clean_text(soup.title.get_text(" ", strip=True)) if soup.title else ""
    h1 = clean_text(soup.find("h1").get_text(" ", strip=True)) if soup.find("h1") else ""

    breadcrumbs = []
    for selector in [
        "[aria-label*='breadcrumb' i]",
        ".breadcrumb",
        "nav[aria-label*='breadcrumb' i]",
    ]:
        node = soup.select_one(selector)
        if node:
            breadcrumbs = [clean_text(x.get_text(" ", strip=True)) for x in node.find_all(["a", "span", "li"]) if clean_text(x.get_text(" ", strip=True))]
            if breadcrumbs:
                break

    product_links = set()
    internal_links = []
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        normalized = normalize_url(page_url, href)
        if is_probable_product_url(href) or is_probable_product_url(normalized):
            product_links.add(normalized)
        text = clean_text(a.get_text(" ", strip=True))
        if text:
            internal_links.append(text)

    # Remove page chrome and likely product-card containers from descriptive-copy extraction.
    for node in soup.select("script, style, nav, footer, header, noscript, [role='navigation']"):
        node.decompose()

    # Avoid double-counting product-card text. We keep product links themselves for graph checks.
    for selector in [
        "[class*='product-card' i]",
        "[class*='productcard' i]",
        "[data-testid*='product-card' i]",
        "[data-testid*='productcard' i]",
    ]:
        for node in soup.select(selector):
            node.decompose()

    text = clean_text(" ".join(soup.stripped_strings))
    cleaned_text = remove_generic_copy(text)
    word_count = len(cleaned_text.split())

    # Evidence that this is a listing/category rather than a product-detail page.
    structural_hints = 0
    if soup.find_all("h2") or soup.find_all("h3"):
        structural_hints += 1
    if LISTING_TEXT_HINTS.search(text):
        structural_hints += 1
    if product_links:
        structural_hints += 2
    if breadcrumbs:
        structural_hints += 1

    return {
        "url": page_url,
        "title": title,
        "h1": h1,
        "breadcrumbs": breadcrumbs,
        "desc_text": cleaned_text,
        "word_count": word_count,
        "product_links": product_links,
        "structural_hints": structural_hints,
    }


def classify_category_candidate(page: dict, signals: dict) -> tuple[bool, int, list[str]]:
    reasons = []
    score = 0
    page_type = str(page.get("page_type") or "").lower().strip()
    url = signals["url"]

    # Strong negative evidence first.
    if page_type == "product":
        return False, -99, ["manifest page_type=product"]
    if any(p.search(url) for p in PRODUCT_URL_PATTERNS):
        return False, -99, ["product-like URL"]

    if page_type in EXPLICIT_CATEGORY_TYPES:
        score += 6
        reasons.append(f"manifest page_type={page_type}")

    if has_category_url_signal(url):
        score += 5
        reasons.append("category-like URL path")

    if len(signals["product_links"]) >= MIN_PRODUCT_LINKS_FOR_GRID:
        score += 3
        reasons.append(f"{len(signals['product_links'])} product links")
    elif len(signals["product_links"]) > 0:
        score += 1
        reasons.append(f"{len(signals['product_links'])} product link(s)")

    if signals["breadcrumbs"]:
        score += 1
        reasons.append("breadcrumb detected")

    if signals["structural_hints"] >= 3:
        score += 1
        reasons.append("listing-page structural signals")

    # Need either an explicit category type or a strong URL/listing combination.
    is_candidate = score >= 5 and (
        page_type in EXPLICIT_CATEGORY_TYPES
        or has_category_url_signal(url)
        or (len(signals["product_links"]) >= MIN_PRODUCT_LINKS_FOR_GRID and signals["structural_hints"] >= 3)
    )
    return is_candidate, score, reasons


def choose_category_pages(pages: list[dict]) -> tuple[list[dict], list[dict], dict]:
    candidates = []
    rejected = []
    stats = {
        "manifest_category_count": 0,
        "fallback_category_count": 0,
        "candidate_count": 0,
        "fallback_used": False,
    }

    for page in pages:
        if page.get("content_classification") != "normal":
            continue
        path = page.get("file")
        if not path or not os.path.exists(path):
            rejected.append({"url": page.get("final_url") or page.get("url", "unknown"), "reason": "missing cached HTML"})
            continue

        page_url = page.get("final_url") or page.get("url", "unknown")
        try:
            html = Path(path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            rejected.append({"url": page_url, "reason": "could not read cached HTML"})
            continue

        signals = extract_category_signals(html, page_url)
        ok, score, reasons = classify_category_candidate(page, signals)
        if ok:
            explicit = str(page.get("page_type") or "").lower().strip() in EXPLICIT_CATEGORY_TYPES
            if explicit:
                stats["manifest_category_count"] += 1
            else:
                stats["fallback_category_count"] += 1
                stats["fallback_used"] = True
            candidates.append({**page, "signals": signals, "category_detection": {"score": score, "reasons": reasons, "fallback": not explicit}})
        else:
            rejected.append({"url": page_url, "reason": ", ".join(reasons) if reasons else "insufficient category signals"})

    stats["candidate_count"] = len(candidates)
    return candidates, rejected, stats


def add_finding(findings: list, finding_id: str, title: str, severity: str, evidence: str, action_summary: str, priority: str | None = None):
    findings.append({
        "id": finding_id,
        "title": title,
        "severity": severity,
        "evidence": evidence,
        "suggested_action": None if action_summary is None else {
            "summary": action_summary,
            "priority": priority or severity,
        },
    })


def run_audit(url: str, cache_dir: str = "audit-cache") -> list:
    manifest = load_manifest(cache_dir)
    pages = manifest.get("pages", [])
    if not pages:
        return [{
            "id": "CIA-NO-MANIFEST-000",
            "title": "Category Isolation Audit Could Not Run",
            "severity": "info",
            "evidence": "No crawl manifest pages were available in the cache.",
            "suggested_action": None,
        }]

    category_pages, rejected_pages, detection_stats = choose_category_pages(pages)

    if len(category_pages) < 2:
        reasons = []
        if detection_stats["manifest_category_count"] == 0:
            reasons.append("the crawler did not label any usable page as category/collection")
        if detection_stats["fallback_category_count"] == 1:
            reasons.append("only one page passed fallback category detection")
        if detection_stats["candidate_count"] == 0:
            reasons.append("no page met the minimum URL/structure/listing evidence")
        reason_text = "; ".join(reasons) if reasons else "fewer than two distinct category candidates were available"

        return [{
            "id": "CIA-INSUFFICIENT-000",
            "title": "Not Enough Category Pages to Audit Isolation",
            "severity": "info",
            "evidence": (
                f"Found {detection_stats['candidate_count']} usable category candidate(s) after combining the crawler's "
                f"page_type with URL and page-structure signals; at least 2 are needed to compare category boundaries. "
                f"Reason: {reason_text}."
            ),
            "suggested_action": {
                "summary": "Crawl more representative category/collection URLs or improve page classification. This result is an evidence limitation, not evidence that category isolation is healthy or unhealthy.",
                "priority": "low",
            },
        }]

    findings = []

    if detection_stats["fallback_used"]:
        fallback_urls = [
            c["signals"]["url"] for c in category_pages if c["category_detection"]["fallback"]
        ]
        findings.append({
            "id": "CIA-DETECTION-FALLBACK-000",
            "title": "Category Pages Detected From URL/Structure Signals",
            "severity": "info",
            "evidence": (
                f"The crawler explicitly classified {detection_stats['manifest_category_count']} usable page(s) as category/collection, "
                f"while {detection_stats['fallback_category_count']} additional category candidate(s) were recovered from URL and page-structure evidence. "
                f"Examples: {', '.join(fallback_urls[:5])}."
            ),
            "suggested_action": {
                "summary": "Keep the fallback detection enabled and improve the crawler's page-type classifier so category/collection pages are labeled consistently across sites.",
                "priority": "low",
            },
        })

    # 1. Thin/absent category copy, but only when the page really behaves like a product grid.
    thin_pages = [
        c for c in category_pages
        if c["signals"]["word_count"] < THIN_COPY_WORD_THRESHOLD
        and len(c["signals"]["product_links"]) >= MIN_PRODUCT_LINKS_FOR_GRID
    ]
    if thin_pages:
        examples = "; ".join(
            f"{c['signals']['url']} ({c['signals']['word_count']} descriptive words, {len(c['signals']['product_links'])} product links)"
            for c in thin_pages[:5]
        )
        add_finding(
            findings,
            "CIA-THIN-001",
            "Category Pages Have Little Descriptive Copy",
            "medium",
            f"{len(thin_pages)}/{len(category_pages)} detected category pages have fewer than {THIN_COPY_WORD_THRESHOLD} descriptive words while exposing a product grid. Examples: {examples}.",
            "Add concise, category-specific explanatory copy that states what the collection contains and the primary user intent it serves. Keep the product grid, but expose the category meaning as readable HTML text.",
            "medium",
        )

    # 2. Near-duplicate category copy. Compare normalized descriptive copy, not the whole DOM.
    comparable = [
        c for c in category_pages
        if c["signals"]["word_count"] >= THIN_COPY_WORD_THRESHOLD
    ]
    high_sim_pairs = []
    for a, b in combinations(comparable, 2):
        na = normalize_text_for_similarity(a["signals"]["desc_text"])
        nb = normalize_text_for_similarity(b["signals"]["desc_text"])
        if not na or not nb:
            continue
        ratio = SequenceMatcher(None, na, nb).ratio()
        if ratio >= DUPLICATE_SIMILARITY_THRESHOLD:
            high_sim_pairs.append((a, b, ratio))

    if high_sim_pairs:
        examples = "; ".join(
            f"{a['signals']['url']} vs {b['signals']['url']} ({ratio:.0%})"
            for a, b, ratio in high_sim_pairs[:5]
        )
        add_finding(
            findings,
            "CIA-OVERLAP-002",
            "Category Pages Use Near-Duplicate Descriptive Copy",
            "medium",
            f"{len(high_sim_pairs)} category-page pair(s) exceed {DUPLICATE_SIMILARITY_THRESHOLD:.0%} normalized text similarity. Examples: {examples}.",
            "Write unique category-specific copy for each collection. Keep shared brand language short, but make the category's scope, audience, products, and intent explicit so an AI classifier can distinguish sibling collections.",
            "medium",
        )

    # 3. Product cross-listing. Only report when there is enough graph evidence and a meaningful share is shared.
    product_to_cats = defaultdict(set)
    for c in category_pages:
        for link in c["signals"]["product_links"]:
            product_to_cats[link].add(c["signals"]["url"])

    cross_listed = {
        product: cats
        for product, cats in product_to_cats.items()
        if len(cats) >= CROSS_LIST_MIN_CATEGORIES
    }
    if product_to_cats and cross_listed:
        share = len(cross_listed) / len(product_to_cats)
        if share >= CROSS_LIST_MIN_SHARE:
            sample_product, sample_cats = max(cross_listed.items(), key=lambda x: len(x[1]))
            severity = "medium"
            add_finding(
                findings,
                "CIA-CROSSLIST-003",
                "Heavy Product Overlap Across Category Pages",
                severity,
                (
                    f"{len(cross_listed)}/{len(product_to_cats)} distinct product URLs ({share:.0%}) detected in the sampled category grids "
                    f"appear in at least {CROSS_LIST_MIN_CATEGORIES} categories. Example: {sample_product} appears in {len(sample_cats)} categories: {sorted(sample_cats)}."
                ),
                "Keep cross-listing where it matches genuine merchandising intent, but add category-specific framing and tighten category membership when the same inventory dominates otherwise distinct collections.",
                "medium",
            )

    if not findings:
        findings.append({
            "id": "CIA-PASS-000",
            "title": "Category Boundaries Show No Strong Isolation Signal",
            "severity": "info",
            "evidence": (
                f"Checked {len(category_pages)} detected category/collection pages using descriptive-copy, similarity, and product-overlap signals. "
                "No configured threshold was exceeded."
            ),
            "suggested_action": {
                "summary": "Keep category names, introductory copy, internal links, and product membership specific to each user intent; continue monitoring as the information architecture changes.",
                "priority": "low",
            },
        })

    return findings


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Target URL to audit")
    parser.add_argument("--cache-dir", required=True, help="Cache directory")
    args = parser.parse_args()
    print(json.dumps(run_audit(args.url, cache_dir=args.cache_dir), ensure_ascii=False))
