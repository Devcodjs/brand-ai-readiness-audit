"""URL-level page-type discovery helpers for the site crawler.

This module classifies URLs using path/query hints only.

IMPORTANT:
A URL matching a pattern is DISCOVERY evidence only.
It does not prove that the URL successfully resolves to that page type.
"""

from urllib.parse import urlparse


# Order matters: the first matching type wins.
PAGE_TYPE_HINTS = (
    (
        "product",
        (
            "/ip/",
            "/product/",
            "/products/",
            "/dp/",
            "/p/",
        ),
    ),
    (
        "location",
        (
            "/store/",
            "/stores/",
            "/locations/",
            "/location/",
        ),
    ),
    (
        "category",
        (
            "/category/",
            "/categories/",
            "/cp/",
            "/collections/",
            "/shop/",
        ),
    ),
    (
        "search",
        (
            "/search",
            "/s?",
            "/browse?",
        ),
    ),
    (
        "article",
        (
            "/article/",
            "/blog/",
            "/news/",
            "/stories/",
            "/help/",
            "/learn/",
        ),
    ),
)

PAGE_TYPES = (
    "home",
    "product",
    "category",
    "location",
    "article",
    "search",
    "other",
)


def classify_url(url: str) -> str:
    """Classify a URL from its path/query structure only."""
    parsed = urlparse(url)

    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query

    path = path.lower()

    if path in ("", "/"):
        return "home"

    for page_type, hints in PAGE_TYPE_HINTS:
        if any(hint in path for hint in hints):
            return page_type

    return "other"


def summarize_candidate_urls(
    urls: list[str],
    max_examples: int = 25,
) -> dict:
    """Summarize page-type signals across the complete candidate URL set.

    Returns:
        total:
            Number of candidate URLs examined.

        counts:
            Number of candidate URLs whose structure suggests each page type.

        examples:
            Up to `max_examples` example URLs for each detected type.

    This is DISCOVERY evidence only. It does not mean the URLs were fetched
    successfully or that their returned HTML matched the inferred type.
    """
    counts = {page_type: 0 for page_type in PAGE_TYPES}
    examples = {page_type: [] for page_type in PAGE_TYPES}

    for url in urls:
        page_type = classify_url(url)

        counts[page_type] += 1

        if len(examples[page_type]) < max_examples:
            examples[page_type].append(url)

    examples = {
        page_type: urls_for_type
        for page_type, urls_for_type in examples.items()
        if urls_for_type
    }

    return {
        "total": len(urls),
        "counts": counts,
        "examples": examples,
    }