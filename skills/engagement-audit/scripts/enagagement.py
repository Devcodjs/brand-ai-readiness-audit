import sys
import json
import argparse
import re
from pathlib import Path
from bs4 import BeautifulSoup


def load_cached_pages(cache_dir):
    index_path = Path(cache_dir) / "cache_index.json"
    if not index_path.exists():
        return []
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []

    pages = []
    for page in index.get("pages", []):
        if page.get("success"):
            try:
                html = Path(page["file"]).read_text(encoding="utf-8", errors="replace")
                soup = BeautifulSoup(html, "html.parser")
                pages.append({
                    "url": page["url"],
                    "html": html,
                    "soup": soup,
                    "content_classification": page.get("content_classification"),
                })
            except Exception:
                continue
    return pages


# Class-name signals that USUALLY mean "not shown by default", even though we
# can't evaluate real computed CSS from static HTML alone. Treated as a
# confidence-lowering signal, not an exclusion — a class-toggled modal that
# ships visible-by-default in the raw HTML (a common real mistake) would be
# wrongly cleared if we excluded on class name alone.
LIKELY_INACTIVE_CLASSES = re.compile(
    r'\b(hidden|d-none|is-hidden|u-hidden|invisible|collapse(?!d)|hide)\b', re.I
)
FULLSCREEN_HINTS = re.compile(r'fullscreen|full-page|full-screen|takeover|lightbox-open', re.I)
OVERLAY_KEYWORDS = re.compile(
    r'cookie|consent|gdpr|onetrust|cookiebot|cc-banner|cookie-notice|modal|'
    r'popup|overlay|lightbox|newsletter|signup-modal|paywall|login-wall', re.I
)


def _style_dict(style_attr):
    props = {}
    for chunk in (style_attr or "").split(";"):
        if ":" not in chunk:
            continue
        k, _, v = chunk.partition(":")
        props[k.strip().lower()] = v.strip().lower()
    return props


def _looks_fullscreen(style, cls, id_val):
    """
    Best-effort viewport-coverage heuristic from static markup only: edges
    pinned to 0 on all sides, an explicit large percentage/viewport-unit
    dimension, or an unambiguous fullscreen/takeover naming convention.
    Absence of these signals means "not enough static evidence to call this
    a blocking interstitial" — NOT "confirmed small" — callers should treat
    that case as lower confidence, not a clean negative.
    """
    edge_keys = ("top", "left", "right", "bottom")
    edges_pinned = all(k in style for k in edge_keys) and \
        all(style.get(k, "").strip().rstrip("px") in ("0", "") for k in edge_keys)

    def _big(val):
        val = (val or "").strip()
        m = re.match(r'^(\d+(?:\.\d+)?)(vw|vh|%)$', val)
        if m:
            return float(m.group(1)) >= 80
        m = re.match(r'^(\d+)px$', val)
        if m:
            return int(m.group(1)) >= 600
        return False

    big_dims = any(_big(style.get(k)) for k in ("width", "height", "min-width", "min-height"))
    name_hint = bool(FULLSCREEN_HINTS.search(f"{cls} {id_val}"))
    return edges_pinned or big_dims or name_hint


# ---------------------------------------------------------------------------
# Point 1 — Page weight / render-blocking load proxy
# ---------------------------------------------------------------------------
def check_page_weight(pages, findings):
    try:
        heavy_pages, blocking_pages = [], []
        for page in pages:
            html_bytes = len(page["html"].encode("utf-8", errors="ignore"))
            if html_bytes >= 900_000:  # raw HTML this large almost always means heavy inlining
                heavy_pages.append((page["url"], html_bytes))

            head = page["soup"].find("head")
            if head:
                blocking = sum(
                    1 for s in head.find_all("script", src=True)
                    if not s.has_attr("async") and not s.has_attr("defer")
                    and s.get("type", "").lower() != "module"
                )
                if blocking >= 4:
                    blocking_pages.append((page["url"], blocking))

        if heavy_pages:
            worst = max(heavy_pages, key=lambda x: x[1])
            severity = "high" if worst[1] >= 2_000_000 else "medium"
            findings.append({
                "id": "ENG-WEIGHT-001",
                "title": "Bloated Raw HTML Payload",
                "severity": severity,
                "evidence": (
                    f"{len(heavy_pages)}/{len(pages)} page(s) served raw HTML >=900KB "
                    f"before any external asset loads; worst case {worst[0]} at "
                    f"{worst[1]/1_000_000:.1f}MB. Measured from the server-delivered "
                    f"document only - real total page weight is at least this large."
                ),
                "suggested_action": {
                    "summary": (
                        "Check for inlined base64 images/fonts, duplicated inline <style> "
                        "blocks, or embedded state dumps. Move large binary payloads to "
                        "separate cacheable requests instead of inlining them into the "
                        "document every crawler and visitor must download before anything renders."
                    ),
                    "priority": severity,
                },
            })

        if blocking_pages:
            worst = max(blocking_pages, key=lambda x: x[1])
            severity = "high" if worst[1] >= 8 else "medium"
            findings.append({
                "id": "ENG-WEIGHT-002",
                "title": "Render-Blocking Scripts in <head>",
                "severity": severity,
                "evidence": (
                    f"{len(blocking_pages)}/{len(pages)} page(s) load 4+ external "
                    f"<script> tags in <head> without async/defer/module; worst case "
                    f"{worst[0]} with {worst[1]} blocking script(s)."
                ),
                "suggested_action": {
                    "summary": (
                        "Add defer (or async where order doesn't matter) to head scripts "
                        "not needed before first paint — start with analytics, chat-widget, "
                        "and A/B-testing scripts."
                    ),
                    "priority": severity,
                },
            })
    except Exception as e:
        sys.stderr.write(f"Error in ENG-WEIGHT checks: {e}\n")


# ---------------------------------------------------------------------------
# Point 2 — Overlay / interstitial blockade (fixes the coverage-area gap)
# ---------------------------------------------------------------------------
def check_overlays(pages, findings):
    try:
        found, affected, low_confidence = [], set(), set()
        has_gated_wall = False

        for page in pages:
            for el in page["soup"].find_all(True):
                style = _style_dict(el.get("style", ""))
                cls = " ".join(el.get("class", []))
                id_val = el.get("id", "")

                if not (OVERLAY_KEYWORDS.search(cls) or OVERLAY_KEYWORDS.search(id_val) or el.name == "dialog"):
                    continue
                if style.get("display") == "none" or style.get("visibility") == "hidden" or el.has_attr("hidden"):
                    continue
                if not (style.get("position") in ("fixed", "absolute") or el.name == "dialog"):
                    continue

                class_hint_hidden = bool(LIKELY_INACTIVE_CLASSES.search(f"{cls} {id_val}"))
                covers_viewport = _looks_fullscreen(style, cls, id_val)
                is_gated = bool(el.find("input", attrs={"type": "password"})) or \
                    bool(re.search(r'paywall|login-wall|gated|premium-content', f"{cls} {id_val}", re.I))
                if is_gated:
                    has_gated_wall = True

                found.append(f"{el.name}.{cls.replace(' ', '.')[:40] or id_val}")
                (affected if covers_viewport and not class_hint_hidden else low_confidence).add(page["url"])
                break

        if affected:
            ratio = len(affected) / len(pages)
            severity = ("critical" if ratio > 0.5 else "high") if has_gated_wall \
                else ("high" if ratio > 0.8 else "medium")
            findings.append({
                "id": "ENG-OVERLAY-001",
                "title": "Full-Viewport Overlay Likely Blocking Content on Load",
                "severity": severity,
                "evidence": (
                    f"Found statically positioned, viewport-covering overlay element(s) on "
                    f"{len(affected)}/{len(pages)} page(s): {list(set(found))[:5]}. Coverage "
                    f"was inferred from inline sizing/positioning (edge-pinned or \u226580% "
                    f"width/height/viewport-unit), not computed CSS — JS-injected consent "
                    f"tools that mount after page load (OneTrust, Cookiebot, etc.) are not "
                    f"covered by this check."
                ),
                "suggested_action": {
                    "summary": (
                        "If the overlay serves a real purpose (consent, paywall), keep the "
                        "underlying content present in the DOM behind it so crawlers can "
                        "still read it. If it's promotional, delay it behind a scroll or "
                        "time trigger instead of showing it on load."
                    ),
                    "priority": severity,
                },
            })
        elif low_confidence:
            findings.append({
                "id": "ENG-OVERLAY-002",
                "title": "Overlay-Pattern Elements Present (Coverage Unconfirmed)",
                "severity": "low",
                "evidence": (
                    f"{len(low_confidence)} page(s) have fixed/absolute-positioned elements "
                    f"matching common overlay/consent naming conventions, but static markup "
                    f"didn't give enough sizing info to confirm real viewport coverage."
                ),
                "suggested_action": {
                    "summary": "Manually load the page to confirm whether it blocks content on arrival.",
                    "priority": "low",
                },
            })
    except Exception as e:
        sys.stderr.write(f"Error in ENG-OVERLAY checks: {e}\n")


# ---------------------------------------------------------------------------
# Point 3 — Mobile viewport correctness
# ---------------------------------------------------------------------------
def check_mobile_viewport(pages, findings):
    try:
        missing, broken, zoom_disabled = [], [], []
        for page in pages:
            soup = page.get("soup")
            if not soup:
                continue
                
            # Case-insensitive match to handle <meta name="Viewport"> variants gracefully
            tag = soup.find("meta", attrs={"name": re.compile(r"^viewport$", re.I)})
            
            if not tag or not tag.get("content"):
                missing.append(page["url"])
                continue
                
            content = tag["content"].lower()
            content_no_spaces = content.replace(" ", "")
            
            # Check for standard responsive width mapping
            if "width=device-width" not in content_no_spaces:
                broken.append(page["url"])
                
            # Check for accessibility/zoom restrictions
            if re.search(r'user-scalable\s*=\s*no', content) or re.search(r'maximum-scale\s*=\s*1(\.0)?\b', content):
                zoom_disabled.append(page["url"])

        # The Observation Hedge: We used a Desktop UA, so adaptive sites will naturally fail this.
        desktop_ua_caveat = (
            "NOTE: This crawl used a desktop User-Agent. If your site uses server-side adaptive "
            "delivery (serving different HTML/templates to mobile devices) rather than responsive CSS, "
            "this may be a false positive."
        )

        if missing:
            severity = "high" if len(missing) / len(pages) >= 0.5 else "medium"
            findings.append({
                "id": "ENG-VIEWPORT-001",
                "title": "Missing Mobile Viewport Meta Tag",
                "severity": severity,
                "evidence": (
                    f"{len(missing)}/{len(pages)} sampled page(s) lack a <meta name=\"viewport\"> tag. "
                    f"{desktop_ua_caveat} Examples: {', '.join(missing[:3])}."
                ),
                "suggested_action": {
                    "summary": (
                        "Verify whether this omission is an artifact of adaptive desktop delivery. "
                        "If your site relies on responsive design (serving the same HTML to all devices), "
                        "you must add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"> "
                        "to your global <head> to ensure mobile readability."
                    ),
                    "priority": severity,
                },
            })
            
        if broken:
            severity = "high" if len(broken) / len(pages) >= 0.5 else "medium"
            findings.append({
                "id": "ENG-VIEWPORT-002",
                "title": "Viewport Meta Tag Missing width=device-width",
                "severity": severity,
                "evidence": (
                    f"{len(broken)}/{len(pages)} sampled page(s) declare a viewport tag, but omit the 'width=device-width' "
                    f"directive required for fluid responsive scaling. {desktop_ua_caveat} Examples: {', '.join(broken[:3])}."
                ),
                "suggested_action": {
                    "summary": (
                        "Verify whether this omission is an artifact of adaptive desktop delivery. "
                        "If your site is responsive, explicitly set content=\"width=device-width, initial-scale=1\" "
                        "in your viewport tag so AI-referred mobile visitors don't bounce due to horizontal scrolling."
                    ),
                    "priority": severity,
                },
            })
            
        if zoom_disabled:
            # Zoom restrictions are unconditionally bad for accessibility regardless of adaptive/responsive delivery
            findings.append({
                "id": "ENG-VIEWPORT-003",
                "title": "Pinch-Zoom Disabled in Viewport Meta Tag",
                "severity": "medium",
                "evidence": f"{len(zoom_disabled)}/{len(pages)} sampled page(s) restrict zooming by setting user-scalable=no or maximum-scale=1. Examples: {', '.join(zoom_disabled[:3])}.",
                "suggested_action": {
                    "summary": "Remove 'user-scalable=no' and 'maximum-scale' restrictions from your viewport tag. Disabling pinch-to-zoom is a W3C accessibility violation and degrades mobile engagement.",
                    "priority": "medium",
                },
            })
            
    except Exception as e:
        sys.stderr.write(f"Error in ENG-VIEWPORT checks: {e}\n")


def run_checks(pages):
    findings = []
    usable_pages = [p for p in pages if p.get("content_classification") == "normal"]

    if not usable_pages:
        if pages:
            return [{
                "id": "ENG-SKIP-NO-USABLE-PAGES",
                "title": "Engagement Audit Skipped (No Usable Pages)",
                "severity": "info",
                "evidence": f"{len(pages)} pages sampled but 0 had content_classification == 'normal'.",
                "suggested_action": None,
                "skill": "engagement-audit",
            }]
        return findings

    check_page_weight(usable_pages, findings)
    check_overlays(usable_pages, findings)
    check_mobile_viewport(usable_pages, findings)

    if not any(f.get("severity") in ("critical", "high", "medium") for f in findings):
        findings.append({
            "id": "ENG-PASS-000",
            "title": "No Engagement Blockers Detected in Available Evidence",
            "severity": "info",
            "evidence": f"Checked {len(usable_pages)} page(s) for payload weight, blocking scripts, overlays, and viewport config. No defect crossed reporting thresholds.",
            "suggested_action": None,
        })
    return findings


def main():
    parser = argparse.ArgumentParser(description="Engagement Audit")
    parser.add_argument("--url", required=True, help="Target URL")
    parser.add_argument("--cache-dir", required=True, help="Cache directory")
    args = parser.parse_args()
    print(json.dumps(run_checks(load_cached_pages(args.cache_dir)), indent=2))


if __name__ == "__main__":
    main()