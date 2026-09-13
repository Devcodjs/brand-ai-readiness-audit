import argparse
import json
import os
import re
import sys
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


# Points directly to 'skills/utils'
UTILS_DIR = Path(__file__).resolve().parents[2] / "utils"
if str(UTILS_DIR) not in sys.path:
    sys.path.insert(0, str(UTILS_DIR))

from evidence_gate import (
    BLOCKED_URL_PATTERNS,
    CHALLENGE_PATTERNS,
    classify_page_evidence,
    detect_challenge,
    extract_text,
    summarize_blocked_url_targets,
)

# Pure AI Discoverability & Search Agents (No Foundation Model Training Scrapers)
DISCOVERABILITY_BOTS = [
    "OAI-SearchBot",      # ChatGPT Search Indexing
    "ChatGPT-User",       # ChatGPT Live Web Browsing fetches
    "Claude-SearchBot",   # Anthropic Search Indexing
    "Claude-User",        # Anthropic Live Web Browsing fetches
    "PerplexityBot",      # Perplexity AI Search
    "Grok",               # xAI Search
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


def heuristic_static_render_estimate(raw_html: str) -> dict:
    soup = BeautifulSoup(raw_html, "html.parser")
    doc_text = extract_text(raw_html)
    doc_text_len = len(doc_text)

    signals = []
    details = {}
    score = 0.0

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

    noscript_hits = [ns.get_text(" ", strip=True) for ns in soup.find_all("noscript")]
    if any(NOSCRIPT_JS_REQUIRED.search(t) for t in noscript_hits if t):
        score += 0.20
        signals.append("noscript_requires_js_message")

    html_len = max(1, len(raw_html))
    text_ratio = doc_text_len / html_len
    if text_ratio < 0.05:
        score += 0.20
        signals.append("very_low_text_to_html_ratio")
    elif text_ratio < 0.10:
        score += 0.10
        signals.append("low_text_to_html_ratio")
    details["text_to_html_ratio"] = round(text_ratio, 4)

    bundler_scripts = [
        s.get("src") for s in soup.find_all("script", src=True)
        if BUNDLER_SRC_PATTERN.search(s.get("src", ""))
    ]
    if bundler_scripts and doc_text_len < 400:
        score += 0.15
        signals.append("bundler_scripts_with_thin_text")
        details["bundler_script_examples"] = bundler_scripts[:3]

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
    elif score >= 0.15:
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
        for bot in DISCOVERABILITY_BOTS:
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
    parsed_pages = 0
    homepage_noindexed = False
    homepage_url = None

    for page in pages:
        path = page.get("file")
        page_url = page.get("final_url") or page.get("url", "unknown")
        header_tag = (page.get("headers") or {}).get("X-Robots-Tag", "")

        is_noindex = False
        parsed_successfully = False

        if header_tag:
            parsed_successfully = True
            if "noindex" in header_tag.lower():
                is_noindex = True

        if not is_noindex and path and os.path.exists(path):
            try:
                html = Path(path).read_text(encoding="utf-8", errors="ignore")
                soup = BeautifulSoup(html, "html.parser")
                parsed_successfully = True
                tag = soup.find("meta", attrs={"name": re.compile(r"robots", re.I)})
                if tag and "noindex" in (tag.get("content") or "").lower():
                    is_noindex = True
            except OSError:
                pass

        if parsed_successfully:
            parsed_pages += 1
            if is_noindex:
                noindexed.append(page_url)
                path_part = urlparse(page_url).path
                is_home = (
                    page.get("page_type") == "home"
                    or page.get("index") == 0
                    or path_part in ("", "/")
                )
                if is_home:
                    homepage_noindexed = True
                    homepage_url = page_url

    if not noindexed or parsed_pages == 0:
        return []

    ratio = len(noindexed) / parsed_pages

    if homepage_noindexed or ratio >= 0.8:
        severity = "critical"
        if homepage_noindexed and ratio >= 0.8:
            reason = f"the homepage ({homepage_url}) and {ratio:.0%} of verified pages carry a noindex directive"
        elif homepage_noindexed:
            reason = f"the root homepage ({homepage_url}) explicitly forbids indexing via noindex"
        else:
            reason = f"{len(noindexed)}/{parsed_pages} ({ratio:.0%}) verified pages carry a noindex directive"
    elif ratio >= 0.4 or len(noindexed) > 1:
        severity = "high"
        reason = f"{len(noindexed)}/{parsed_pages} ({ratio:.0%}) verified pages carry a noindex directive"
    else:
        severity = "medium"
        reason = f"{len(noindexed)}/{parsed_pages} verified page carries a noindex directive"

    return [{
        "id": "DISC-NOINDEX-001",
        "title": "Pages Blocked From Indexing via noindex",
        "severity": severity,
        "evidence": (
            f"Site-level policy explicitly declares {reason}. "
            f"Because this is declared directly in HTML meta tags or HTTP headers, "
            f"AI and search indexers honor this directive regardless of bot identity. "
            f"Examples: {', '.join(noindexed[:5])}."
        ),
        "suggested_action": {
            "summary": (
                "Ideal Fix: Remove 'noindex' from <meta name='robots'> and X-Robots-Tag headers on public canonical routes. "
                "Scalable Quick Win: Check your global CMS settings (e.g., WordPress 'Discourage search engines' toggle), "
                "SEO plugins, or edge CDN transform rules to unblock indexability globally at the template level."
            ),
            "priority": severity,
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

    evidence_records = []
    for page in pages:
        path = page.get("file")
        html = ""
        if path and os.path.exists(path):
            try:
                html = Path(path).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                html = ""
        ev = classify_page_evidence(page, html)
        page["_evidence_gate"] = ev
        
        if not ev["usable"]:
            page["content_classification"] = ev["classification"]

        evidence_records.append((page, ev))

    challenge_pages = [
        p for p, ev in evidence_records
        if ev["classification"] == "bot_challenge"
    ]
    normal_pages = [
        p for p, ev in evidence_records
        if ev["usable"]
    ]
    blocked_pages = [
        p for p, ev in evidence_records
        if ev["classification"] in {"blocked", "auth_wall"}
    ]

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
            "audit_effect": "page-dependent semantic checks are suppressed because trustworthy normal-page evidence is unavailable.",
        })

    if blocked_pages:
        ratio = len(blocked_pages) / max(1, len(pages))
        severity = "high" if ratio >= 0.8 else "medium"
        findings.append({
            "id": "CRAWL-BLOCKED-002",
            "title": "Access-Restricted Pages Detected",
            "severity": severity,
            "evidence": (
                f"{len(blocked_pages)}/{len(pages)} sampled URLs returned access/auth restrictions or bot-challenges instead of normal content. "
                f"NOTE: This crawl used a generic browser signature. If your WAF (e.g., Cloudflare, Akamai) allowlists verified AI crawlers, real bots may bypass this block."
            ),
            "suggested_action": {
                "summary": "Check server access logs for 200 OK responses to 'OAI-SearchBot' or 'PerplexityBot'. If they are also blocked, update your edge WAF rules to explicitly allowlist verified AI discovery agents.",
                "priority": severity,
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

    if heuristic_only:
        flagged = [m for m in heuristic_only if m.get("heuristic_severity")]
        if flagged:
            csr_loading, csr_empty, csr_noscript, csr_bundler, csr_ratio = [], [], [], [], []
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

            csr_caveat = "NOTE: This observation relies on a standard browser UA. If you use dynamic rendering (e.g., Prerender.io) via UA-sniffing, verified bots may receive full HTML."

            if csr_loading:
                findings.append({
                    "id": "CRAWL-RENDER-CSR-LOADING",
                    "title": "Client-Side Rendering: Loading Placeholder Detected",
                    "severity": "high",
                    "evidence": f"Found explicit loading placeholders inside initial HTML mount points across {len(csr_loading)} page(s). {csr_caveat} Examples: {', '.join(csr_loading[:3])}.",
                    "suggested_action": {"summary": "Verify that your edge router serves pre-rendered DOMs to AI user-agents. If not, implement SSR/SSG to deliver complete text rather than a loading state.", "priority": "high"},
                })

            if csr_empty:
                findings.append({
                    "id": "CRAWL-RENDER-CSR-EMPTY",
                    "title": "Client-Side Rendering: Empty Framework Mount Point",
                    "severity": "high",
                    "evidence": f"Detected empty framework mount points in the raw HTML of {len(csr_empty)} page(s), requiring JS hydration. {csr_caveat} Examples: {', '.join(csr_empty[:3])}.",
                    "suggested_action": {"summary": "Verify that your edge router serves pre-rendered DOMs to AI user-agents. If not, implement SSR/SSG so crawlers can index content without running client-side scripts.", "priority": "high"},
                })

            if csr_noscript:
                findings.append({
                    "id": "CRAWL-RENDER-CSR-NOSCRIPT",
                    "title": "Client-Side Rendering: JS Requirement Notice",
                    "severity": "medium",
                    "evidence": f"Found <noscript> tags explicitly instructing visitors to enable JavaScript to view content across {len(csr_noscript)} page(s). {csr_caveat} Examples: {', '.join(csr_noscript[:3])}.",
                    "suggested_action": {"summary": "Ensure primary informational content is rendered directly in the HTML response for clients without JavaScript runtime support.", "priority": "medium"},
                })

            if csr_bundler:
                findings.append({
                    "id": "CRAWL-RENDER-CSR-BUNDLER",
                    "title": "Client-Side Rendering: Bundler Scripts with Minimal HTML Text",
                    "severity": "medium",
                    "evidence": f"Detected heavy SPA bundler scripts (Webpack, Next.js, Vite) paired with unusually thin visible text across {len(csr_bundler)} page(s). {csr_caveat} Examples: {', '.join(csr_bundler[:3])}.",
                    "suggested_action": {"summary": "Configure client-side bundler setups to deliver pre-rendered static content for public landing pages.", "priority": "medium"},
                })

            if csr_ratio and not csr_loading and not csr_empty:
                findings.append({
                    "id": "CRAWL-RENDER-CSR-RATIO",
                    "title": "Client-Side Rendering: Low Visible Text Ratio",
                    "severity": "medium",
                    "evidence": (
                        f"Observed an unusually low ratio of visible text to HTML markup (< 10%) across {len(csr_ratio)} page(s). "
                        f"Heavy DOM structures with sparse text dilute the semantic signal. {csr_caveat} Examples: {', '.join(csr_ratio[:3])}."
                    ),
                    "suggested_action": {
                        "summary": "Low initial text density impairs LLM parsing. Verify that core informational copy is directly embedded in the server-delivered DOM.",
                        "priority": "medium",
                    },
                })
        else:
            findings.append({
                "id": "CRAWL-RENDER-CSR-PASS-000",
                "title": "Server-Rendered / Static HTML Verified",
                "severity": "info",
                "evidence": f"Static HTML analysis evaluated {len(heuristic_only)} sampled page(s). No empty framework mount points or loading placeholders were detected.",
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
        is_total_block = len(blocked_bots) == len(DISCOVERABILITY_BOTS)
        
        severity = "critical" if is_total_block else "high"
        
        if is_total_block:
            evidence_str = f"robots.txt explicitly disallows ALL tracked AI search/retrieval agents ({', '.join(blocked_bots)}). This prevents live retrieval, meaning AI cannot fetch current facts directly from your site."
            action_summary = (
                "This block prevents future crawling but does not erase past training data. AI systems will still answer "
                "questions about your brand using stale memory or third-party mentions, but you permanently lose the ability "
                "to correct outdated information or earn referral citations. If this is a deliberate strategy to protect a data moat, "
                "no action is needed. However, if accurate self-representation and referral traffic matter, removing these "
                "specific retrieval-bot disallows is required to restore live AI discoverability."
            )
        else:
            evidence_str = f"robots.txt explicitly disallows specific AI search/retrieval agents: {', '.join(blocked_bots)}."
            action_summary = (
                "Review robots.txt rules for AI/search crawlers. Ensure you are not accidentally blocking specific discovery engines "
                "(like OAI-SearchBot or PerplexityBot) due to legacy security templates. Blocking these bots forces them to rely on "
                "stale training data or third-party mentions rather than your live site."
            )

        findings.append({
            "id": "DISC-ROBOTS-001",
            "title": "AI Crawler User-Agents Blocked by robots.txt",
            "severity": severity,
            "evidence": evidence_str,
            "suggested_action": {
                "summary": action_summary, 
                "priority": severity
            },
        })
    elif robot_meta["robots_checked"]:
        findings.append({
            "id": "DISC-PASS-000",
            "title": "AI Crawling Not Blocked by robots.txt",
            "severity": "info",
            "evidence": "Checked cached robots.txt against standard AI discoverability user-agents; no explicit disallow was found for the audited target URL.",
            "suggested_action": None,
        })

    try:
        index_path = Path(cache_dir) / "cache_index.json"
        index_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except OSError:
        pass

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
    parser.set_defaults(live_render=False)
    args = parser.parse_args()
    print(json.dumps(
        audit_crawl_and_render(
            args.url,
            cache_dir=args.cache_dir,
            live_render=args.live_render,
        ),
        indent=2,
    ))