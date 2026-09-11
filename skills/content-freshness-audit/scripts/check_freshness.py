import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup


CURRENT_YEAR = 2026

# Page types where freshness is especially meaningful.
FRESHNESS_SCHEMA_TYPES = {
    "Article",
    "NewsArticle",
    "BlogPosting",
    "WebPage",
    "Product",
    "Offer",
    "Event",
    "CollectionPage",
    "ItemList",
}

# Pages that should never become semantic evidence.
CHALLENGE_CLASSIFICATIONS = {
    "bot_challenge",
    "challenge",
    "blocked",
    "auth_wall",
    "unexpected_http",
}

# A blocked/interstitial URL should not be trusted even if an upstream
# crawler accidentally labelled it "normal".
BLOCKED_URL_PATTERNS = (
    "/blocked",
    "/challenge",
    "/captcha",
    "/access-denied",
    "/access_denied",
    "/security-check",
    "/verify",
)

CHALLENGE_PATTERNS = [
    re.compile(r"verify\s+you\s+are\s+human", re.I),
    re.compile(r"checking\s+your\s+browser", re.I),
    re.compile(r"just\s+a\s+moment", re.I),
    re.compile(r"security\s+check", re.I),
    re.compile(r"robot\s+or\s+human", re.I),
    re.compile(r"unusual\s+traffic", re.I),
    re.compile(r"access\s+denied", re.I),
    re.compile(r"captcha", re.I),
    re.compile(r"enable\s+javascript\s+and\s+cookies", re.I),
    re.compile(r"ray\s+id", re.I),
]

COPYRIGHT_YEAR_PATTERN = re.compile(
    r"(?:©|&copy;|Copyright)\s*(?:[A-Za-z\s0-9,&.-]*\s*)?(20\d{2})",
    re.IGNORECASE,
)


def normalize_url(url):
    return (url or "").strip().lower()


def looks_like_blocked_url(url):
    normalized = normalize_url(url)
    return any(pattern in normalized for pattern in BLOCKED_URL_PATTERNS)


def extract_visible_text(soup):
    clone = BeautifulSoup(str(soup), "html.parser")
    for node in clone(["script", "style", "noscript", "template", "svg"]):
        node.decompose()
    return " ".join(clone.stripped_strings)


def detect_challenge(html):
    soup = BeautifulSoup(html, "html.parser")
    text = extract_visible_text(soup)
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    combined = f"{title} {text[:5000]}"

    matched = [p.pattern for p in CHALLENGE_PATTERNS if p.search(combined)]

    score = 0.0
    signals = []

    if matched:
        score += min(0.65, 0.18 * len(matched))
        signals.append("challenge_text")

    if len(text) < 350:
        score += 0.08
        signals.append("very_short_document")

    lower = html.lower()
    if any(
        marker in lower
        for marker in (
            "cf-chl-",
            "challenge-platform",
            "turnstile",
            "captcha",
        )
    ):
        score += 0.18
        signals.append("challenge_markup")

    return {
        "is_challenge": score >= 0.50,
        "score": round(min(score, 1.0), 3),
        "signals": sorted(set(signals)),
        "patterns": sorted(set(matched)),
    }


def page_is_usable(page, html):
    """
    Independent evidence gate.

    We do not trust content_classification alone because upstream crawlers
    can accidentally classify a block/interstitial as normal.
    """
    url = page.get("final_url") or page.get("url", "")
    classification = str(page.get("content_classification") or "").lower()

    if classification in CHALLENGE_CLASSIFICATIONS:
        return False, "blocked classification"

    if looks_like_blocked_url(url):
        return False, "blocked/interstitial URL"

    challenge = detect_challenge(html)
    if challenge["is_challenge"]:
        return False, f"challenge evidence score={challenge['score']}"

    if not page.get("success", True):
        return False, "crawl record marked unsuccessful"

    return True, None


def load_cached_pages(cache_dir):
    """
    Load only independently verified normal HTML pages.

    Returns:
      {
        "pages": [...],
        "requested": int,
        "usable": int,
        "challenge": int,
        "rejected": int,
        "rejection_reasons": Counter(...)
      }
    """
    index_path = Path(cache_dir) / "cache_index.json"

    result = {
        "pages": [],
        "requested": 0,
        "usable": 0,
        "challenge": 0,
        "rejected": 0,
        "rejection_reasons": Counter(),
    }

    if not index_path.exists():
        return result

    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return result

    records = index.get("pages", [])
    result["requested"] = len(records)

    for page in records:
        path = page.get("file")
        if not path or not Path(path).exists():
            result["rejected"] += 1
            result["rejection_reasons"]["missing cached HTML"] += 1
            continue

        try:
            html = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            result["rejected"] += 1
            result["rejection_reasons"]["unreadable cached HTML"] += 1
            continue

        usable, reason = page_is_usable(page, html)

        if not usable:
            result["challenge"] += 1
            result["rejected"] += 1
            result["rejection_reasons"][reason] += 1
            continue

        soup = BeautifulSoup(html, "html.parser")
        result["pages"].append(
            {
                "url": page.get("final_url") or page.get("url", "unknown"),
                "html": html,
                "soup": soup,
                "page_type": page.get("page_type", "other"),
                "content_classification": page.get("content_classification"),
            }
        )
        result["usable"] += 1

    return result


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


def schema_types(schema):
    raw = schema.get("@type", [])
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, list):
        return {x for x in raw if isinstance(x, str)}
    return set()


def parse_page_schemas(page):
    nodes = []
    malformed = 0

    for script in page["soup"].find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text()
        if not raw:
            continue

        try:
            nodes.extend(unpack_schemas(json.loads(raw)))
        except (json.JSONDecodeError, TypeError):
            malformed += 1

    return nodes, malformed


def check_schema_dates(pages):
    """
    Check structured freshness only on schema nodes where a freshness signal
    is meaningful. This avoids treating every arbitrary JSON-LD node as stale.
    """
    if not pages:
        return None

    pages_with_relevant_schema = 0
    pages_missing_freshness = []
    malformed_pages = []

    for page in pages:
        nodes, malformed = parse_page_schemas(page)

        if malformed:
            malformed_pages.append(page["url"])

        relevant_nodes = [
            node
            for node in nodes
            if isinstance(node, dict) and schema_types(node) & FRESHNESS_SCHEMA_TYPES
        ]

        if not relevant_nodes:
            continue

        pages_with_relevant_schema += 1

        has_freshness = any(
            bool(node.get("dateModified") or node.get("datePublished"))
            for node in relevant_nodes
        )

        if not has_freshness:
            pages_missing_freshness.append(page["url"])

    if (
        pages_with_relevant_schema >= 2
        and len(pages_missing_freshness) / pages_with_relevant_schema > 0.5
    ):
        return {
            "id": "FRESH-001",
            "title": "Missing Machine-Readable Freshness Signals",
            "severity": "medium",
            "evidence": (
                f"{len(pages_missing_freshness)}/{pages_with_relevant_schema} pages "
                f"with freshness-relevant structured data lack dateModified or "
                f"datePublished. Examples: "
                f"{', '.join(pages_missing_freshness[:3])}."
            ),
            "suggested_action": {
                "summary": (
                    "Add an accurate dateModified and, where appropriate, "
                    "datePublished property to the relevant WebPage, Article, "
                    "Product, or other freshness-sensitive schema node. Keep the "
                    "value synchronized with the actual content state."
                ),
                "priority": "medium",
            },
        }

    return None


def check_copyright_year(pages):
    """
    Treat the footer year as a weak, supporting signal rather than proof of
    content staleness. A copyright year alone should not drive a high-severity
    AI-readiness finding.
    """
    if not pages:
        return None

    stale_pages = []

    for page in pages:
        soup = page["soup"]

        footers = soup.find_all("footer")
        if not footers:
            footers = soup.find_all(
                attrs={"class": re.compile(r"footer", re.I)}
            )

        if not footers:
            continue

        years = []

        for footer in footers:
            text = footer.get_text(separator=" ")
            years.extend(
                int(value)
                for value in COPYRIGHT_YEAR_PATTERN.findall(text)
            )

        if years and max(years) < CURRENT_YEAR:
            stale_pages.append(page["url"])

    if not stale_pages:
        return None

    return {
        "id": "FRESH-002",
        "title": "Outdated Footer Copyright Year",
        "severity": "low",
        "evidence": (
            f"{len(stale_pages)}/{len(pages)} usable pages contain a footer "
            f"copyright year earlier than {CURRENT_YEAR}. Examples: "
            f"{', '.join(stale_pages[:3])}."
        ),
        "suggested_action": {
            "summary": (
                f"Review the site-wide copyright year and update it to "
                f"{CURRENT_YEAR} when appropriate. Treat this as a housekeeping "
                "signal rather than a substitute for actual content timestamps."
            ),
            "priority": "low",
        },
    }


def check_sitemap_lastmod(cache_dir):
    sitemap_path = Path(cache_dir) / "sitemap.xml"

    if not sitemap_path.exists():
        return None

    try:
        content = sitemap_path.read_text(encoding="utf-8", errors="replace")
        soup = BeautifulSoup(content, "xml")
        urls = soup.find_all("url")

        if not urls:
            return None

        missing_lastmod = sum(1 for url in urls if not url.find("lastmod"))

        if missing_lastmod / len(urls) > 0.5:
            return {
                "id": "FRESH-003",
                "title": "Missing Sitemap Modification Timestamps",
                "severity": "medium",
                "evidence": (
                    f"{missing_lastmod}/{len(urls)} sitemap URLs lack a "
                    f"<lastmod> tag."
                ),
                "suggested_action": {
                    "summary": (
                        "Configure the CMS or publishing pipeline to emit "
                        "accurate <lastmod> timestamps in the XML sitemap for "
                        "URLs whose content changes are tracked."
                    ),
                    "priority": "medium",
                },
            }

    except Exception as exc:
        sys.stderr.write(f"Error parsing sitemap: {exc}\n")

    return None


def make_evidence_finding(audit):
    if audit["requested"] == 0:
        return {
            "id": "FRESH-EVIDENCE-000",
            "title": "Freshness Audit Has No Crawl Evidence",
            "severity": "medium",
            "evidence": (
                "cache_index.json contains no page records, so freshness "
                "properties could not be evaluated."
            ),
            "suggested_action": {
                "summary": (
                    "Complete a crawl and make representative public HTML "
                    "pages available before evaluating freshness."
                ),
                "priority": "high",
            },
        }

    if audit["usable"] == 0:
        reasons = ", ".join(
            f"{reason} ({count})"
            for reason, count in audit["rejection_reasons"].most_common(4)
        )

        return {
            "id": "FRESH-EVIDENCE-001",
            "title": "Freshness Checks Suppressed Due to Insufficient Evidence",
            "severity": "medium",
            "evidence": (
                f"0/{audit['requested']} cached pages passed independent "
                f"normal-content verification. Freshness checks were suppressed. "
                f"Observed rejection reasons: {reasons or 'none recorded'}."
            ),
            "suggested_action": {
                "summary": (
                    "Restore access to representative normal HTML pages before "
                    "drawing conclusions about schema dates or visible freshness."
                ),
                "priority": "high",
            },
        }

    return None


def main():
    parser = argparse.ArgumentParser(
        description="Audit machine-readable and visible freshness signals."
    )
    parser.add_argument("--url", required=True)
    parser.add_argument("--cache-dir", required=True)
    args = parser.parse_args()

    audit = load_cached_pages(args.cache_dir)
    pages = audit["pages"]
    findings = []

    evidence_finding = make_evidence_finding(audit)
    if evidence_finding:
        findings.append(evidence_finding)

    # Only run page-level semantic freshness checks when usable evidence exists.
    if pages:
        schema_finding = check_schema_dates(pages)
        if schema_finding:
            findings.append(schema_finding)

        copyright_finding = check_copyright_year(pages)
        if copyright_finding:
            findings.append(copyright_finding)

    # Sitemap is an independent cache-level signal.
    sitemap_finding = check_sitemap_lastmod(args.cache_dir)
    if sitemap_finding:
        findings.append(sitemap_finding)

    if not findings:
        sitemap_state = (
            "a sitemap.xml was present and checked"
            if (Path(args.cache_dir) / "sitemap.xml").exists()
            else "no cached sitemap.xml was available for freshness analysis"
        )

        findings.append(
            {
                "id": "FRESH-PASS-000",
                "title": "No Freshness Defect Detected in Available Evidence",
                "severity": "info",
                "evidence": (
                    f"Verified {len(pages)} independently usable page(s); "
                    f"{sitemap_state}. No freshness defect crossed the configured "
                    "reporting thresholds."
                ),
                "suggested_action": {
                    "summary": (
                        "Continue maintaining accurate dateModified/datePublished "
                        "values and synchronized publishing metadata as content changes."
                    ),
                    "priority": "low",
                },
            }
        )

    # Include compact execution metadata without changing the required finding shape.
    output = {
        "url": args.url,
        "audit_confidence": {
            "execution": "HIGH",
            "evidence": (
                "HIGH"
                if audit["usable"] >= 3
                else "MEDIUM"
                if audit["usable"] > 0
                else "LOW"
            ),
        },
        "crawl_evidence": {
            "pages_requested": audit["requested"],
            "pages_usable": audit["usable"],
            "pages_rejected": audit["rejected"],
            "pages_challenge": audit["challenge"],
            "rejection_reasons": dict(audit["rejection_reasons"]),
        },
        "findings": findings,
    }

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
