"""
Single source of truth for "is this cached page actually usable evidence."

Why this exists: three different pieces of code (crawler.py at fetch time,
check_render.py during its own audit, and — before this module — nobody
upfront) were each making an independent judgment call about whether a page
was real content or a bot-challenge/interstitial, using slightly different
logic, at different times, with no guarantee about ordering. The result on a
real run against a bot-defended site: crawl_summary reported 12/12 pages
usable while a concurrently-running check's own evidence said 11/12 were
blocked — two contradictory claims in the same report, and every
page-content-dependent check in between ran against 11 copies of a generic
"blocked" page as if they were normal site content.

The fix is not a better heuristic — the heuristics here were already good.
It's making the classification happen exactly ONCE, synchronously, before
anything else runs, so every consumer (the suppression decision, the final
crawl_summary, every dispatched skill) sees the same already-corrected
picture instead of racing to fix it independently and inconsistently.
"""

import base64
import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from bs4 import BeautifulSoup

CHALLENGE_CLASSIFICATIONS = {
    "bot_challenge",
    "challenge",
    "blocked",
    "auth_wall",
    "unexpected_http",
}

# A blocked/interstitial URL should not be trusted even when the response
# came back HTTP 200 with no obvious challenge text in the body — this is
# exactly what Walmart's /blocked?url=... redirect does, and is why
# content-only challenge detection missed it entirely.
BLOCKED_URL_PATTERNS = (
    "/blocked",
    "/challenge",
    "/captcha",
    "/access-denied",
    "/access_denied",
    "/security-check",
    "/verify",
    "/px/captcha",
)

INTERSTITIAL_MARKERS = (
    "access denied",
    "request blocked",
    "verify you are human",
    "checking your browser",
    "enable javascript and cookies",
    "unusual traffic",
    "robot or human",
    "security verification",
    "captcha",
    "pardon our interruption",
    "automated bot activity",
    "perimeterx",
    "px-captcha",
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
    re.compile(r"pardon\s+our\s+interruption", re.I),
    re.compile(r"automated\s+bot\s+activity", re.I),
    re.compile(r"blocked\s+by\s+perimeterx", re.I),
    re.compile(r"px-captcha", re.I),
]


def normalize_url(url):
    return (url or "").strip().lower()


def looks_like_blocked_url(url: str) -> bool:
    normalized = normalize_url(url)
    return any(pattern in normalized for pattern in BLOCKED_URL_PATTERNS)


def extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "noscript", "template"]):
        node.decompose()
    return " ".join(soup.stripped_strings)


def detect_challenge(html: str, status_code: int) -> dict:
    text = extract_text(html)
    title = BeautifulSoup(html, "html.parser").title
    title_text = title.get_text(" ", strip=True) if title else ""
    combined = f"{title_text} {text[:5000]}"
    matched = [p.pattern for p in CHALLENGE_PATTERNS if p.search(combined)]

    score = 0.0
    signals = []
    if status_code in (403, 429):
        score += 0.35
        signals.append(f"http_{status_code}")
    if matched:
        score += min(0.55, 0.18 * len(matched))
        signals.append("challenge_text")
    if len(text) < 350:
        score += 0.08
        signals.append("very_short_document")
    lower = html.lower()
    if any(marker in lower for marker in ("cf-chl-", "challenge-platform", "turnstile", "captcha", "perimeterx")):
        score += 0.18
        signals.append("challenge_markup")

    return {
        "is_challenge": score >= 0.50,
        "score": round(min(score, 1.0), 3),
        "signals": sorted(set(signals)),
        "patterns": sorted(set(matched)),
        "text_length": len(text),
    }


def classify_page_evidence(record: dict, html: str) -> dict:
    """Return a conservative evidence classification for one cached page.

    Never upgrades a page the crawler already flagged as bad — only ever
    narrows "normal" further, so this can only make classification stricter,
    never looser.
    """
    url = (record.get("final_url") or record.get("url") or "").strip()
    status = record.get("status_code") or record.get("status") or 200
    try:
        status = int(status)
    except (TypeError, ValueError):
        status = 200

    lowered_url = url.lower()
    url_hits = [p for p in BLOCKED_URL_PATTERNS if p in lowered_url]

    challenge = detect_challenge(html, status)
    text = extract_text(html)
    lowered_text = text[:12000].lower()
    marker_hits = [m for m in INTERSTITIAL_MARKERS if m in lowered_text]

    if url_hits:
        return {
            "usable": False,
            "classification": "bot_challenge",
            "reason": "blocked_or_challenge_url",
            "signals": url_hits,
            "challenge_score": challenge["score"],
        }

    if challenge["is_challenge"]:
        return {
            "usable": False,
            "classification": "bot_challenge",
            "reason": "challenge_response",
            "signals": challenge["signals"] + marker_hits[:5],
            "challenge_score": challenge["score"],
        }

    if marker_hits and len(text) < 1200:
        return {
            "usable": False,
            "classification": "bot_challenge",
            "reason": "short_interstitial",
            "signals": marker_hits,
            "challenge_score": max(challenge["score"], 0.50),
        }

    declared = record.get("content_classification")
    if declared in {"blocked", "auth_wall", "bot_challenge"}:
        return {
            "usable": False,
            "classification": declared,
            "reason": "crawler_classification",
            "signals": [declared],
            "challenge_score": challenge["score"],
        }

    return {
        "usable": True,
        "classification": "normal",
        "reason": "normal_page_evidence",
        "signals": [],
        "challenge_score": challenge["score"],
    }


def decode_blocked_url_target(url: str) -> str | None:
    """
    Many bot-defense systems (Walmart's among them) redirect a blocked
    request to an interstitial URL that still carries the originally
    requested path, base64-encoded in a query parameter. Recovering it costs
    nothing and gives a human reader *some* signal about what the crawler was
    trying to reach — this is NOT confirmation of what that page actually
    contains, since content was never retrieved. Callers must present it as
    "intended path only," never as verified evidence.
    """
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        candidates = params.get("url") or params.get("dest") or params.get("target")
        if not candidates:
            return None
        raw = candidates[0]
        padded = raw + "=" * (-len(raw) % 4)
        decoded = base64.b64decode(padded, validate=False).decode("utf-8", errors="strict")
        if not decoded or not decoded.isprintable():
            return None
        if not (decoded.startswith("/") or decoded.startswith("http")):
            return None
        return decoded
    except Exception:
        return None


def summarize_blocked_url_targets(urls, limit: int = 6) -> list[str]:
    """Readable, deduplicated intended-destination paths recovered from
    blocked-redirect URLs, falling back to a truncated original URL when
    nothing could be decoded. Also replaces unreadable base64 blobs in
    evidence text with something a non-expert can actually read."""
    seen = set()
    out = []
    for url in urls:
        decoded = decode_blocked_url_target(url)
        display = decoded if decoded else (url[:80] + "..." if len(url) > 80 else url)
        if display in seen:
            continue
        seen.add(display)
        out.append(display)
        if len(out) >= limit:
            break
    return out


def apply_evidence_gate(manifest: dict) -> dict:
    """
    Run the ONE authoritative usability pass over every cached page, before
    any check is scheduled. Mutates and returns `manifest` with `meta` counts
    recomputed from the corrected classifications, so the suppression
    decision, the final crawl_summary, and every dispatched skill all see the
    same single, already-corrected picture — no ordering dependency, no race.
    """
    pages = manifest.get("pages", [])
    corrected_count = 0
    corrected_urls = []

    for page in pages:
        path = page.get("file")
        html = ""
        if path and Path(path).exists():
            try:
                html = Path(path).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                html = ""

        evidence = classify_page_evidence(page, html)
        page["_evidence_gate"] = evidence

        was_normal = page.get("content_classification") == "normal" and page.get("success")
        if not evidence["usable"]:
            if was_normal:
                corrected_count += 1
                corrected_urls.append(page.get("final_url") or page.get("url", "unknown"))
            page["content_classification"] = evidence["classification"]
            page["success"] = False

    counts: dict = {}
    for page in pages:
        kind = page.get("content_classification", "unknown")
        counts[kind] = counts.get(kind, 0) + 1

    usable_pages = [p for p in pages if p.get("content_classification") == "normal"]
    challenge_pages = [p for p in pages if p.get("content_classification") == "bot_challenge"]

    meta = manifest.setdefault("meta", {})
    meta["pages_usable"] = len(usable_pages)
    meta["pages_challenge"] = len(challenge_pages)
    meta["pages_by_classification"] = counts
    meta["usable_page_ratio"] = round(len(usable_pages) / max(1, len(pages)), 3)
    meta["challenge_ratio"] = round(len(challenge_pages) / max(1, len(pages)), 3)
    meta["evidence_gate_corrections"] = corrected_count
    meta["evidence_gate_corrected_urls"] = corrected_urls[:10]

    return manifest