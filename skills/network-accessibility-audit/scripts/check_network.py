import json
import argparse
import sys
from pathlib import Path

def audit_network_accessibility(url: str, cache_dir: str) -> list[dict]:
    findings = []
    index_file = Path(cache_dir) / "cache_index.json"
    
    if not index_file.exists():
        return []

    try:
        manifest = json.loads(index_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []

    meta = manifest.get("meta", {})
    crawl_status = meta.get("crawl_status", "unknown")
    failure_reason = meta.get("failure_reason", "Unknown network failure")
    sitemap_present = meta.get("sitemap_present", False)
    
    blocked_training = meta.get("blocked_training_bots", [])
    blocked_search = meta.get("blocked_search_bots", [])

    # 1. EXPLICIT DISALLOWAL (Highest severity: Brand intentionally opted out)
    if crawl_status == "blocked_by_robots":
        findings.append({
            "id": "NET-ACCESS-SEARCH-BLOCKED",
            "title": "Real-Time AI Search Bots Explicitly Blocked",
            "severity": "critical",
            "evidence": f"/robots.txt disallows real-time RAG agents. Blocked: {', '.join(blocked_search)}.",
            "suggested_action": {
                "summary": "If conversational search discoverability is a business objective, review robots.txt restrictions and consider allowlisting agents like OAI-SearchBot and PerplexityBot.",
                "priority": "critical"
            }
        })
    
    # 2. WAF / CAPTCHA INTERCEPTION (Medium/High severity: Technical barrier)
    elif crawl_status == "blocked_by_waf":
        findings.append({
            "id": "NET-ACCESS-WAF-INTERCEPT",
            "title": "Homepage Access Intercepted by WAF/CAPTCHA",
            "severity": "high",
            "evidence": f"GET request to the root URL returned HTTP 403. Reason: {failure_reason}.",
            "suggested_action": {
                "summary": "If direct AI retrieval is desired, verify that your Web Application Firewall (e.g., Cloudflare, Akamai) is configured to permit requests from verified AI search agents.",
                "priority": "high"
            }
        })
        
    # 3. RATE LIMITING / TIMEOUTS
    elif crawl_status == "rate_limited":
        findings.append({
            "id": "NET-ACCESS-RATE-LIMIT",
            "title": "Crawl Dropped via Timeout or Rate Limit",
            "severity": "medium",
            "evidence": f"Connection dropped or throttled. Reason: {failure_reason}.",
            "suggested_action": {
                "summary": "Server load-balancers or anti-scraping shields are dropping headless connections. Monitor traffic rules to ensure AI IP ranges are not inadvertently tarpitted.",
                "priority": "medium"
            }
        })

    # 4. TRAINING BOTS (Info: Good IP protection)
    if blocked_training and not blocked_search:
        findings.append({
            "id": "NET-ACCESS-TRAINING-BLOCKED",
            "title": "LLM Training Bots Blocked",
            "severity": "info",
            "evidence": f"/robots.txt blocks training scrapers ({', '.join(blocked_training)}) but permits real-time search bots.",
            "suggested_action": {
                "summary": "The current configuration successfully protects intellectual property from unauthorized LLM training while maintaining real-time search discoverability.",
                "priority": "low"
            }
        })

    # 5. SYNDICATION FALLBACK (Conditional severity based on access)
    is_blocked = crawl_status in ("blocked_by_waf", "rate_limited", "failed_root_fetch", "blocked_by_robots")
    
    if is_blocked and not sitemap_present:
        findings.append({
            "id": "NET-DISC-NONE-OBSERVED",
            "title": "Public Syndication Surface Not Observed",
            "severity": "medium",  # Lowered from critical to medium as per advice
            "evidence": "Direct crawl failed, and standard public discovery endpoints (e.g., robots.txt sitemap declarations or standard /sitemap.xml paths) were inaccessible or absent.",
            "suggested_action": {
                "summary": "Because direct visibility is restricted, public machine-readable discovery surfaces can improve retrievability. If you do not already expose other discovery mechanisms, consider making an XML sitemap publicly accessible.",
                "priority": "medium"
            }
        })

    return findings

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--cache-dir", required=True)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(audit_network_accessibility(args.url, args.cache_dir), indent=2))