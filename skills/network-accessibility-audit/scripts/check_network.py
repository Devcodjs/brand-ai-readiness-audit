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

    # 1. EXPLICIT DISALLOWAL (Evaluated independently of WAF crawl_status)
    if blocked_search:
        # If 5 or more primary bots are blocked, it is almost certainly a deliberate strategy
        is_total_block = len(blocked_search) >= 5
        severity = "critical" if is_total_block else "high"
        
        if is_total_block:
            evidence_str = (
                f"robots.txt explicitly disallows ALL tracked AI search/retrieval agents "
                f"({', '.join(blocked_search)}). This prevents live zero-shot retrieval."
            )
            action_summary = (
                "This block prevents future crawling but does not erase past training data. AI systems will still answer "
                "questions about your brand using stale memory or third-party mentions, but you permanently lose the ability "
                "to correct outdated information or earn referral citations. If this is a deliberate strategy to protect a data moat "
                "(e.g., forcing API licensing), no action is needed. However, if accurate self-representation matters, "
                "removing these specific retrieval-bot disallows is required to restore live AI discoverability."
            )
        else:
            evidence_str = (
                f"robots.txt explicitly disallows specific AI search/retrieval agents: "
                f"{', '.join(blocked_search)}."
            )
            action_summary = (
                "Review robots.txt rules for AI/search crawlers. Ensure you are not accidentally blocking specific "
                "discovery engines (like OAI-SearchBot or PerplexityBot) due to legacy security templates. Blocking these bots "
                "forces them to rely on stale training data or third-party mentions rather than your live site."
            )

        findings.append({
            "id": "NET-ACCESS-SEARCH-BLOCKED",
            "title": "AI Search/Retrieval Bots Blocked by robots.txt",
            "severity": severity,
            "evidence": evidence_str,
            "suggested_action": {
                "summary": action_summary,
                "priority": severity
            }
        })

    # 2. WAF / CAPTCHA INTERCEPTION (Medium/High severity: Technical barrier)
    if crawl_status == "blocked_by_waf":
        findings.append({
            "id": "NET-ACCESS-WAF-INTERCEPT",
            "title": "Homepage Access Intercepted by WAF/CAPTCHA",
            "severity": "high",
            "evidence": f"GET request to the root URL returned HTTP 403 or challenge. Reason: {failure_reason}.",
            "suggested_action": {
                "summary": "If direct AI retrieval is desired, verify that your Web Application Firewall (e.g., Cloudflare, Akamai) is configured to permit requests from verified AI search agents via their published IP ranges.",
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
    # Only award this if search bots are intentionally left open
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
    is_blocked = crawl_status in ("blocked_by_waf", "rate_limited", "failed_root_fetch") or bool(blocked_search)
    
    if is_blocked and not sitemap_present:
        findings.append({
            "id": "NET-DISC-NONE-OBSERVED",
            "title": "Public Syndication Surface Not Observed",
            "severity": "medium",
            "evidence": "Direct crawl or AI retrieval is restricted, and standard public discovery endpoints (e.g., robots.txt sitemap declarations or standard /sitemap.xml paths) were inaccessible or absent.",
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