import argparse
import json
import os
import re
import urllib.robotparser
from pathlib import Path

import requests
from bs4 import BeautifulSoup

AI_BOTS = [
    "GPTBot", "ClaudeBot", "PerplexityBot", "Google-Extended", "OAI-SearchBot",
    "ChatGPT-User", "CCBot", "Bytespider", "Amazonbot", "meta-externalagent",
]
HIGH_PRIORITY_BOTS = {"OAI-SearchBot", "ChatGPT-User", "PerplexityBot", "ClaudeBot", "GPTBot"}

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

# Render-delta severity tiers: (min_ratio, severity)
RENDER_DELTA_TIERS = [(0.70, "high"), (0.30, "medium")]


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
    if any(marker in lower for marker in ("cf-chl-", "challenge-platform", "turnstile", "captcha")):
        score += 0.18
        signals.append("challenge_markup")

    return {
        "is_challenge": score >= 0.50,
        "score": round(min(score, 1.0), 3),
        "signals": sorted(set(signals)),
        "patterns": sorted(set(matched)),
        "text_length": len(text),
    }


def raw_render_metrics(raw_html: str, live_url: str | None, live_render: bool) -> dict:
    """Compare raw HTML text with browser-visible text.

    live_render=True (the default) navigates a real browser to live_url with
    page.goto(), so JS bundles fetch and execute exactly as they would for a
    real visitor. The cached_dom fallback (live_render=False) re-parses the
    already-fetched raw HTML through page.set_content() instead, which does
    NOT navigate to a real origin — externally-referenced scripts on relative
    paths (the overwhelming majority of real sites) will fail to load, so any
    client-side-rendered framework never mounts and the measured delta silently
    collapses toward zero. Treat cached_dom results as a weak lower bound only;
    use live_render whenever network access to the live site is available.
    """
    raw_text = extract_text(raw_html)
    metrics = {
        "raw_text_length": len(raw_text),
        "raw_word_count": len(raw_text.split()),
        "render_available": False,
        "render_mode": "none",
        "rendered_text_length": None,
        "rendered_word_count": None,
        "render_delta_ratio": None,
        "render_caveat": None,
        "render_error": None,
    }

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        metrics["render_error"] = "Playwright not installed; raw HTML analysis used."
        return metrics

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ))
            page = context.new_page()

            if live_render and live_url:
                response = page.goto(live_url, wait_until="networkidle", timeout=20000)
                if response is not None and response.status in (403, 429, 503):
                    raise RuntimeError(f"Browser render returned HTTP {response.status}")
                page.wait_for_timeout(1000)
                metrics["render_mode"] = "live_browser"
            else:
                page.set_content(raw_html, wait_until="domcontentloaded", timeout=15000)
                metrics["render_mode"] = "cached_dom"
                metrics["render_caveat"] = (
                    "cached_dom mode cannot execute relative-path scripts; a near-zero delta here does "
                    "NOT confirm the live site is render-safe — rerun with live_render enabled to confirm."
                )

            rendered_text = page.locator("body").inner_text(timeout=5000)
            context.close()
            browser.close()

        metrics["render_available"] = True
        metrics["rendered_text_length"] = len(rendered_text)
        metrics["rendered_word_count"] = len(rendered_text.split())
        raw_words = set(raw_text.split())
        rendered_words = set(rendered_text.split())
        metrics["render_delta_ratio"] = round(
            len(rendered_words - raw_words) / max(1, len(rendered_words)), 3
        )
    except Exception as exc:
        metrics["render_error"] = str(exc)

    return metrics


def load_manifest(cache_dir: str) -> dict:
    index_path = Path(cache_dir) / "cache_index.json"
    if not index_path.exists():
        return {}
    try:
        return json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def robots_findings(target_url: str, cache_dir: str) -> tuple[list[str], dict]:
    robots_path = Path(cache_dir) / "robots.txt"
    blocked = []
    meta = {"robots_checked": False, "blocked_bots": []}
    if not robots_path.exists():
        return blocked, meta

    try:
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(robots_path.read_text(encoding="utf-8").splitlines())
        meta["robots_checked"] = True
        for bot in AI_BOTS:
            if not parser.can_fetch(bot, target_url):
                blocked.append(bot)
    except Exception:
        pass
    meta["blocked_bots"] = blocked
    return blocked, meta


def robots_allows_fetch(url: str, cache_dir: str, user_agent: str = "*") -> bool:
    """Guardrail: 'respect robots.txt' applies to every live request this skill
    makes, including the live-render re-fetch below — not just the upstream
    crawl. Playwright presents a generic desktop-Chrome UA (not a named AI bot),
    so this checks the wildcard/default rule set for that UA. Fails open (True)
    if robots.txt is missing or unparseable, matching robots.txt semantics.
    """
    robots_path = Path(cache_dir) / "robots.txt"
    if not robots_path.exists():
        return True
    try:
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(robots_path.read_text(encoding="utf-8").splitlines())
        return parser.can_fetch(user_agent, url)
    except Exception:
        return True


def noindex_findings(pages: list) -> list:
    """Meta-robots noindex / X-Robots-Tag checks — a separate gate from robots.txt.

    robots.txt controls whether a crawler fetches a page at all; noindex controls
    whether a page that WAS fetched gets kept/cited. Both block visibility, so both
    need their own finding rather than being folded into the robots.txt check.
    """
    noindexed = []
    for page in pages:
        path = page.get("file")
        page_url = page.get("final_url") or page.get("url", "unknown")
        header_tag = (page.get("headers") or {}).get("X-Robots-Tag", "")
        if header_tag and "noindex" in header_tag.lower():
            noindexed.append(page_url)
            continue
        if path and os.path.exists(path):
            try:
                html = Path(path).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            soup = BeautifulSoup(html, "html.parser")
            tag = soup.find("meta", attrs={"name": re.compile("robots", re.I)})
            if tag and "noindex" in (tag.get("content") or "").lower():
                noindexed.append(page_url)

    if not noindexed:
        return []
    return [{
        "id": "DISC-NOINDEX-001",
        "title": "Pages Blocked From Indexing via noindex",
        "severity": "high" if len(noindexed) > 1 else "medium",
        "evidence": (
            f"{len(noindexed)} normal-content page(s) carry a noindex meta tag or X-Robots-Tag header, "
            f"which keeps them out of AI/search indexes even though robots.txt allows crawling. "
            f"Examples: {', '.join(noindexed[:5])}."
        ),
        "suggested_action": {
            "summary": "Remove noindex from any page meant to be publicly discoverable — this is often left over from a staging config and silently hides otherwise-crawlable content.",
            "priority": "high" if len(noindexed) > 1 else "medium",
        },
    }]


def audit_crawl_and_render(
    target_url: str,
    cache_dir: str = "audit-cache",
    live_render: bool = True,
) -> list:
    findings = []
    manifest = load_manifest(cache_dir)
    pages = manifest.get("pages", [])

    if not pages:
        findings.append({
            "id": "CRAWL-001",
            "title": "No Crawl Evidence Available",
            "severity": "high",
            "evidence": "The crawl cache contains no page records, so page-level AI readability could not be evaluated.",
            "suggested_action": {
                "summary": "Verify that the crawler completed and produced cache_index.json plus page artifacts before running semantic audits.",
                "priority": "high",
            },
        })
        return findings

    challenge_pages = [p for p in pages if p.get("content_classification") == "bot_challenge"]
    normal_pages = [p for p in pages if p.get("content_classification") == "normal"]
    blocked_pages = [p for p in pages if p.get("content_classification") in {"blocked", "auth_wall"}]

    if challenge_pages:
        sample_urls = [p.get("url") for p in challenge_pages[:5]]
        findings.append({
            "id": "CRAWL-CHALLENGE-001",
            "title": "Bot-Challenge Pages Detected",
            "severity": "high" if len(challenge_pages) >= max(2, len(pages) // 2) else "medium",
            "evidence": (
                f"{len(challenge_pages)}/{len(pages)} sampled URLs returned content classified as "
                f"bot-challenge/interstitial responses. Challenge URLs include: {', '.join(sample_urls)}."
            ),
            "suggested_action": {
                "summary": "Ensure important public content is retrievable by legitimate automated clients rather than replacing page content with challenge/interstitial responses.",
                "priority": "high",
            },
        })

    if not normal_pages:
        findings.append({
            "id": "CRAWL-EVIDENCE-001",
            "title": "Insufficient Normal-Page Evidence",
            "severity": "high",
            "evidence": "0 normal HTML pages were available for semantic auditing; findings about titles, schema, breadcrumbs, or page content would be unreliable.",
            "suggested_action": {
                "summary": "Restore crawlable access to representative public pages or provide an accessible structured surface such as sitemaps/feeds for the affected content.",
                "priority": "high",
            },
        })

    if blocked_pages:
        findings.append({
            "id": "CRAWL-BLOCKED-002",
            "title": "Access-Restricted Pages Detected",
            "severity": "medium",
            "evidence": f"{len(blocked_pages)}/{len(pages)} sampled URLs returned access/auth restrictions instead of normal content.",
            "suggested_action": {
                "summary": "Keep important public information outside authentication or access barriers when it is intended to be discoverable by AI systems.",
                "priority": "medium",
            },
        })

    # Raw-vs-rendered evidence for a small normal-page sample.
    render_samples = []
    for record in normal_pages[: min(4, len(normal_pages))]:
        path = record.get("file")
        if not path or not os.path.exists(path):
            continue
        try:
            raw_html = Path(path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        page_url = record.get("final_url") or record.get("url")

        # Guardrail: never let the live-render re-fetch bypass robots.txt, even
        # though the upstream crawl cache already passed this URL as "normal".
        effective_live_render = live_render
        robots_block_note = None
        if live_render and page_url and not robots_allows_fetch(page_url, cache_dir):
            effective_live_render = False
            robots_block_note = (
                "robots.txt disallows fetching this URL; skipped the live re-render and "
                "fell back to cached_dom (delta may be understated for this page)."
            )

        metrics = raw_render_metrics(
            raw_html,
            live_url=page_url,
            live_render=effective_live_render,
        )
        if robots_block_note:
            metrics["render_caveat"] = robots_block_note
        metrics["_url"] = page_url
        render_samples.append(metrics)

    rendered = [m for m in render_samples if m.get("render_available")]
    if rendered:
        worst_tier = None
        tiered = []
        for m in rendered:
            ratio = m.get("render_delta_ratio") or 0
            for threshold, severity in RENDER_DELTA_TIERS:
                if ratio >= threshold:
                    tiered.append((m, severity, ratio))
                    if worst_tier is None or RENDER_DELTA_TIERS.index((threshold, severity)) < worst_tier:
                        worst_tier = RENDER_DELTA_TIERS.index((threshold, severity))
                    break
        if tiered:
            severity = tiered[0][1]  # tiers are checked high->low, first match is worst present
            for m, sev, ratio in tiered:
                if sev == "high":
                    severity = "high"
                    break
            avg_delta = round(sum(r for _, _, r in tiered) / len(tiered), 3)
            examples = ", ".join(f"{m['_url']} (+{r:.0%})" for m, _, r in tiered[:5])
            caveat = next((m["render_caveat"] for m, _, _ in tiered if m.get("render_caveat")), None)
            evidence = (
                f"{len(tiered)}/{len(rendered)} rendered sample pages added at least "
                f"{RENDER_DELTA_TIERS[-1][0]:.0%} new visible words after JS execution "
                f"(average delta among affected pages: {avg_delta:.0%}). Examples: {examples}."
            )
            if caveat:
                evidence += f" NOTE: {caveat}"
            findings.append({
                "id": "CRAWL-RENDER-001",
                "title": "Large Raw-to-Rendered Content Difference",
                "severity": severity,
                "evidence": evidence,
                "suggested_action": {
                    "summary": "Ensure important facts are present in crawlable HTML or a stable structured representation (e.g. SSR/SSG), not only in client-side post-load content.",
                    "priority": severity,
                },
            })
    elif render_samples and all(m.get("render_error") for m in render_samples):
        findings.append({
            "id": "CRAWL-RENDER-ERR-000",
            "title": "Render Comparison Unavailable",
            "severity": "info",
            "evidence": f"Raw-vs-rendered comparison could not run: {render_samples[0]['render_error']}",
            "suggested_action": None,
        })

    findings.extend(noindex_findings(normal_pages))

    blocked_bots, robot_meta = robots_findings(target_url, cache_dir)
    if blocked_bots:
        high_priority_hit = any(bot in blocked_bots for bot in HIGH_PRIORITY_BOTS)
        findings.append({
            "id": "DISC-ROBOTS-001",
            "title": "AI Crawler User-Agents Blocked by robots.txt",
            "severity": "high" if high_priority_hit else "medium",
            "evidence": f"robots.txt disallows these AI/search user-agents for the audited target: {', '.join(blocked_bots)}.",
            "suggested_action": {
                "summary": "Review robots.txt rules for AI/search crawlers and explicitly allow the agents you intend to use for discovery, subject to your site's policy.",
                "priority": "high" if high_priority_hit else "medium",
            },
        })
    elif robot_meta["robots_checked"]:
        findings.append({
            "id": "DISC-PASS-000",
            "title": "AI Crawling Not Blocked by robots.txt",
            "severity": "info",
            "evidence": "Checked cached robots.txt against standard AI/search user-agents; no explicit disallow was found for the audited target URL.",
            "suggested_action": None,
        })

    return findings


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--cache-dir", default="audit-cache")
    parser.add_argument(
        "--no-live-render",
        dest="live_render",
        action="store_false",
        help="Skip live browser navigation and fall back to cached_dom parsing only "
             "(faster / fully offline, but understates render gaps — see raw_render_metrics docstring).",
    )
    parser.set_defaults(live_render=True)
    args = parser.parse_args()
    print(json.dumps(
        audit_crawl_and_render(
            args.url,
            cache_dir=args.cache_dir,
            live_render=args.live_render,
        ),
        indent=2,
    ))