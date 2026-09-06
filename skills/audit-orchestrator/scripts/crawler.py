import shutil
import json
import time
from pathlib import Path
from urllib.parse import urlparse, urljoin
import urllib.robotparser
import concurrent.futures
import requests
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
MAX_PAGES = 12
REQUEST_TIMEOUT = 10
MAX_ALLOWED_DELAY = 10.0

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CACHE_DIR = REPOSITORY_ROOT / "audit-cache"
PAGES_DIR = CACHE_DIR / "pages"

# Canonical list of major AI search and crawler bots
KNOWN_AI_BOTS = [
    "GPTBot",
    "ClaudeBot",
    "PerplexityBot",
    "CCBot",
    "Google-Extended",
    "ChatGPT-User",
    "Bytespider",
    "Amazonbot"
]


class RobotsBlockedException(Exception):
    """Raised when robots.txt bans major AI agents or universal crawlers."""
    pass


class WafBlockedException(Exception):
    """Raised when the origin server issues an anti-bot challenge (503/403)."""
    pass


class SiteCrawler:
    def __init__(self, target_url: str):
        self.target_url = target_url.strip()
        parsed = urlparse(self.target_url)
        self.domain_root = f"{parsed.scheme}://{parsed.netloc}"
        
        self.robot_parser = urllib.robotparser.RobotFileParser()
        self.robot_parser.set_url(f"{self.domain_root}/robots.txt")
        self.crawl_delay = 0.0

    def reset_cache_dir(self):
        if CACHE_DIR.exists():
            shutil.rmtree(CACHE_DIR)
        PAGES_DIR.mkdir(parents=True, exist_ok=True)

    def fetch_and_parse_robots(self) -> dict:
        meta = {
            "robots_present": False,
            "sitemap_present": False,
            "crawl_delay": 0,
            "blocked_ai_bots": [],
        }
        
        # 1. Fetch robots.txt
        try:
            resp = requests.get(f"{self.domain_root}/robots.txt", headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                meta["robots_present"] = True
                (CACHE_DIR / "robots.txt").write_text(resp.text, encoding="utf-8")
                self.robot_parser.parse(resp.text.splitlines())

                # Check which major AI crawlers are explicitly blocked at the root
                for bot in KNOWN_AI_BOTS:
                    if not self.robot_parser.can_fetch(bot, f"{self.domain_root}/"):
                        meta["blocked_ai_bots"].append(bot)

                delay = self.robot_parser.crawl_delay("*")
                if delay:
                    self.crawl_delay = min(float(delay), MAX_ALLOWED_DELAY)
                    meta["crawl_delay"] = self.crawl_delay
        except Exception:
            pass

        # 2. Fetch sitemap.xml
        try:
            resp = requests.get(f"{self.domain_root}/sitemap.xml", headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                meta["sitemap_present"] = True
                (CACHE_DIR / "sitemap.xml").write_text(resp.text, encoding="utf-8")
        except Exception:
            pass

        return meta

    def is_allowed(self, url: str) -> bool:
        try:
            return self.robot_parser.can_fetch("*", url)
        except Exception:
            return True

    def extract_internal_links(self, html: str) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        discovered = set()
        parsed_root = urlparse(self.domain_root)

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].split("#")[0].strip()
            if not href:
                continue
            full_url = urljoin(self.domain_root, href)
            parsed_url = urlparse(full_url)
            
            if parsed_url.netloc == parsed_root.netloc and parsed_url.scheme == "https":
                if self.is_allowed(full_url):
                    discovered.add(full_url)
            if len(discovered) >= MAX_PAGES * 2:
                break

        return list(discovered)

    def fetch_page(self, url: str, index: int) -> dict:
        filename = f"page_{index}.html"
        filepath = PAGES_DIR / filename
        record = {
            "index": index,
            "url": url,
            "status_code": 0,
            "file": str(filepath.resolve()),
            "success": False
        }
        try:
            resp = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
            record["status_code"] = resp.status_code
            if resp.status_code == 200:
                filepath.write_text(resp.text, encoding="utf-8")
                record["success"] = True
        except Exception as e:
            record["error"] = str(e)

        return record

    def build_cache(self) -> str:
        self.reset_cache_dir()
        meta = self.fetch_and_parse_robots()

        # Enforce check 1: Halt if major AI bots are explicitly banned
        if len(meta["blocked_ai_bots"]) >= 3:
            raise RobotsBlockedException(
                f"robots.txt disallows AI indexing. Blocked agents: {', '.join(meta['blocked_ai_bots'])}"
            )

        # Enforce check 2: Check homepage for anti-bot WAF status codes
        homepage_record = self.fetch_page(self.target_url, 0)
        if homepage_record["status_code"] in (403, 503):
            raise WafBlockedException(
                f"Origin returned HTTP {homepage_record['status_code']} (Anti-Bot / WAF Challenge) on root fetch."
            )

        pages_manifest = [homepage_record]
        urls_to_fetch = []

        if homepage_record["success"]:
            html_content = (PAGES_DIR / "page_0.html").read_text(encoding="utf-8")
            discovered = self.extract_internal_links(html_content)
            urls_to_fetch = [u for u in discovered if u != self.target_url][: MAX_PAGES - 1]

        if self.crawl_delay > 0:
            for i, url in enumerate(urls_to_fetch):
                time.sleep(self.crawl_delay)
                pages_manifest.append(self.fetch_page(url, i + 1))
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                futures = [executor.submit(self.fetch_page, url, i + 1) for i, url in enumerate(urls_to_fetch)]
                for future in concurrent.futures.as_completed(futures):
                    pages_manifest.append(future.result())

        pages_manifest.sort(key=lambda x: x["index"])

        index_payload = {
            "site": self.target_url,
            "domain_root": self.domain_root,
            "cache_dir": str(CACHE_DIR.resolve()),
            "meta": meta,
            "pages": pages_manifest
        }
        (CACHE_DIR / "cache_index.json").write_text(json.dumps(index_payload, indent=2), encoding="utf-8")
        return str(CACHE_DIR.resolve())