import argparse
import copy
import json
import re
import sys
import urllib.parse
from pathlib import Path
from bs4 import BeautifulSoup
import requests
from urllib.parse import urljoin, urlparse

def load_cached_pages(cache_dir: str) -> list:
    index_path = Path(cache_dir) / "cache_index.json"
    if not index_path.exists():
        return []
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    pages = []
    for page in index.get("pages", []):
        if page.get("content_classification") == "bot_challenge":
            continue
        file_path = page.get("file")
        if file_path and Path(file_path).exists():
            try:
                html = Path(file_path).read_text(encoding="utf-8", errors="replace")
                soup = BeautifulSoup(html, "html.parser")
                pages.append({
                    "url": page.get("final_url") or page.get("url", ""),
                    "page_type": page.get("page_type", "other"),
                    "html": html,
                    "soup": soup
                })
            except Exception:
                continue
    return pages

def detect_site_typology(pages: list) -> str:
    commercial_signals = 0
    informational_signals = 0

    comm_patterns = re.compile(r'\b(cart|checkout|price|pricing|add to cart|order now|shipping|buy now|shop)\b', re.I)
    info_patterns = re.compile(r'\b(encyclopedia|wiki|citations?|references?|peer-reviewed|journal|articles?|documentation)\b', re.I)

    for page in pages:
        text = page["soup"].get_text(" ", strip=True)[:3000]
        if comm_patterns.search(text):
            commercial_signals += 1
        if info_patterns.search(text):
            informational_signals += 1

    if commercial_signals >= 2:
        return "commercial"
    if informational_signals > commercial_signals:
        return "informational"
    return "general"

def get_content_area(soup):
    for selector in ["main", "article", "[role='main']", "#content", "#bodyContent"]:
        el = soup.select_one(selector)
        if el:
            return el
    return soup.find("body") or soup

def check_ica_001(pages: list) -> dict | None:
    """Overlay & Interstitial Blockade Check"""
    overlay_patterns = re.compile(r'cookie|consent|gdpr|onetrust|cookiebot|cc-banner|cookie-notice|modal|popup|overlay|lightbox|newsletter|signup-modal|paywall|login-wall', re.I)
    found_overlays = []
    affected_pages = set()
    has_gated_wall = False
    
    for page in pages:
        soup = page['soup']
        for el in soup.find_all(True):
            style = el.get('style', '').lower()
            if any(h in style for h in ['display:none', 'visibility:hidden', 'opacity:0']) or el.has_attr('hidden'):
                continue

            cls = " ".join(el.get('class', []))
            id_val = el.get('id', '')
            match = overlay_patterns.search(cls) or overlay_patterns.search(id_val)
            
            if match or el.name == 'dialog':
                has_fixed = any(p in style for p in ['position:fixed', 'position:absolute'])
                if has_fixed or el.name == 'dialog':
                    is_gated = el.find('input', type='password') or re.search(r'paywall|login-wall|gated|premium-content', cls + " " + id_val, re.I)
                    if is_gated:
                        has_gated_wall = True
                    found_overlays.append(f"{el.name}.{cls.replace(' ', '.')[:40]}")
                    affected_pages.add(page['url'])
                    break
                
    if affected_pages:
        ratio = len(affected_pages) / len(pages)
        if has_gated_wall:
            severity = "critical" if ratio > 0.5 else "high"
        else:
            severity = "high" if ratio > 0.8 else "medium"
            
        return {
            "id": "ICA-OVERLAY-001",
            "title": "Overlay & Interstitial Blockade",
            "severity": severity,
            "evidence": f"Found statically positioned overlay elements on {len(affected_pages)}/{len(pages)} pages: {list(set(found_overlays))[:5]}.",
            "suggested_action": {
                "summary": (
                    "Full-viewport overlays obstruct automated parsers from reading initial text. "
                    "If removing the marketing pop-up isn't an option, configure your edge router (e.g., Cloudflare Workers) or server-side middleware to suppress the overlay component specifically when the user-agent matches a verified AI bot."
                ),
                "priority": severity
            }
        }
    return None

def check_ica_002(pages: list) -> dict | None:
    """Deep-Link Anchor Targets"""
    if not pages:
        return None
    
    low_anchor_pages = 0
    total_anchors = 0
    
    for page in pages:
        content = get_content_area(page['soup'])
        anchors = set()
        
        for tag in content.find_all(['h1', 'h2', 'h3', 'h4', 'section', 'article'], id=True):
            anchors.add(tag['id'])
        for tag in content.find_all(['h1', 'h2', 'h3', 'h4']):
            child_with_id = tag.find(id=True)
            if child_with_id:
                anchors.add(child_with_id['id'])

        total_anchors += len(anchors)
        if len(anchors) < 2:
            low_anchor_pages += 1
            
    avg = total_anchors / max(1, len(pages))
    ratio = low_anchor_pages / len(pages)
    
    if avg < 2.0 and ratio > 0.5:
        severity = "medium" if ratio > 0.8 else "low"
        return {
            "id": "ICA-ANCHOR-001",
            "title": "Sparse Deep-Link Anchor Targets",
            "severity": severity,
            "evidence": f"Pages average {avg:.1f} deep-linkable section anchors. {low_anchor_pages}/{len(pages)} sampled routes contain fewer than 2 targetable heading IDs.",
            "suggested_action": {
                "summary": "Configure your CMS, Markdown renderer, or rich-text editor to automatically generate id attributes from heading text (auto-slugging). This instantly enables deep-linking site-wide without manual data entry.",
                "priority": severity
            }
        }
    return None

def check_ica_003(pages: list) -> dict | None:
    """URL Parameter & Referral Context Handling"""
    if not pages:
        return None
        
    param_patterns = re.compile(r'URLSearchParams|location\.search|location\.hash|getParameter|queryString|utm_source|analytics', re.I)
    bundler_patterns = re.compile(r'gtm\.js|analytics\.js|googletagmanager|_next/static|bundle|chunk|app\.', re.I)
    
    has_params = 0
    has_modern_bundler = 0

    for page in pages:
        soup = page['soup']
        for script in soup.find_all('script'):
            if script.string and param_patterns.search(script.string):
                has_params += 1
                break
            src = script.get('src', '')
            if bundler_patterns.search(src):
                has_modern_bundler += 1
                break
                
    if has_params == 0 and has_modern_bundler == 0:
        severity = "high" if len(pages) > 5 else "medium"
        return {
            "id": "ICA-URLPARAM-001",
            "title": "No Referral Context Handling Detected",
            "severity": severity,
            "evidence": f"Scanned {len(pages)} pages: zero client-side referral/UTM parsing or modern application bundlers detected.",
            "suggested_action": {
                "summary": "Verify that edge routers or analytics configurations capture referral context so inbound AI users land on customized responses.",
                "priority": severity
            }
        }
    return None

def check_ica_004(pages: list, site_type: str) -> dict | None:
    """Above-Fold Information Density (Contextually Gated)"""
    if site_type == "informational" or not pages:
        return None
        
    trans_keywords = re.compile(r'\b(price|cost|\$|€|£|₹|buy|order|book|add to cart|in stock|get started|demo)\b', re.I)
    deficit_pages = 0
    evaluated_pages = 0

    for page in pages:
        if page.get("page_type") not in {"product", "category", "home", "other"}:
            continue

        soup = copy.copy(page['soup'])
        for s in soup(['script', 'style', 'noscript', 'header', 'nav']):
            s.decompose()
        
        body = soup.find('body')
        if not body:
            continue
            
        text = list(body.stripped_strings)
        if len(text) < 50:
            continue
            
        evaluated_pages += 1
        fold_idx = max(1, int(len(text) * 0.25))
        above_fold = " ".join(text[:fold_idx])
        below_fold = " ".join(text[fold_idx:])
        
        if not trans_keywords.search(above_fold) and trans_keywords.search(below_fold):
            deficit_pages += 1
            
    if evaluated_pages > 0:
        ratio = deficit_pages / evaluated_pages
        if ratio > 0.5:
            severity = "high" if ratio > 0.8 else "medium"
            return {
                "id": "ICA-TRANSACTIONAL-001",
                "title": "Above-Fold Transactional Content Deficit",
                "severity": severity,
                "evidence": f"{deficit_pages}/{evaluated_pages} commercial templates bury key transaction signals below the initial 25% text fold.",
                "suggested_action": {
                    "summary": "Ensure primary product availability, pricing, and main action items are positioned high in the initial DOM payload.",
                    "priority": severity
                }
            }
    return None

def check_ica_005(pages: list) -> dict | None:
    """Title Uniqueness & Specificity"""
    if not pages:
        return None
        
    titles = []
    missing = 0
    for page in pages:
        title_tag = page['soup'].find('title')
        if not title_tag or not title_tag.string or not title_tag.string.strip():
            missing += 1
        else:
            titles.append(title_tag.string.strip())
            
    title_counts = {}
    for t in titles:
        title_counts[t] = title_counts.get(t, 0) + 1
        
    dup_count = sum(v for v in title_counts.values() if v > 1)
    ratio = (dup_count + missing) / len(pages)
    
    if ratio > 0.3:
        if (missing / len(pages)) > 0.5:
            severity = "critical"
        else:
            severity = "high" if ratio > 0.7 else "medium"
            
        return {
            "id": "ICA-TITLE-001",
            "title": "Missing or Duplicate Page Titles",
            "severity": severity,
            "evidence": f"{dup_count}/{len(pages)} sampled routes share duplicate <title> tags. {missing}/{len(pages)} lack titles entirely.",
            "suggested_action": {
                "summary": "Update your global <Head> component or SEO plugin to automatically generate titles and meta descriptions using dynamic variables (e.g., {{Product Name}} | {{Brand}}), and fallback to the first paragraph of text for the summary.",
                "priority": severity
            }
        }
    return None

def check_ica_006(pages: list) -> dict | None:
    """Meta Descriptions and Open Graph Signals"""
    if not pages:
        return None
        
    meta_missing = 0
    for page in pages:
        soup = page['soup']
        desc = soup.find('meta', attrs={'name': re.compile(r'^description$', re.I)})
        og_desc = soup.find('meta', attrs={'property': 'og:description'})
        
        has_desc = (desc and desc.get('content') and len(desc.get('content').strip()) > 20) or \
                   (og_desc and og_desc.get('content') and len(og_desc.get('content').strip()) > 20)
        if not has_desc:
            meta_missing += 1
            
    ratio = meta_missing / len(pages)
    if ratio > 0.4:
        severity = "high" if ratio > 0.8 else "medium"
        return {
            "id": "ICA-META-001",
            "title": "Missing Meta Descriptions / Summary Tags",
            "severity": severity,
            "evidence": f"{meta_missing}/{len(pages)} pages lack static <meta name='description'> or Open Graph description summaries.",
            "suggested_action": {
                "summary": "Publish descriptive meta summaries to guide snippet generation and grounding across AI interfaces.",
                "priority": severity
            }
        }
    return None

def check_ica_007(pages: list, site_type: str) -> dict | None:
    """Actionable Next Steps / CTAs"""
    if not pages:
        return None
        
    if site_type == "informational":
        action_patterns = re.compile(r'\b(read|view|cite|source|reference|download|explore|learn more|edit|share)\b', re.I)
    else:
        action_patterns = re.compile(r'\b(buy|order|book|contact|get started|sign up|learn more|try|subscribe|add to cart|shop|enroll|explore)\b', re.I)
    
    no_cta_pages = 0
    for page in pages:
        content = get_content_area(page['soup'])
        ctas = content.find_all(['a', 'button'])
        has_cta = any(action_patterns.search(cta.get_text(strip=True)) for cta in ctas)
        if not has_cta:
            no_cta_pages += 1
            
    ratio = no_cta_pages / len(pages)
    if ratio > 0.5:
        if site_type == "commercial":
            severity = "high" if ratio > 0.8 else "medium"
        else:
            severity = "medium" if ratio > 0.8 else "low"
            
        return {
            "id": "ICA-CTA-001",
            "title": "Sparse Action Pathways in Body Content",
            "severity": severity,
            "evidence": f"{no_cta_pages}/{len(pages)} pages contain zero clear action-oriented links or buttons in their main content area.",
            "suggested_action": {
                "summary": "Embed contextual navigational and conversion links in primary content to provide clear follow-through actions for users guided by AI engines.",
                "priority": severity
            }
        }
    return None

def check_ica_008(pages: list, site_type: str) -> dict | None:
    """Primary Conversion Endpoints (Commercial Domains Only)"""
    if site_type == "informational" or not pages:
        return None
        
    conv_elements = 0
    conv_patterns = re.compile(r'contact|book|schedule|quote|cart|checkout|register|signup', re.I)
    
    for page in pages:
        soup = page['soup']
        tel_links = soup.find_all('a', href=re.compile(r'^tel:'))
        mailto_links = soup.find_all('a', href=re.compile(r'^mailto:'))
        forms = [f for f in soup.find_all('form') if 'search' not in (f.get('action') or '').lower() and 'search' not in (f.get('class') or [])]
        action_links = [a for a in soup.find_all('a', href=True) if conv_patterns.search(a.get('href', ''))]
        conv_elements += len(tel_links) + len(mailto_links) + len(forms) + len(action_links)
        
    if conv_elements == 0:
        severity = "critical" if len(pages) >= 5 else "high"
        return {
            "id": "ICA-CONVERSION-001",
            "title": "No Clear Conversion Endpoints Found",
            "severity": severity,
            "evidence": f"Scanned {len(pages)} pages: found 0 interactive conversion paths (checkout, lead forms, or contact links).",
            "suggested_action": {
                "summary": "Provide a clear, persistent path to conversion (inquiry form, store link, or checkout) so AI models can direct actionable customer intent.",
                "priority": severity
            }
        }
    return None

def check_ica_009(pages: list) -> dict | None:
    """Breadcrumb Taxonomy Navigation"""
    if len(pages) <= 1:
        return None
        
    missing_breadcrumbs = 0
    non_home_pages = 0
    
    for page in pages:
        parsed = urllib.parse.urlparse(page['url'])
        if parsed.path in ['', '/'] and not parsed.query:
            continue
            
        non_home_pages += 1
        soup = page['soup']
        has_breadcrumb = False
        
        for nav in soup.find_all(['nav', 'div', 'ol', 'ul']):
            aria = (nav.get('aria-label') or '').lower()
            cls = ' '.join(nav.get('class', [])).lower()
            if 'breadcrumb' in aria or 'breadcrumb' in cls or nav.get('itemtype') == 'https://schema.org/BreadcrumbList':
                has_breadcrumb = True
                break
                
        if not has_breadcrumb:
            for script in soup.find_all('script', type='application/ld+json'):
                if script.string and 'BreadcrumbList' in script.string:
                    has_breadcrumb = True
                    break
                    
        if not has_breadcrumb:
            missing_breadcrumbs += 1
            
    if non_home_pages > 2:
        ratio = missing_breadcrumbs / non_home_pages
        if ratio > 0.5:
            severity = "medium" if ratio > 0.9 else "low"
            return {
                "id": "ICA-BREADCRUMB-001",
                "title": "Missing Breadcrumb Navigation",
                "severity": severity,
                "evidence": f"{missing_breadcrumbs}/{non_home_pages} sub-pages lack breadcrumb markers or BreadcrumbList structured data.",
                "suggested_action": {
                    "summary": "Add breadcrumb navigation and BreadcrumbList JSON-LD to sub-pages to clarify category taxonomy for AI crawlers.",
                    "priority": severity
                }
            }
    return None

def check_ica_010(pages: list) -> dict | None:
    """Contextual Internal Linking & Orphan Route Check"""
    if not pages:
        return None
        
    orphan_pages = 0
    for page in pages:
        parsed = urllib.parse.urlparse(page['url'])
        domain = parsed.netloc
        
        content = copy.copy(get_content_area(page['soup']))
        for el in content.find_all(['nav', 'footer', 'header']):
            el.decompose()
            
        internal_links = 0
        for link in content.find_all('a', href=True):
            href = link.get('href', '').strip()
            if href.startswith(('#', 'javascript:', 'tel:', 'mailto:')):
                continue
            href_parsed = urllib.parse.urlparse(href)
            if not href_parsed.netloc or href_parsed.netloc == domain:
                internal_links += 1
                
        if internal_links == 0:
            orphan_pages += 1
            
    ratio = orphan_pages / len(pages)
    if ratio >= 0.4:
        severity = "high" if ratio > 0.8 else "medium"
        return {
            "id": "ICA-ORPHAN-001",
            "title": "Low Internal Cross-Linking Density",
            "severity": severity,
            "evidence": f"{orphan_pages}/{len(pages)} pages contain 0 contextual internal links within their primary body text.",
            "suggested_action": {
                "summary": "Connect key content nodes with contextual in-body links to provide semantic traversal pathways for web crawlers.",
                "priority": severity
            }
        }
    return None

# --- Proactive Recommendations ---

def proactive_search_action(pages: list) -> dict | None:
    has_search_action = False
    for page in pages:
        for script in page['soup'].find_all('script', type='application/ld+json'):
            try:
                text = script.string or ""
                if "SearchAction" in text:
                    has_search_action = True
                    break
            except Exception:
                continue
        if has_search_action:
            break

    if not has_search_action:
        return {
            "id": "ICA-PRO-001",
            "title": "Add WebSite SearchAction Schema for AI Sitelinks",
            "severity": "low",
            "evidence": f"Scanned {len(pages)} pages; none declare a WebSite schema with SearchAction. AI assistants and search engines use SearchAction to offer a sitelinks search box directly in results.",
            "suggested_action": {
                "summary": "Add a WebSite JSON-LD node on the homepage with a SearchAction that points to your site's internal search endpoint. This enables AI assistants to deep-link users into search results.",
                "priority": "low"
            }
        }
    return None

def proactive_faq_schema(pages: list) -> dict | None:
    has_faq_schema = False
    has_faq_content = False

    for page in pages:
        soup = page['soup']
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                text = script.string or ""
                if "FAQPage" in text:
                    has_faq_schema = True
                    break
            except Exception:
                continue

        faq_patterns = re.compile(r'\b(faq|frequently asked|questions|q\s*&\s*a)\b', re.I)
        url_lower = page['url'].lower()
        page_text = soup.get_text(' ', strip=True)[:5000].lower()

        if faq_patterns.search(url_lower) or faq_patterns.search(page_text):
            has_faq_content = True

        if has_faq_schema:
            break

    if not has_faq_schema:
        evidence_detail = (
            "FAQ-like content was detected but lacks FAQPage structured data"
            if has_faq_content
            else f"No FAQPage schema was found across {len(pages)} pages"
        )
        severity = "low"

        return {
            "id": "ICA-PRO-002",
            "title": "Add FAQPage Schema to Increase AI Citation Rate",
            "severity": severity,
            "evidence": f"{evidence_detail}. AI assistants preferentially quote content from pages with FAQPage structured data due to its question-answer format.",
            "suggested_action": {
                "summary": "Structure your most common customer questions as FAQ schema entries. Each Q&A pair becomes a directly quotable answer for AI conversational engines.",
                "priority": severity
            }
        }
    return None

def proactive_smart_404(pages: list) -> dict | None:
    has_custom_404 = False
    for page in pages:
        soup = page['soup']
        forms = soup.find_all('form')
        for form in forms:
            action = (form.get('action') or '').lower()
            role = (form.get('role') or '').lower()
            if 'search' in action or role == 'search':
                has_custom_404 = True 
                break

    if not has_custom_404:
        return {
            "id": "ICA-PRO-003",
            "title": "Implement a Recovery-Oriented 404 Page for AI Deep Links",
            "severity": "low",
            "evidence": "AI assistants occasionally generate or hallucinate URLs that don't exist on a site. Without internal search routing capabilities, broken AI deep links lose the visitor entirely.",
            "suggested_action": {
                "summary": "Build a custom 404 page that includes a prominent site search box and links to your most popular categories to recover visitors from broken AI-generated links.",
                "priority": "low"
            }
        }
    return None

def check_eng_redirects(pages: list) -> dict | None:
    """ENG-REDIRECT-001: Excessive Redirect Chains (Latency & Bounce Risk)"""
    affected_urls = []
    high_severity_count = 0
    
    for page in pages:
        chain = page.get("redirect_chain", [])
        if not chain:
            continue
            
        final_url = page.get("final_url") or page.get("url")
        full_chain = [hop["url"] for hop in chain] + [final_url]
        
        real_hops = 0
        cross_domain = False
        https_downgrade = False
        
        for i in range(len(full_chain) - 1):
            u1 = full_chain[i]
            u2 = full_chain[i + 1]
            
            # Check HTTPS downgrade
            if u1.startswith("https:") and u2.startswith("http:"):
                https_downgrade = True
                
            p1 = urlparse(u1.lower())
            p2 = urlparse(u2.lower())
            n1 = p1.netloc.replace("www.", "")
            n2 = p2.netloc.replace("www.", "")
            
            if n1 != n2:
                cross_domain = True
                
            # Filter trivial hops: identical paths/queries where only scheme or www changed
            if n1 == n2 and p1.path == p2.path and p1.query == p2.query:
                continue 
            else:
                real_hops += 1

        # Escalate severity based on what the chain actually costs the user
        if real_hops >= 2 or cross_domain or https_downgrade:
            affected_urls.append(page["url"])
            if cross_domain or https_downgrade:
                high_severity_count += 1
                
    if affected_urls:
        severity = "high" if high_severity_count > 0 else "medium"
        return {
            "id": "ENG-REDIRECT-001",
            "title": "Excessive Redirect Chains (High Bounce Risk)",
            "severity": severity,
            "evidence": (
                f"{len(affected_urls)} sampled URL(s) triggered risky or multi-hop redirect chains before loading. "
                f"Each intermediate hop adds network latency, which is a known engagement killer for referred traffic. "
                f"Examples: {', '.join(affected_urls[:3])}."
            ),
            "suggested_action": {
                "summary": "Collapse redirect chains to a single hop. Update internal links, sitemaps, and canonical tags to point directly to the final destination URL rather than relying on legacy server redirects to catch up.",
                "priority": severity
            }
        }
    return None

def check_eng_broken_ctas(pages: list) -> dict | None:
    """ENG-DEAD-LINK-001: Live check of primary CTAs and navigation links for 404s."""
    broken_links = []
    
    # Standard browser UA to avoid instant WAF blocks on Python-requests
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    
    for page in pages:
        soup = page["soup"]
        base_url = page.get("final_url") or page["url"]
        domain = urlparse(base_url).netloc
        
        # Target high-value engagement links: buttons, CTAs, or explicit nav links
        cta_tags = soup.find_all("a", href=True, attrs={"class": re.compile(r"btn|cta|button|nav", re.I)})
        if not cta_tags:
            # Fallback to first few content links if no buttons found
            content = soup.find(["main", "article", "[role='main']"]) or soup
            cta_tags = content.find_all("a", href=True)[:3]
            
        sampled_urls = set()
        for a in cta_tags:
            href = a["href"].strip()
            if href.startswith(("mailto:", "tel:", "javascript:", "#")):
                continue
            full_url = urljoin(base_url, href)
            if urlparse(full_url).netloc == domain:
                sampled_urls.add(full_url)
            if len(sampled_urls) >= 3:
                break
                
        # Fast HEAD request to check for actual Not Found errors
        for target_url in sampled_urls:
            try:
                resp = requests.head(target_url, headers=headers, timeout=3, allow_redirects=True)
                
                # Strictly check for 404 (Not Found) or 410 (Gone).
                # Do NOT flag 401, 403 (Forbidden/Blocked) or 429 (Rate Limited) as dead links.
                if resp.status_code in {404, 410}:
                    broken_links.append({"source": base_url, "target": target_url, "status": resp.status_code})
            except requests.RequestException:
                pass # Ignore timeouts/network failures; only flag confirmed HTTP errors

    if broken_links:
        severity = "high" if len(broken_links) > 2 else "medium"
        examples = "; ".join(f"{b['target']} ({b['status']})" for b in broken_links[:3])
        return {
            "id": "ENG-DEAD-LINK-001",
            "title": "Broken Internal Links / Dead CTAs",
            "severity": severity,
            "evidence": (
                f"Sampled primary interaction links (CTAs/nav) across the audited pages and found "
                f"{len(broken_links)} link(s) returning standard 'Not Found' HTTP errors. Examples: {examples}. "
                f"NOTE: This check used an automated script. Some CDNs serve dynamic 404s to unrecognized bots."
            ),
            "suggested_action": {
                "summary": (
                    "Verify that these targets are genuinely dead for human visitors. If confirmed, fix or remove "
                    "the 404ing links. Visitors arriving via AI citations bounce immediately if their next logical click is broken."
                ),
                "priority": severity
            }
        }
    return None

def check_eng_scannability(pages: list) -> dict | None:
    """ENG-SCAN-001: Wall-of-Text Fatigue via Words-Between-Headings."""
    fatigue_pages = []

    for page in pages:
        # Isolate the main content to ignore massive footer menus
        content = page["soup"].find(["main", "article", "[role='main']"])
        if not content:
            content = page["soup"].find("body") or page["soup"]
            
        text = content.get_text(separator=" ", strip=True)
        word_count = len(text.split())
        
        if word_count < 150:
            continue # Skip thin pages (audited elsewhere)
            
        headings_count = len(content.find_all(["h2", "h3", "h4"]))
        
        # Calculate text density
        words_per_heading = word_count / max(1, headings_count)
        
        if words_per_heading > 350:
            fatigue_pages.append({"url": page["url"], "ratio": int(words_per_heading)})

    if fatigue_pages:
        ratio = len(fatigue_pages) / len(pages)
        severity = "medium"
        examples = ", ".join(f"{p['url']} (~{p['ratio']} words/heading)" for p in fatigue_pages[:3])
        return {
            "id": "ENG-SCAN-001",
            "title": "Poor Content Scannability (Wall of Text)",
            "severity": severity,
            "evidence": (
                f"{len(fatigue_pages)} sampled page(s) exhibit severe reading fatigue, averaging over 350 words "
                f"per subheading. Examples: {examples}. AI-referred users scan for specific facts; unbroken text walls cause immediate abandonment."
            ),
            "suggested_action": {
                "summary": "Break monolithic text blocks with descriptive <h2>/<h3> subheadings, bolded key takeaways, and bulleted lists. Ensure your CMS templates encourage structured content formatting.",
                "priority": severity
            }
        }
    return None

def check_eng_arrival_orientation(pages: list) -> dict | None:
    """ENG-ORIENT-001: Can a cold arrival instantly orient themselves?"""
    disoriented_pages = []

    for page in pages:
        soup = page["soup"]
        
        # 1. Is there a clear, dominant H1?
        has_h1 = bool(soup.find("h1"))
        
        # 2. Are there visual breadcrumbs? (Not schema, actual UI)
        has_breadcrumbs = False
        for selector in [".breadcrumb", "[aria-label*='breadcrumb' i]", "#breadcrumbs"]:
            if soup.select_one(selector):
                has_breadcrumbs = True
                break
                
        # 3. Is the brand name/logo accessible in the header?
        has_header_brand = False
        header = soup.find(["header", "nav"])
        if header:
            if header.find("img", alt=True) or header.find(class_=re.compile(r"logo|brand", re.I)):
                has_header_brand = True

        # If a page lacks an H1 AND has no breadcrumbs, a cold arrival is lost.
        if not has_h1 and not has_breadcrumbs:
            disoriented_pages.append(page["url"])

    if disoriented_pages:
        ratio = len(disoriented_pages) / len(pages)
        severity = "high" if ratio > 0.4 else "medium"
        return {
            "id": "ENG-ORIENT-001",
            "title": "Poor Arrival Orientation for Deep Links",
            "severity": severity,
            "evidence": (
                f"{len(disoriented_pages)} page(s) lack both a clear <h1> title and visual UI breadcrumbs. "
                f"Examples: {', '.join(disoriented_pages[:3])}. Visitors arriving 'cold' from an AI citation have no contextual cues to understand where they are within the site hierarchy."
            ),
            "suggested_action": {
                "summary": "Ensure every public URL features a prominent <h1> and visible navigational breadcrumbs. Do not rely solely on your homepage or hidden structured data to orient users.",
                "priority": severity
            }
        }
    return None

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--cache-dir", required=True)
    args = parser.parse_args()

    pages = load_cached_pages(args.cache_dir)
    findings = []
    
    if not pages:
        findings.append({
            "id": "ICA-INFO-NO-DATA",
            "title": "Intent Continuity Audit Skipped",
            "severity": "info",
            "evidence": "No usable HTML page artifacts found in cache to evaluate intent continuity.",
            "suggested_action": None
        })
        print(json.dumps(findings, indent=2))
        sys.exit(0)

    site_type = detect_site_typology(pages)
    
    checks = [
        lambda p: check_ica_001(p),
        lambda p: check_ica_002(p),
        lambda p: check_ica_003(p),
        lambda p: check_ica_004(p, site_type),
        lambda p: check_ica_005(p),
        lambda p: check_ica_006(p),
        lambda p: check_ica_007(p, site_type),
        lambda p: check_ica_008(p, site_type),
        lambda p: check_ica_009(p),
        lambda p: check_ica_010(p),
        lambda p: check_eng_arrival_orientation(p),
        lambda p: check_eng_scannability(p),
        lambda p: check_eng_broken_ctas(p),
        lambda p: check_eng_redirects(p),
    ]
    
    for check in checks:
        try:
            finding = check(pages)
            if finding:
                findings.append(finding)
        except Exception as e:
            sys.stderr.write(f"Error in intent check: {e}\n")
            
    proactive_checks = [
        proactive_search_action,
        proactive_faq_schema,
        proactive_smart_404,
    ]

    for check in proactive_checks:
        try:
            finding = check(pages)
            if finding:
                findings.append(finding)
        except Exception as e:
            sys.stderr.write(f"Error in proactive check: {e}\n")
            
    # Check if there are any non-'info' severity findings before determining 'all clear'
    has_defects = any(f.get("severity") in ["critical", "high", "medium", "low"] for f in findings)
            
    if not has_defects:
        findings.append({
            "id": "ICA-INFO-ALL-CLEAR",
            "title": "AI Content & Intent Continuity Validated",
            "severity": "info",
            "evidence": f"Analyzed {len(pages)} cached pages. Content hierarchy, deep links, metadata, and user engagement pathways pass structural heuristics for site classification: '{site_type}'.",
            "suggested_action": None
        })
            
    print(json.dumps(findings, indent=2))
    sys.exit(0)

if __name__ == "__main__":
    main()