import os
import shutil
import json
import time
import argparse
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse, urljoin
import urllib.robotparser
import concurrent.futures
import requests
from bs4 import BeautifulSoup

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

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CACHE_DIR = REPOSITORY_ROOT / "audit-cache"
PAGES_DIR = CACHE_DIR / "pages"

# Bots used for scraping data to train massive LLMs
TRAINING_BOTS = [
    "GPTBot",           
    "Google-Extended",  
    "CCBot",            
    "Anthropic-ai",     
    "Amazonbot",        
    "FacebookBot",      
]

# Bots used for real-time conversational search and RAG
SEARCH_BOTS = [
    "OAI-SearchBot",    
    "ChatGPT-User",     
    "PerplexityBot",    
    "ClaudeBot",        
]

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
        """Fetches robots.txt and sitemaps using prioritized candidate resolution."""
        meta = {
            "robots_present": False,
            "sitemap_present": False,
            "crawl_delay": 0.0,
            "blocked_training_bots": [],
            "blocked_search_bots": [],
            "crawl_status": "success",
            "failure_reason": None,
        }

        sitemap_candidates = []

        # 1. Fetch robots.txt
        try:
            robots_resp = requests.get(
                f"{self.domain_root}/robots.txt",
                headers=REQUEST_HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            if robots_resp.status_code == 200:
                meta["robots_present"] = True
                (CACHE_DIR / "robots.txt").write_text(robots_resp.text, encoding="utf-8")
                self.robot_parser.parse(robots_resp.text.splitlines())

                # Explicit sitemaps declared in robots.txt take highest priority
                explicit_sitemaps = self.robot_parser.site_maps() or []
                sitemap_candidates.extend(explicit_sitemaps)

                for bot in TRAINING_BOTS:
                    if not self.robot_parser.can_fetch(bot, f"{self.domain_root}/"):
                        meta["blocked_training_bots"].append(bot)

                for bot in SEARCH_BOTS:
                    if not self.robot_parser.can_fetch(bot, f"{self.domain_root}/"):
                        meta["blocked_search_bots"].append(bot)

                delay = self.robot_parser.crawl_delay("*")
                if delay:
                    self.crawl_delay = min(float(delay), MAX_ALLOWED_DELAY)
                    meta["crawl_delay"] = self.crawl_delay
        except Exception:
            pass

        # 2. Append standard fallback paths (deduplicating against explicit declarations)
        for sm_path in ["/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml"]:
            full_url = f"{self.domain_root}{sm_path}"
            if full_url not in sitemap_candidates:
                sitemap_candidates.append(full_url)

        # 3. Unified fetch loop: stop on first valid XML payload
        for sm_url in sitemap_candidates:
            try:
                sm_resp = requests.get(
                    sm_url,
                    headers=REQUEST_HEADERS,
                    timeout=REQUEST_TIMEOUT,
                )
                if sm_resp.status_code == 200 and sm_resp.text.strip():
                    # WAF Shield Check: Ensure the 200 OK is actually XML, not an HTML JS Challenge
                    content_type = sm_resp.headers.get("Content-Type", "").lower()
                    text_start = sm_resp.text.strip()[:100].lower()
                    
                    is_valid_xml = (
                        "xml" in content_type or 
                        text_start.startswith("<?xml") or 
                        "<urlset" in text_start or 
                        "<sitemapindex" in text_start
                    )

                    if is_valid_xml:
                        meta["sitemap_present"] = True
                        (CACHE_DIR / "sitemap.xml").write_text(sm_resp.text, encoding="utf-8")
                        break
            except Exception:
                continue

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
            "success": False,
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

        if len(meta["blocked_search_bots"]) >= 2:
            meta["crawl_status"] = "blocked_by_robots"
            meta["failure_reason"] = "robots disallow (Search/RAG bots explicitly blocked)"

        homepage_record = self.fetch_page(self.target_url, 0)
        pages_manifest = [homepage_record]
        urls_to_fetch = []

        if homepage_record["status_code"] == 403:
            meta["crawl_status"] = "blocked_by_waf"
            meta["failure_reason"] = "403 WAF / CAPTCHA challenge"
        elif homepage_record["status_code"] in (429, 503):
            meta["crawl_status"] = "rate_limited"
            meta["failure_reason"] = f"HTTP {homepage_record['status_code']} Rate limit / Service Unavailable"
        elif not homepage_record["success"] and meta["crawl_status"] == "success":
            meta["crawl_status"] = "failed_root_fetch"
            meta["failure_reason"] = homepage_record.get("error", "Unknown fetch error")

        # Discovery Branch 1: DOM Extraction (Happy Path)
        if meta["crawl_status"] == "success" and homepage_record["success"]:
            html_content = (PAGES_DIR / "page_0.html").read_text(encoding="utf-8")
            discovered = self.extract_internal_links(html_content)
            urls_to_fetch = [u for u in discovered if u != self.target_url][: MAX_PAGES - 1]

        # Discovery Branch 2: XML Sitemap Fallback (WAF / Blocked Route)
        elif meta["crawl_status"] != "success" and meta["sitemap_present"]:
            sitemap_path = CACHE_DIR / "sitemap.xml"
            if sitemap_path.exists():
                try:
                    xml_text = sitemap_path.read_text(encoding="utf-8")
                    root = ET.fromstring(xml_text)
                    discovered = [e.text for e in root.iter() if e.tag.endswith('loc') and e.text]
                    urls_to_fetch = [u for u in discovered if u != self.target_url][: MAX_PAGES - 1]
                except ET.ParseError:
                    pass

        # Execute fetch for discovered URLs
        if urls_to_fetch:
            if self.crawl_delay > 0:
                for i, url in enumerate(urls_to_fetch):
                    time.sleep(self.crawl_delay)
                    pages_manifest.append(self.fetch_page(url, i + 1))
            else:
                with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                    futures = [
                        executor.submit(self.fetch_page, url, i + 1)
                        for i, url in enumerate(urls_to_fetch)
                    ]
                    for future in concurrent.futures.as_completed(futures):
                        pages_manifest.append(future.result())

            # Evaluate if the sitemap fallback successfully bypassed the WAF for sub-pages
            successful_pages = [p for p in pages_manifest if p["success"]]
            if successful_pages and meta["crawl_status"] != "success":
                meta["crawl_status"] = "success"
                meta["failure_reason"] = "Root blocked, but sub-pages successfully retrieved via XML syndication."

        pages_manifest.sort(key=lambda x: x["index"])

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
    args = parser.parse_args()
    crawler = SiteCrawler(args.url)
    print(crawler.build_cache())