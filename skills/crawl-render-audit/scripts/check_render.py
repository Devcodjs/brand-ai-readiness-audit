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

# --- Heuristic Static Render Signals --------------------------------------
SPA_ROOT_IDS = [
    "root", "app", "__next", "___gatsby", "___next", "app-root", "__nuxt", "__layout", "svelte"
]

NOSCRIPT_JS_REQUIRED = re.compile(
    r"(enable|turn on)\s+javascript|javascript\s+is\s+(required|disabled)",
    re.I,
)

BUNDLER_SRC_PATTERN = re.compile(
    r"(_next/static|/static/js/|webpack|chunk[-.][0-9a-f]{4,}|vendor[-.][0-9a-f]{4,}"
    r"|main\.[0-9a-f]{4,}\.js|/assets/index-[0-9a-f]{6,}\.js)",
    re.I,
)

SPA_LOADING_TEXT = re.compile(r"^(loading|please wait|one moment|initializing)[.\s]*$", re.I)

FRAMEWORK_FINGERPRINTS = {
    "react": re.compile(r"data-reactroot|react-dom", re.I),
    "vue": re.compile(r"__VUE__|data-v-[0-9a-f]{6,}", re.I),
    "angular": re.compile(r"ng-version=", re.I),
    "nuxt": re.compile(r"__NUXT__", re.I),
    "next": re.compile(r"__NEXT_DATA__", re.I),
}


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


def heuristic_static_render_estimate(raw_html: str) -> dict:
    soup = BeautifulSoup(raw_html, "html.parser")
    doc_text = extract_text(raw_html)
    doc_text_len = len(doc_text)

    signals = []
    details = {}
    score = 0.0

    # 1. Mount point check (loading placeholder or empty shell)
    loading_root = None
    thin_root = None

    for root_id in SPA_ROOT_IDS:
        el = soup.find(attrs={"id": re.compile(rf"^{re.escape(root_id)}$", re.I)})
        if el is None:
            continue

        for node in el.find_all(["script", "style", "noscript", "template"]):
            node.decompose()
        root_text = " ".join(el.stripped_strings).strip()
        root_text_len = len(root_text)

        if SPA_LOADING_TEXT.match(root_text):
            loading_root = (root_id, root_text)
            break

        if root_text_len < 80:
            thin_root = (root_id, root_text_len)
            break

    if loading_root:
        root_id, placeholder = loading_root
        score += 0.45
        signals.append(f"spa_root_loading_placeholder:#{root_id}('{placeholder}')")
        details["loading_root_id"] = root_id
        details["loading_root_placeholder"] = placeholder
    elif thin_root:
        root_id, root_text_len = thin_root
        weight = 0.40 if doc_text_len < 250 else 0.30
        score += weight
        signals.append(f"empty_spa_root:#{root_id}({root_text_len}_chars)")
        details["thin_root_id"] = root_id
        details["thin_root_text_length"] = root_text_len

    # 2. "Enable JavaScript" notice in <noscript>
    noscript_hits = [ns.get_text(" ", strip=True) for ns in soup.find_all("noscript")]
    if any(NOSCRIPT_JS_REQUIRED.search(t) for t in noscript_hits if t):
        score += 0.20
        signals.append("noscript_requires_js_message")

    # 3. Low visible-text-to-markup ratio
    html_len = max(1, len(raw_html))
    text_ratio = doc_text_len / html_len
    if text_ratio < 0.05:
        score += 0.20
        signals.append("very_low_text_to_html_ratio")
    elif text_ratio < 0.10:
        score += 0.10
        signals.append("low_text_to_html_ratio")
    details["text_to_html_ratio"] = round(text_ratio, 4)

    # 4. Bundler script tags paired with thin visible text
    bundler_scripts = [
        s.get("src") for s in soup.find_all("script", src=True)
        if BUNDLER_SRC_PATTERN.search(s.get("src", ""))
    ]
    if bundler_scripts and doc_text_len < 400:
        score += 0.15
        signals.append("bundler_scripts_with_thin_text")
        details["bundler_script_examples"] = bundler_scripts[:3]

    # 5. Corroborating framework signatures
    if signals:
        detected_frameworks = [
            fw for fw, pattern in FRAMEWORK_FINGERPRINTS.items()
            if pattern.search(raw_html)
        ]
        if detected_frameworks:
            score += 0.10
            signals.append(f"corroborating_frameworks:{','.join(detected_frameworks)}")
            details["detected_frameworks"] = detected_frameworks

    score = round(min(score, 1.0), 3)
    if score >= 0.55:
        severity = "high"
    elif score >= 0.30:
        severity = "medium"
    elif score >= 0.15:  # Require >= 0.15 so isolated text-ratio drops on static sites don't flag
        severity = "low"
    else:
        severity = None

    return {
        "heuristic_score": score,
        "heuristic_severity": severity,
        "heuristic_signals": signals,
        "heuristic_details": details,
        "doc_text_length": doc_text_len,
    }


def raw_render_metrics(raw_html: str, live_url: str | None, live_render: bool) -> dict:
    raw_text = extract_text(raw_html)
    metrics = {
        "raw_text_length": len(raw_text),
        "raw_word_count": len(raw_text.split()),
        "render_available": False,
        "render_mode": "static_heuristic",
        "rendered_text_length": None,
        "rendered_word_count": None,
        "render_delta_ratio": None,
        "render_caveat": None,
        "render_error": None,
    }

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        # Standard zero-dependency execution path
        metrics.update(heuristic_static_render_estimate(raw_html))
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
                    "cached_dom mode cannot execute relative-path scripts; "
                    "re-run with live network access for dynamic verification."
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
        metrics["render_mode"] = "static_heuristic"
        metrics["render_error"] = str(exc)
        metrics.update(heuristic_static_render_estimate(raw_html))

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
            f"preventing search and AI indices from citing them. Examples: {', '.join(noindexed[:5])}."
        ),
        "suggested_action": {
            "summary": "Remove noindex directives from public pages intended to be discovered and cited by AI systems.",
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

        effective_live_render = live_render
        robots_block_note = None
        if live_render and page_url and not robots_allows_fetch(page_url, cache_dir):
            effective_live_render = False
            robots_block_note = "robots.txt disallows fetching this URL; live browser render skipped."

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
    heuristic_only = [
        m for m in render_samples
        if not m.get("render_available") and m.get("heuristic_score") is not None
    ]

    # Measured browser render path (if browser automation was locally available)
    if rendered:
        tiered = []
        for m in rendered:
            ratio = m.get("render_delta_ratio") or 0
            for threshold, severity in RENDER_DELTA_TIERS:
                if ratio >= threshold:
                    tiered.append((m, severity, ratio))
                    break
        if tiered:
            severity = tiered[0][1]
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
                    "summary": "Ensure key information is present in the initial server-rendered HTML (e.g., SSR/SSG) rather than injected only after client-side JavaScript execution.",
                    "priority": severity,
                },
            })

    # Hardened static heuristic path (deterministic, zero-dependency)
    if heuristic_only:
        flagged = [m for m in heuristic_only if m.get("heuristic_severity")]

        if flagged:
            csr_loading = []
            csr_empty = []
            csr_noscript = []
            csr_bundler = []
            csr_ratio = []

            for m in flagged:
                sigs = m.get("heuristic_signals", [])
                url = m.get("_url", "unknown")

                if any(s.startswith("spa_root_loading_placeholder") for s in sigs):
                    csr_loading.append(url)
                elif any(s.startswith("empty_spa_root") for s in sigs):
                    csr_empty.append(url)

                if "noscript_requires_js_message" in sigs:
                    csr_noscript.append(url)

                if "bundler_scripts_with_thin_text" in sigs:
                    csr_bundler.append(url)

                if "very_low_text_to_html_ratio" in sigs or "low_text_to_html_ratio" in sigs:
                    csr_ratio.append(url)

            # FINDING 1: Loading Placeholders
            if csr_loading:
                findings.append({
                    "id": "CRAWL-RENDER-CSR-LOADING",
                    "title": "Client-Side Rendering: Loading Placeholder Detected",
                    "severity": "high",
                    "evidence": (
                        f"Found explicit loading placeholders (e.g., 'Loading...', 'Please wait') "
                        f"inside initial HTML mount points across {len(csr_loading)} page(s). "
                        f"Text content is missing until JS executes. Examples: {', '.join(csr_loading[:3])}."
                    ),
                    "suggested_action": {
                        "summary": "Implement Server-Side Rendering (SSR) so the initial HTML payload delivers complete body text rather than a loading state.",
                        "priority": "high",
                    },
                })

            # FINDING 2: Empty Framework Mount Points
            if csr_empty:
                findings.append({
                    "id": "CRAWL-RENDER-CSR-EMPTY",
                    "title": "Client-Side Rendering: Empty Framework Mount Point",
                    "severity": "high",
                    "evidence": (
                        f"Detected empty framework mount points (e.g., <div id='root'></div>) "
                        f"in the raw HTML of {len(csr_empty)} page(s), requiring JS hydration to assemble content. "
                        f"Examples: {', '.join(csr_empty[:3])}."
                    ),
                    "suggested_action": {
                        "summary": "Pre-render HTML content on the server (SSR/SSG) so search and AI crawlers can index the page without running client-side scripts.",
                        "priority": "high",
                    },
                })

            # FINDING 3: JS Required Notice
            if csr_noscript:
                findings.append({
                    "id": "CRAWL-RENDER-CSR-NOSCRIPT",
                    "title": "Client-Side Rendering: JS Requirement Notice",
                    "severity": "medium",
                    "evidence": (
                        f"Found <noscript> tags explicitly instructing visitors to enable JavaScript "
                        f"to view content across {len(csr_noscript)} page(s). Examples: {', '.join(csr_noscript[:3])}."
                    ),
                    "suggested_action": {
                        "summary": "Ensure primary informational content is rendered directly in the HTML response for clients without JavaScript runtime support.",
                        "priority": "medium",
                    },
                })

            # FINDING 4: Heavy Bundlers with Thin Text
            if csr_bundler:
                findings.append({
                    "id": "CRAWL-RENDER-CSR-BUNDLER",
                    "title": "Client-Side Rendering: Bundler Scripts with Minimal HTML Text",
                    "severity": "medium",
                    "evidence": (
                        f"Detected heavy SPA bundler scripts (Webpack, Next.js, Vite) paired with "
                        f"unusually thin visible text across {len(csr_bundler)} page(s). "
                        f"Examples: {', '.join(csr_bundler[:3])}."
                    ),
                    "suggested_action": {
                        "summary": "Configure client-side bundler setups to deliver pre-rendered static content for public landing pages.",
                        "priority": "medium",
                    },
                })

            # FINDING 5: Low Text Ratio (Suppressed if already captured by mount point findings)
            if csr_ratio and not csr_loading and not csr_empty:
                findings.append({
                    "id": "CRAWL-RENDER-CSR-RATIO",
                    "title": "Client-Side Rendering: Low Visible Text Ratio",
                    "severity": "low",
                    "evidence": (
                        f"Observed an unusually low ratio of visible text to markup across {len(csr_ratio)} "
                        f"page(s), indicating page copy may depend on subsequent client-side fetches. "
                        f"Examples: {', '.join(csr_ratio[:3])}."
                    ),
                    "suggested_action": {
                        "summary": "Verify that primary informational copy is present in the initial document body rather than loaded asynchronously.",
                        "priority": "low",
                    },
                })

        else:
            findings.append({
                "id": "CRAWL-RENDER-CSR-PASS-000",
                "title": "Server-Rendered / Static HTML Verified",
                "severity": "info",
                "evidence": (
                    f"Static HTML analysis evaluated {len(heuristic_only)} sampled page(s). "
                    f"No empty framework mount points, loading placeholders, 'JavaScript required' notices, "
                    f"or extreme text-to-markup imbalances were detected. Primary text content is present "
                    f"directly in the initial HTML document payload."
                ),
                "suggested_action": None,
            })

    if not rendered and not heuristic_only and render_samples and all(m.get("render_error") for m in render_samples):
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
        help="Skip live browser navigation and fall back to cached_dom parsing only.",
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