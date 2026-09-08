import argparse
import json
import os
import re
from collections import defaultdict
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path

from bs4 import BeautifulSoup

# page_type value (as emitted in cache_index.json) used for category/collection
# pages. Adjust this if your crawler labels them differently (e.g. "collection").
CATEGORY_PAGE_TYPE = "category"

# Heuristic used to recognize an internal link as a product link, so it can be
# excluded from "descriptive copy" and counted for cross-listing instead.
PRODUCT_LINK_PATTERN = re.compile(r"/(products?|p)/", re.I)

THIN_COPY_WORD_THRESHOLD = 40
HIGH_SIMILARITY_THRESHOLD = 0.60
CROSS_LIST_MIN_CATEGORIES = 3


def load_manifest(cache_dir: str) -> dict:
    index_path = Path(cache_dir) / "cache_index.json"
    if not index_path.exists():
        return {}
    try:
        return json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def extract_category_signals(html: str) -> dict:
    """Split a category page into descriptive copy vs. product-grid links."""
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        node.decompose()

    product_links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if PRODUCT_LINK_PATTERN.search(href):
            product_links.add(href.split("?")[0].rstrip("/"))
            a.decompose()  # remove product-card anchor text from descriptive copy

    desc_text = " ".join(soup.stripped_strings)
    return {
        "desc_text": desc_text,
        "word_count": len(desc_text.split()),
        "product_links": product_links,
    }


def run_audit(url: str, cache_dir: str = "audit-cache") -> list:
    manifest = load_manifest(cache_dir)
    pages = manifest.get("pages", [])
    category_pages = [
        p for p in pages
        if p.get("page_type") == CATEGORY_PAGE_TYPE and p.get("content_classification") == "normal"
    ]

    if len(category_pages) < 2:
        return [{
            "id": "CIA-INSUFFICIENT-000",
            "title": "Not Enough Category Pages to Audit Isolation",
            "severity": "info",
            "evidence": (
                f"Found {len(category_pages)} page(s) classified as '{CATEGORY_PAGE_TYPE}' in the crawl manifest; "
                f"at least 2 are needed to compare category boundaries."
            ),
            "suggested_action": None,
        }]

    cat_data = []
    for page in category_pages:
        path = page.get("file")
        page_url = page.get("final_url") or page.get("url", "unknown")
        if not path or not os.path.exists(path):
            continue
        try:
            html = Path(path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        signals = extract_category_signals(html)
        signals["url"] = page_url
        cat_data.append(signals)

    findings = []

    # 1. Thin/absent category copy — no classification signal for a crawler.
    thin_pages = [c for c in cat_data if c["word_count"] < THIN_COPY_WORD_THRESHOLD]
    if thin_pages:
        examples = ", ".join(f"{c['url']} ({c['word_count']} words)" for c in thin_pages[:5])
        findings.append({
            "id": "CIA-THIN-001",
            "title": "Category Pages With No Descriptive Copy",
            "severity": "medium",
            "evidence": (
                f"{len(thin_pages)}/{len(cat_data)} category pages have under {THIN_COPY_WORD_THRESHOLD} words of "
                f"text outside the product grid/nav/footer. Examples: {examples}."
            ),
            "suggested_action": {
                "summary": "Add a short intro paragraph to each category page stating what it includes (and, ideally, what it excludes) so both readers and AI systems can classify it.",
                "priority": "medium",
            },
        })

    # 2. Near-duplicate descriptive copy between sibling categories.
    comparable = [c for c in cat_data if c["word_count"] >= THIN_COPY_WORD_THRESHOLD]
    high_sim_pairs = []
    for a, b in combinations(comparable, 2):
        ratio = SequenceMatcher(None, a["desc_text"], b["desc_text"]).ratio()
        if ratio >= HIGH_SIMILARITY_THRESHOLD:
            high_sim_pairs.append((a["url"], b["url"], round(ratio, 2)))
    if high_sim_pairs:
        examples = "; ".join(f"{u1} vs {u2} ({r:.0%} similar text)" for u1, u2, r in high_sim_pairs[:5])
        findings.append({
            "id": "CIA-OVERLAP-002",
            "title": "Category Pages Use Near-Duplicate Descriptive Copy",
            "severity": "medium",
            "evidence": f"{len(high_sim_pairs)} category-page pair(s) exceed {HIGH_SIMILARITY_THRESHOLD:.0%} text similarity: {examples}.",
            "suggested_action": {
                "summary": "Write unique, category-specific copy for each collection — templated boilerplate gives an AI classifier no signal to tell categories apart.",
                "priority": "medium",
            },
        })

    # 3. Products cross-listed across many categories with no apparent differentiation.
    product_to_cats = defaultdict(set)
    for c in cat_data:
        for link in c["product_links"]:
            product_to_cats[link].add(c["url"])
    cross_listed = {p: cats for p, cats in product_to_cats.items() if len(cats) >= CROSS_LIST_MIN_CATEGORIES}
    if cross_listed:
        sample_product, sample_cats = next(iter(cross_listed.items()))
        findings.append({
            "id": "CIA-CROSSLIST-003",
            "title": "Products Cross-Listed Across Many Categories",
            "severity": "medium" if len(cross_listed) < len(product_to_cats) // 2 + 1 else "high",
            "evidence": (
                f"{len(cross_listed)}/{len(product_to_cats)} products found in category grids appear in "
                f"{CROSS_LIST_MIN_CATEGORIES}+ different categories. Example: '{sample_product}' appears in "
                f"{len(sample_cats)} categories: {sorted(sample_cats)}."
            ),
            "suggested_action": {
                "summary": "Either narrow category membership to genuinely distinct sets of products, or add category-specific framing/copy explaining why a shared product belongs in each one.",
                "priority": "medium",
            },
        })

    if not findings:
        findings.append({
            "id": "CIA-PASS-000",
            "title": "Category Boundaries Appear Distinct",
            "severity": "info",
            "evidence": (
                f"Checked {len(cat_data)} category pages: no thin-copy, near-duplicate, or heavy cross-listing "
                f"signals found above configured thresholds."
            ),
            "suggested_action": None,
        })

    return findings


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Target URL to audit")
    parser.add_argument("--cache-dir", required=True, help="Cache directory")
    args = parser.parse_args()

    # Scripts must print JSON array to stdout for the Orchestrator to consume
    print(json.dumps(run_audit(args.url, cache_dir=args.cache_dir)))