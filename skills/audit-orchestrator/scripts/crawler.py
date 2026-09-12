import argparse
import concurrent.futures
import hashlib
import json
import re
import shutil
import time
import urllib.robotparser
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

import sys

# Points directly to 'skills/utils'
UTILS_DIR = Path(__file__).resolve().parents[2] / "utils"
if str(UTILS_DIR) not in sys.path:
    sys.path.insert(0, str(UTILS_DIR))


from evidence_gate import looks_like_blocked_url

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

MAX_PAGES = 12
REQUEST_TIMEOUT = 10
MAX_ALLOWED_DELAY = 5.0
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "ref", "ref_",
}

CHALLENGE_PATTERNS = [
    re.compile(r"verify\s+you\s+are\s+human", re.I),
    re.compile(r"are\s+you\s+a\s+human", re.I),
    re.compile(r"checking\s+your\s+browser", re.I),
    re.compile(r"just\s+a\s+moment", re.I),
    re.compile(r"security\s+check", re.I),
    re.compile(r"robot\s+check", re.I),
    re.compile(r"robot\s+or\s+human", re.I),
    re.compile(r"unusual\s+traffic", re.I),
    re.compile(r"access\s+denied", re.I),
    re.compile(r"request\s+blocked", re.I),
    re.compile(r"enable\s+(javascript|cookies)", re.I),
    re.compile(r"captcha", re.I),
    re.compile(r"cf-chl-", re.I),
    re.compile(r"challenge-platform", re.I),
]

PAGE_TYPE_HINTS = (
    ("product", ("/ip/", "/product/", "/products/", "/dp/", "/p/")),
    ("location", ("/store/", "/stores/", "/locations/", "/location/")),
    ("category", ("/category/", "/categories/", "/cp/", "/collections/", "/shop/")),
    ("search", ("/search", "/s?", "/browse?")),
    ("article", ("/article/", "/blog/", "/news/", "/stories/", "/help/", "/learn/")),
)

# --- Subdomain-pivot tuning ---
# Subdomains that host assets/infra rather than crawlable site content.
# Even if one of these clusters more heavily than the real content
# subdomain (e.g. a CDN referenced on every page), it's never the right
# pivot target, so it's excluded before any counting happens.
INFRA_SUBDOMAIN_LABELS = {
    "cdn", "static", "assets", "img", "images", "media", "api",
    "accounts", "account", "login", "auth", "sso", "help", "support",
    "status", "mail", "email", "cache", "edge", "ajax",
}
# Below this many same-subdomain links, a homepage is treated as too thin
# to represent real site content on its own (a routing/portal page).
# NOTE: this gate only applies to the "dominant cluster" pattern below -
# see the ordering comment inside _evaluate_subdomain_pivot for why the
# diffuse fan-out pattern must be checked BEFORE this gate, not after it.
MIN_SELF_LINKS_TO_SKIP_PIVOT = 3
# A single other subdomain clearly "wins" once it has this many links -
# the classic blog.example.com / shop.example.com case.
DOMINANT_CLUSTER_MIN_LINKS = 3
# A portal that fans out to many DIFFERENT subdomains, each with only one
# or two links (e.g. Wikipedia's ~300 language editions), is just as real
# a signal even though no single one hits DOMINANT_CLUSTER_MIN_LINKS -
# what matters there is breadth of distinct subdomains, not depth of any one.
MIN_DISTINCT_SUBDOMAINS_FOR_FANOUT = 5

def find_repository_root() -> Path:
    """Find the marketplace root without relying on a fixed directory depth."""
    here = Path(__file__).resolve()
    for candidate in [here.parent, *here.parents]:
        if (candidate / "marketplace.json").exists() and (candidate / "skills").exists():
            return candidate
    # Fallback preserves compatibility with the original repo layout.
    return here.parents[3]


REPOSITORY_ROOT = find_repository_root()
CACHE_DIR = REPOSITORY_ROOT / "audit-cache"
PAGES_DIR = CACHE_DIR / "pages"

TRAINING_BOTS = [
    "GPTBot", "Google-Extended", "CCBot", "Anthropic-ai", "Amazonbot", "FacebookBot"
]
SEARCH_BOTS = [
    "OAI-SearchBot", "ChatGPT-User", "PerplexityBot", "ClaudeBot"
]


def normalize_url(url: str) -> str:
    """Remove fragments/tracking noise while preserving meaningful query parameters."""
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower() or "https"
    host = parsed.netloc.lower()
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")

    query_pairs = [
        (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS
    ]
    query = urlencode(sorted(query_pairs))
    return urlunparse((scheme, host, path, "", query, ""))


def classify_url(url: str) -> str:
    parsed = urlparse(url)
    path = (parsed.path + ("?" + parsed.query if parsed.query else "")).lower()
    if path in ("", "/"):
        return "home"
    for page_type, hints in PAGE_TYPE_HINTS:
        if any(hint in path for hint in hints):
            return page_type
    return "other"


def page_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "noscript", "template"]):
        node.decompose()
    return " ".join(soup.stripped_strings)


def content_signature(text: str) -> str:
    normalized = " ".join(text.lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def detect_challenge(html: str, status_code: int, headers: dict) -> dict:
    text = page_text(html)
    title = BeautifulSoup(html, "html.parser").title
    title_text = title.get_text(" ", strip=True) if title else ""
    combined = f"{title_text} {text[:5000]}".lower()

    matches = [p.pattern for p in CHALLENGE_PATTERNS if p.search(combined)]
    signals = []
    score = 0.0

    if status_code in (403, 429):
        score += 0.35
        signals.append(f"http_{status_code}")
    elif status_code in (503, 502):
        score += 0.20
        signals.append(f"http_{status_code}")

    if matches:
        score += min(0.55, 0.18 * len(matches))
        signals.append("challenge_text")

    if len(text) < 350:
        score += 0.08
        signals.append("very_short_document")

    html_lower = html.lower()
    if any(marker in html_lower for marker in ("cf-chl-", "challenge-platform", "captcha", "turnstile")):
        score += 0.18
        signals.append("challenge_markup")

    content_type = str(headers.get("Content-Type", "")).lower()
    if status_code == 200 and "text/html" not in content_type and content_type:
        signals.append("unexpected_content_type")

    return {
        "is_challenge": score >= 0.50,
        "score": round(min(score, 1.0), 3),
        "signals": sorted(set(signals)),
        "matched_patterns": sorted(set(matches)),
        "text_length": len(text),
    }


class SiteCrawler:
    def __init__(self, target_url: str, max_pages: int = MAX_PAGES):
        self.target_url = normalize_url(target_url)
        parsed = urlparse(self.target_url)
        self.domain_root = f"{parsed.scheme}://{parsed.netloc}"
        self.max_pages = max(1, int(max_pages))
        self.robot_parser = urllib.robotparser.RobotFileParser()
        self.robot_parser.set_url(f"{self.domain_root}/robots.txt")
        self.crawl_delay = 0.0
        self.last_pivot_diagnostics = None

    def reset_cache_dir(self):
        if CACHE_DIR.exists():
            shutil.rmtree(CACHE_DIR)
        PAGES_DIR.mkdir(parents=True, exist_ok=True)

    def fetch_and_parse_robots(self) -> dict:
        # Always parse into a FRESH parser. urllib.robotparser's internal
        # _add_entry() keeps the *first* "User-agent: *" block it ever sees
        # on a given instance ("the first default entry wins") and silently
        # drops later ones. Since this method is called a second time after
        # a subdomain pivot (see build_cache), reusing self.robot_parser
        # would leave every is_allowed()/crawl_delay() check still running
        # against the ORIGINAL domain's robots.txt instead of the pivoted
        # target's - a real robots.txt-compliance bug, not just a stale-data
        # one. A fresh instance per call makes this method safe to re-run.
        self.robot_parser = urllib.robotparser.RobotFileParser()
        self.robot_parser.set_url(f"{self.domain_root}/robots.txt")

        meta = {
            "robots_present": False,
            "robots_status": None,
            "sitemap_present": False,
            "crawl_delay": 0.0,
            "blocked_training_bots": [],
            "blocked_search_bots": [],
            "crawl_status": "success",
            "failure_reason": None,
        }
        sitemap_candidates = []

        try:
            robots_resp = requests.get(
                f"{self.domain_root}/robots.txt",
                headers=REQUEST_HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            meta["robots_status"] = robots_resp.status_code
            if robots_resp.status_code == 200 and robots_resp.text.strip():
                meta["robots_present"] = True
                (CACHE_DIR / "robots.txt").write_text(robots_resp.text, encoding="utf-8")
                self.robot_parser.parse(robots_resp.text.splitlines())
                sitemap_candidates.extend(self.robot_parser.site_maps() or [])

                for bot in TRAINING_BOTS:
                    if not self.robot_parser.can_fetch(bot, self.target_url):
                        meta["blocked_training_bots"].append(bot)
                for bot in SEARCH_BOTS:
                    if not self.robot_parser.can_fetch(bot, self.target_url):
                        meta["blocked_search_bots"].append(bot)

                delay = self.robot_parser.crawl_delay("*")
                if delay:
                    self.crawl_delay = min(float(delay), MAX_ALLOWED_DELAY)
                    meta["crawl_delay"] = self.crawl_delay
            elif robots_resp.status_code in (403, 429):
                meta["crawl_status"] = "robots_unavailable"
                meta["failure_reason"] = f"robots.txt returned HTTP {robots_resp.status_code}"
        except requests.RequestException as exc:
            meta["crawl_status"] = "robots_unavailable"
            meta["failure_reason"] = str(exc)

        for sm_path in ["/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml"]:
            full_url = f"{self.domain_root}{sm_path}"
            if full_url not in sitemap_candidates:
                sitemap_candidates.append(full_url)

        for sm_url in sitemap_candidates:
            try:
                sm_resp = requests.get(sm_url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
                if sm_resp.status_code != 200 or not sm_resp.text.strip():
                    continue
                content_type = sm_resp.headers.get("Content-Type", "").lower()
                text_start = sm_resp.text.lstrip()[:300].lower()
                is_valid_xml = (
                    "xml" in content_type
                    or text_start.startswith("<?xml")
                    or "<urlset" in text_start
                    or "<sitemapindex" in text_start
                )
                if is_valid_xml:
                    meta["sitemap_present"] = True
                    (CACHE_DIR / "sitemap.xml").write_text(sm_resp.text, encoding="utf-8")
                    break
            except requests.RequestException:
                continue

        return meta

    def is_allowed(self, url: str, user_agent: str = "*") -> bool:
        try:
            return self.robot_parser.can_fetch(user_agent, url)
        except Exception:
            return True

    def extract_internal_links(self, html: str) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        discovered = set()
        parsed_root = urlparse(self.domain_root)

        for a_tag in soup.find_all("a", href=True):
            href = a_tag.get("href", "").strip()
            if not href or href.startswith(("mailto:", "tel:", "javascript:", "data:")):
                continue
            full_url = normalize_url(urljoin(self.domain_root, href))
            parsed_url = urlparse(full_url)
            if parsed_url.netloc == parsed_root.netloc and parsed_url.scheme == "https":
                if self.is_allowed(full_url):
                    discovered.add(full_url)
        return sorted(discovered)

    def _read_sitemap_urls(self) -> list[str]:
        sitemap_path = CACHE_DIR / "sitemap.xml"
        if not sitemap_path.exists():
            return []
        try:
            root = ET.fromstring(sitemap_path.read_text(encoding="utf-8"))
        except (ET.ParseError, OSError):
            return []

        urls = []
        for element in root.iter():
            if element.tag.endswith("loc") and element.text:
                normalized = normalize_url(element.text.strip())
                if urlparse(normalized).netloc == urlparse(self.domain_root).netloc:
                    if self.is_allowed(normalized):
                        urls.append(normalized)
        return sorted(set(urls))

    def sample_urls(self, candidates: Iterable[str]) -> list[str]:
        """Choose a deterministic, representative sample rather than first-N URLs."""
        unique = []
        seen = set()
        for raw in candidates:
            normalized = normalize_url(raw)
            if normalized == self.target_url or normalized in seen:
                continue
            parsed = urlparse(normalized)
            if parsed.netloc != urlparse(self.domain_root).netloc or parsed.scheme != "https":
                continue
            seen.add(normalized)
            unique.append(normalized)

        # "home" is included here as a defensive backstop, not because we
        # ever want to sample it: classify_url() can legitimately return
        # "home" for a candidate URL (e.g. a stray homepage link that
        # survives the self-exclusion check above in some edge case), and
        # buckets[classify_url(url)] must never KeyError regardless. It gets
        # no quota below, so any such URL is collected but never selected.
        buckets = {k: [] for k in ("home", "product", "category", "location", "article", "search", "other")}
        for url in unique:
            buckets[classify_url(url)].append(url)

        # Prefer page types that reveal different audit surfaces.
        quota = {
            "product": 3,
            "category": 2,
            "location": 2,
            "article": 2,
            "search": 1,
            "other": 2,
        }
        selected = []
        for kind, limit in quota.items():
            selected.extend(buckets[kind][:limit])

        if len(selected) < self.max_pages:
            leftovers = [u for u in unique if u not in set(selected)]
            selected.extend(leftovers[: self.max_pages - len(selected)])

        return selected[: max(0, self.max_pages - 1)]

    def _evaluate_subdomain_pivot(self, html: str) -> str | None:
        """
        Detect if the current homepage is a routing portal with few local links,
        and identify if a specific same-base subdomain should be crawled instead.

        Two different patterns both count as "pivot-worthy":
          1. Diffuse fan-out - many distinct subdomains each with only a
             link or two (e.g. Wikipedia's ~300 per-language subdomains,
             each linked once from the www.wikipedia.org portal).
          2. A dominant cluster - one other subdomain clearly has more links
             than any other (e.g. a blog/shop subdomain holding all the real
             content: blog.example.com linked 20x from a bare www.example.com).

        ORDERING (this was the actual Wikipedia bug): fan-out is now checked
        BEFORE the current-page self-link gate, not after it. Previously the
        gate ("does the current page already have >= MIN_SELF_LINKS_TO_SKIP_PIVOT
        self-links? if so, it's real content, don't pivot") ran first and
        short-circuited everything else. That's a reasonable question for an
        ordinary homepage, but wrong for a portal: www.wikipedia.org has 4
        self-links (logo, ToS, privacy policy - boilerplate) sitting right next
        to ~272 distinct language subdomains. 4 >= 3 tripped the gate and the
        fan-out signal - which was computed correctly the whole time - never
        even got read. No ordinary site links to
        MIN_DISTINCT_SUBDOMAINS_FOR_FANOUT+ distinct same-base subdomains from
        its homepage; only a genuine portal does, regardless of how many
        footer/legal links it also has to itself. So fan-out breadth is checked
        on its own first, independent of current_count, and only if that
        doesn't qualify does the self-link gate get a say (protecting the
        dominant-cluster case from pivoting away from a page that already has
        plenty of its own real content).

        Diagnostics are stashed on self.last_pivot_diagnostics either way,
        so build_cache() can surface *why* a pivot did or didn't happen -
        useful for tuning the thresholds above against real sites.
        """
        soup = BeautifulSoup(html, "html.parser")
        parsed_root = urlparse(self.domain_root)
        current_netloc = parsed_root.netloc

        # Naive base domain extraction (strips www.)
        base_domain = current_netloc[4:] if current_netloc.startswith("www.") else current_netloc

        netloc_counts = {}
        for a_tag in soup.find_all("a", href=True):
            href = a_tag.get("href", "").strip()
            if not href or href.startswith(("mailto:", "tel:", "javascript:", "data:")):
                continue

            full_url = normalize_url(urljoin(self.target_url, href))
            parsed_url = urlparse(full_url)

            if parsed_url.scheme != "https":
                continue

            net = parsed_url.netloc
            # Only count links sharing the base domain
            if net != base_domain and not net.endswith("." + base_domain):
                continue

            label = net[: -(len(base_domain) + 1)] if net != base_domain else ""
            if label in INFRA_SUBDOMAIN_LABELS:
                continue

            netloc_counts[net] = netloc_counts.get(net, 0) + 1

        current_count = netloc_counts.get(current_netloc, 0)
        other_counts = {k: v for k, v in netloc_counts.items() if k != current_netloc}
        diagnostics = {
            "current_netloc": current_netloc,
            "current_netloc_links": current_count,
            "other_netloc_counts": other_counts,
        }

        # --- Pattern 1: diffuse fan-out, checked FIRST and independent of
        # current_count. See the ordering note in the docstring above for why.
        is_diffuse_fanout = len(other_counts) >= MIN_DISTINCT_SUBDOMAINS_FOR_FANOUT
        if is_diffuse_fanout:
            best_netloc = max(other_counts, key=other_counts.get)
            diagnostics["decision"] = f"pivot_to::{best_netloc}"
            diagnostics["pivot_reason"] = "diffuse_fanout"
            self.last_pivot_diagnostics = diagnostics
            return f"https://{best_netloc}"

        # --- Pattern 2 gate: only an ordinary (non-portal) homepage with
        # plenty of its own content should skip pivoting outright. This runs
        # AFTER the fan-out check so it can no longer mask a portal's fan-out
        # behind a handful of boilerplate self-links.
        if current_count >= MIN_SELF_LINKS_TO_SKIP_PIVOT:
            diagnostics["decision"] = "no_pivot_sufficient_self_links"
            self.last_pivot_diagnostics = diagnostics
            return None

        # --- Pattern 2: dominant cluster - one other subdomain clearly wins.
        best_netloc, best_count = None, 0
        for net, count in other_counts.items():
            if count > best_count:
                best_netloc, best_count = net, count

        is_dominant_cluster = best_count >= DOMINANT_CLUSTER_MIN_LINKS and best_count > current_count

        if best_netloc and is_dominant_cluster:
            diagnostics["decision"] = f"pivot_to::{best_netloc}"
            diagnostics["pivot_reason"] = "dominant_cluster"
            self.last_pivot_diagnostics = diagnostics
            return f"https://{best_netloc}"

        diagnostics["decision"] = "no_pivot_no_qualifying_candidate"
        self.last_pivot_diagnostics = diagnostics
        return None

    def fetch_page(self, url: str, index: int) -> dict:
        filepath = PAGES_DIR / f"page_{index}.html"
        record = {
            "index": index,
            "url": url,
            "final_url": None,
            "status_code": 0,
            "content_type": None,
            "file": str(filepath.resolve()),
            "success": False,
            "content_classification": "network_error",
            "page_type": classify_url(url),
            "content_signature": None,
            "text_length": 0,
            "challenge_score": 0.0,
            "challenge_signals": [],
            "challenge_patterns": [],
        }
        try:
            resp = requests.get(
                url,
                headers=REQUEST_HEADERS,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )
            record["status_code"] = resp.status_code
            record["final_url"] = str(resp.url)
            record["content_type"] = resp.headers.get("Content-Type", "")

            # Extract the raw response text early for content checking
            html = resp.text or ""

            # Prevent false non_html flags by checking the actual body text if the header is missing/weird
            is_html_header = "html" in record["content_type"].lower()
            if not is_html_header and resp.status_code == 200:
                content_start = html[:1000].lower()
                if "<html" not in content_start and "<!doctype" not in content_start:
                    record["content_classification"] = "non_html"
                    return record

            filepath.write_text(html, encoding="utf-8")
            challenge = detect_challenge(html, resp.status_code, resp.headers)
            record["text_length"] = challenge["text_length"]
            record["challenge_score"] = challenge["score"]
            record["challenge_signals"] = challenge["signals"]
            record["challenge_patterns"] = challenge["matched_patterns"]
            record["content_signature"] = content_signature(page_text(html)) if html else None

            if challenge["is_challenge"]:
                record["content_classification"] = "bot_challenge"
            elif resp.status_code == 200 and looks_like_blocked_url(record["final_url"]):
                # Content-based challenge detection can miss a bot-defense
                # system that returns HTTP 200 with bland, non-matching body
                # text (e.g. Walmart's /blocked?url=... redirect target) —
                # the URL itself is still a reliable tell.
                record["content_classification"] = "bot_challenge"
            elif resp.status_code in (401, 407):
                record["content_classification"] = "auth_wall"
            elif resp.status_code in (403, 429):
                record["content_classification"] = "blocked"
            elif resp.status_code >= 500:
                record["content_classification"] = "server_error"
            elif resp.status_code >= 400:
                record["content_classification"] = "http_error"
            elif resp.status_code == 200:
                record["content_classification"] = "normal"
                record["success"] = True
            else:
                record["content_classification"] = "unexpected_http"

        except requests.RequestException as exc:
            record["error"] = str(exc)
        return record

    def _fetch_many(self, urls: list[str], start_index: int) -> list[dict]:
        if not urls:
            return []
        if self.crawl_delay > 0 or len(urls) == 1:
            records = []
            for offset, url in enumerate(urls):
                if offset:
                    time.sleep(self.crawl_delay)
                records.append(self.fetch_page(url, start_index + offset))
            return records

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(urls))) as executor:
            futures = [
                executor.submit(self.fetch_page, url, start_index + i)
                for i, url in enumerate(urls)
            ]
            return [future.result() for future in futures]

    def build_cache(self) -> str:
        self.reset_cache_dir()
        meta = self.fetch_and_parse_robots()

        homepage_record = self.fetch_page(self.target_url, 0)

        # --- Subdomain Pivot Logic ---
        pivot_diagnostics = None
        homepage_path = PAGES_DIR / "page_0.html"
        if homepage_record["success"] and homepage_path.exists():
            html = homepage_path.read_text(encoding="utf-8")
            pivot_root = self._evaluate_subdomain_pivot(html)
            pivot_diagnostics = self.last_pivot_diagnostics

            if pivot_root:
                # Pivot to the dominant subdomain. target_url MUST go through
                # normalize_url() here, same as every discovered link does -
                # otherwise this ends up as "https://en.wikipedia.org" (no
                # trailing slash) while a link back to that same homepage
                # normalizes to "https://en.wikipedia.org/", the two never
                # string-compare equal in sample_urls()'s self-exclusion
                # check, and that stray homepage link slips through
                # classify_url() as "home" - a bucket sample_urls() doesn't
                # define, causing a KeyError('home') crash.
                self.target_url = normalize_url(pivot_root)
                self.domain_root = pivot_root

                # Re-fetch robots.txt under the new subdomain's rules
                meta = self.fetch_and_parse_robots()

                # Re-fetch the new target as the authoritative page_0
                homepage_record = self.fetch_page(self.target_url, 0)

        # meta may have been replaced by the re-fetch above; attach the
        # pivot diagnostics afterward either way so it's always visible in
        # cache_index.json, whether or not a pivot actually happened.
        if pivot_diagnostics is not None:
            meta["subdomain_pivot"] = pivot_diagnostics
        # ----------------------------------

        pages_manifest = [homepage_record]

        # The homepage can be challenged even with HTTP 200; that is not a normal crawl.
        if homepage_record["content_classification"] == "bot_challenge":
            meta["crawl_status"] = "blocked_by_waf"
            meta["failure_reason"] = "Homepage returned bot-challenge/interstitial content"
        elif homepage_record["content_classification"] == "rate_limited":
            meta["crawl_status"] = "rate_limited"
        elif homepage_record["content_classification"] in {"blocked", "server_error"}:
            meta["crawl_status"] = "blocked_by_waf"
            meta["failure_reason"] = (
                f"Homepage returned HTTP {homepage_record['status_code']} "
                f"({homepage_record['content_classification']})"
            )
        elif not homepage_record["success"]:
            meta["crawl_status"] = "failed_root_fetch"
            meta["failure_reason"] = homepage_record.get("error", "Unknown fetch error")

        candidates = []

        # 1. SITEMAP FIRST: Prioritize canonical, machine-readable syndication
        candidates.extend(self._read_sitemap_urls())

        # 2. DOM SUPPLEMENT: Fall back to homepage links to catch orphaned pages
        # Re-assign homepage_path just in case it wasn't captured above
        homepage_path = PAGES_DIR / "page_0.html"
        if homepage_record["success"] and homepage_path.exists():
            candidates.extend(
                self.extract_internal_links(homepage_path.read_text(encoding="utf-8"))
            )

        # 3. SAMPLE: The deduplication will now favor the clean sitemap URLs
        urls_to_fetch = self.sample_urls(candidates)

        if urls_to_fetch:
            pages_manifest.extend(self._fetch_many(urls_to_fetch, 1))

        pages_manifest.sort(key=lambda x: x["index"])

        counts = {}
        for page in pages_manifest:
            kind = page.get("content_classification", "unknown")
            counts[kind] = counts.get(kind, 0) + 1

        usable_pages = [p for p in pages_manifest if p.get("content_classification") == "normal"]
        challenge_pages = [p for p in pages_manifest if p.get("content_classification") == "bot_challenge"]

        # Distinguish partial evidence from total failure.
        if usable_pages:
            if challenge_pages:
                meta["crawl_status"] = "partial" if meta.get("crawl_status") == "success" else meta["crawl_status"]
        elif challenge_pages:
            meta["crawl_status"] = "blocked_by_waf"
            meta["failure_reason"] = (
                "No normal HTML pages were retrieved; sampled URLs returned bot-challenge content."
            )

        meta.update({
            "pages_requested": len(pages_manifest),
            "pages_usable": len(usable_pages),
            "pages_challenge": len(challenge_pages),
            "pages_by_classification": counts,
            "usable_page_ratio": round(len(usable_pages) / max(1, len(pages_manifest)), 3),
            "challenge_ratio": round(len(challenge_pages) / max(1, len(pages_manifest)), 3),
            "page_type_counts": {
                page_type: sum(p.get("page_type") == page_type for p in pages_manifest)
                for page_type in {p.get("page_type") for p in pages_manifest}
                if page_type
            },
        })

        index_payload = {
            "site": self.target_url,
            "domain_root": self.domain_root,
            "cache_dir": str(CACHE_DIR.resolve()),
            "meta": meta,
            "pages": pages_manifest,
        }
        (CACHE_DIR / "cache_index.json").write_text(
            json.dumps(index_payload, indent=2), encoding="utf-8"
        )
        return str(CACHE_DIR.resolve())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Target URL to audit")
    parser.add_argument("--max-pages", type=int, default=MAX_PAGES)
    args = parser.parse_args()
    crawler = SiteCrawler(args.url, max_pages=args.max_pages)
    print(crawler.build_cache())