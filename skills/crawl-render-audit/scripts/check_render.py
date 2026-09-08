import sys
import json
import argparse
import os
import urllib.robotparser
import requests
from bs4 import BeautifulSoup

AI_BOTS = ["GPTBot", "ClaudeBot", "PerplexityBot", "Google-Extended"]

def audit_crawl_and_syndication(target_url: str, cache_dir: str = "audit-cache") -> list:
    findings = []
    domain = "/".join(target_url.split("/")[:3])
    
    # 1. Check robots.txt (Local Cache First, Fallback to Live Network)
    is_ai_bot_blocked = False
    blocked_bots = []
    rp = urllib.robotparser.RobotFileParser()
    
    cached_robots = os.path.join(cache_dir, "robots.txt")
    if os.path.exists(cached_robots):
        try:
            with open(cached_robots, "r", encoding="utf-8") as f:
                rp.parse(f.readlines())
        except Exception:
            pass
    else:
        try:
            rp.set_url(f"{domain}/robots.txt")
            rp.read()
        except Exception:
            pass # Handle missing or unparseable robots.txt gracefully

    for bot in AI_BOTS:
        if not rp.can_fetch(bot, target_url):
            blocked_bots.append(bot)
            
    if blocked_bots:
        is_ai_bot_blocked = True

    # 2. Inspect Raw HTML for Feed Syndication (Local Cache First, Fallback to Live Network)
    has_merchant_feeds = False
    feed_types_found = []
    html_content = ""
    
    cached_html = os.path.join(cache_dir, "index.html")
    if os.path.exists(cached_html):
        try:
            with open(cached_html, "r", encoding="utf-8") as f:
                html_content = f.read()
        except Exception:
            pass
    else:
        try:
            resp = requests.get(target_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            html_content = resp.text
        except Exception:
            pass

    if html_content:
        soup = BeautifulSoup(html_content, "html.parser")
        
        # Search for RSS, Atom, and OpenSearch tags in <head>
        feed_tags = soup.find_all("link", attrs={
            "type": [
                "application/rss+xml", 
                "application/atom+xml", 
                "application/opensearchdescription+xml"
            ]
        })
        if feed_tags:
            has_merchant_feeds = True
            feed_types_found = list({tag.get("type") for tag in feed_tags if tag.get("type")})

    # 3. Apply Conditional Diagnostic Logic (Solving the "Amazon Problem")
    if is_ai_bot_blocked and not has_merchant_feeds:
        findings.append({
            "id": "DISC-CRIT-001",
            "title": "Complete AI Invisibility",
            "severity": "critical",
            "evidence": f"robots.txt disallows AI crawlers ({', '.join(blocked_bots)}) and zero RSS/merchant syndication feeds were detected.",
            "suggested_action": {
                "summary": "Update robots.txt to permit AI crawlers or publish structured product feeds/sitemaps.",
                "priority": "critical"
            }
        })
    elif is_ai_bot_blocked and has_merchant_feeds:
        findings.append({
            "id": "DISC-LOW-002",
            "title": "Direct AI Crawling Blocked (Syndication Active)",
            "severity": "low",
            "evidence": f"AI crawlers are disallowed in robots.txt, but active feed channels ({', '.join(feed_types_found)}) were detected.",
            "suggested_action": {
                "summary": "Ensure your merchant feeds and product sitemaps are submitted to major commercial indexes so AI shopping assistants can index your inventory.",
                "priority": "low"
            }
        })
    else:
        findings.append({
            "id": "DISC-PASS-000",
            "title": "Direct AI Crawling Allowed",
            "severity": "none",
            "evidence": "Verified that robots.txt permits standard AI assistant user-agents.",
            "suggested_action": None
        })

    return findings

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--cache-dir", default="audit-cache")
    args = parser.parse_args()
    
    results = audit_crawl_and_syndication(args.url, cache_dir=args.cache_dir)
    print(json.dumps(results, indent=2))