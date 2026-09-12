import argparse
import copy
import json
import re
import sys
import urllib.parse
from pathlib import Path
from bs4 import BeautifulSoup

def load_cached_pages(cache_dir):
    index_path = Path(cache_dir) / "cache_index.json"
    if not index_path.exists():
        return []
    index = json.loads(index_path.read_text(encoding="utf-8"))
    pages = []
    for page in index.get("pages", []):
        if page.get("success"):
            try:
                html = Path(page["file"]).read_text(encoding="utf-8", errors="replace")
                soup = BeautifulSoup(html, "html.parser")
                pages.append({"url": page["url"], "html": html, "soup": soup})
            except Exception:
                continue
    return pages

def get_content_area(soup):
    for selector in ["main", "article", "[role='main']"]:
        el = soup.select_one(selector)
        if el:
            return el
    return soup.find("body") or soup

def check_ica_001(pages):
    overlay_patterns = re.compile(r'cookie|consent|gdpr|onetrust|cookiebot|cc-banner|cookie-notice|cookie-law|modal|popup|overlay|lightbox|subscribe|newsletter|signup-modal|paywall|login-wall|gated|premium-content|subscriber-only', re.I)
    
    found_overlays = []
    has_high_severity = False
    
    for page in pages:
        soup = page['soup']
        for el in soup.find_all(True):
            style = el.get('style', '').lower()
            
            # Skip elements explicitly hidden in the static DOM
            if 'display:none' in style or 'display: none' in style or \
               'visibility:hidden' in style or 'visibility: hidden' in style or \
               'opacity:0' in style or 'opacity: 0' in style or el.has_attr('hidden'):
                continue

            cls = " ".join(el.get('class', []))
            id_val = el.get('id', '')
            match = overlay_patterns.search(cls) or overlay_patterns.search(id_val)
            
            if match or el.name == 'dialog':
                has_fixed = 'position:fixed' in style or 'position:absolute' in style or 'position: fixed' in style or 'position: absolute' in style
                
                # Apply the filter: only flag if explicitly positioned or a native dialog
                if has_fixed or el.name == 'dialog':
                    is_login = el.find('input', type='password') or re.search(r'paywall|login-wall|gated|premium-content|subscriber-only', cls + " " + id_val, re.I)
                    if is_login:
                        has_high_severity = True
                    
                    found_overlays.append(f"{el.name}.{cls.replace(' ', '.')}")
                    break
                
    if found_overlays:
        severity = "high" if has_high_severity else "medium"
        return {
            "id": "ICA-OVERLAY-001",
            "title": "Overlay & Interstitial Blockade",
            "severity": severity,
            "evidence": f"Found {len(found_overlays)} statically positioned overlay elements across {len(pages)} pages: {list(set(found_overlays))[:5]}.",
            "suggested_action": {
                "summary": (
                    "Full-viewport overlays block AI parsers from reading initial content. "
                    "If these overlays do not already detect and bypass AI user-agents server-side, verify that "
                    "they are replaced with non-blocking inline banners to ensure unrestricted AI discoverability "
                    "and seamless on-site engagement."
                ),
                "priority": severity
            }
        }
    return None

def check_ica_002(pages):
    if not pages:
        return None
    
    low_anchor_pages = 0
    total_anchors = 0
    
    for page in pages:
        content = get_content_area(page['soup'])
        anchors = content.find_all(id=True)
        headers_with_id = content.find_all(['h2', 'h3', 'h4'], id=True)
        sections_with_id = content.find_all('section', id=True)
        
        page_anchors = len(set(headers_with_id + sections_with_id + anchors))
        total_anchors += page_anchors
        
        if page_anchors < 3:
            low_anchor_pages += 1
            
    avg = total_anchors / len(pages) if pages else 0
    if avg < 3:
        return {
            "id": "ICA-ANCHOR-001",
            "title": "Missing Deep-Link Anchor Targets",
            "severity": "medium",
            "evidence": f"Across {len(pages)} pages, average of {avg:.1f} deep-linkable sections. {low_anchor_pages}/{len(pages)} pages have fewer than 3 anchor targets.",
            "suggested_action": {
                "summary": (
                    "Missing anchor targets prevent AI from deep-linking users to specific answers. "
                    "If section routing is not handled exclusively via a custom client-side router, verify that "
                    "descriptive 'id' attributes are added to all major HTML content sections to improve AI "
                    "discoverability and subsequent on-site engagement."
                ),
                "priority": "medium"
            }
        }
    return None

def check_ica_003(pages):
    if not pages:
        return None
        
    param_patterns = re.compile(r'URLSearchParams|location\.search|location\.hash|getParameter|queryString|window\.location\.href\.split|document\.referrer|utm_source|utm_medium|utm_campaign')
    
    has_params = 0
    for page in pages:
        scripts = page['soup'].find_all('script')
        for script in scripts:
            if script.string and param_patterns.search(script.string):
                has_params += 1
                break
                
    if has_params == 0:
        return {
            "id": "ICA-URLPARAM-001",
            "title": "No URL Parameter Handling Detected",
            "severity": "medium",
            "evidence": f"Scanned static scripts across {len(pages)} pages; 0 contain standard client-side URL parameter parsing.",
            "suggested_action": {
                "summary": (
                    "Without parameter parsing, the site cannot adapt to AI referral context. "
                    "If referral tracking is not handled server-side or at the edge, verify that client-side "
                    "URL parameter parsing is implemented to surface relevant content and boost on-site engagement."
                ),
                "priority": "medium"
            }
        }
    return None

def check_ica_004(pages):
    if not pages:
        return None
        
    trans_keywords = re.compile(r'\b(price|cost|\$|€|£|₹|buy|order|book|add to cart|contact|phone|call|hours|availability|in stock|shipping|free|get started)\b', re.I)
    
    deficit_pages = 0
    for page in pages:
        soup = copy.copy(page['soup'])
        for s in soup(['script', 'style', 'noscript']):
            s.extract()
        
        body = soup.find('body')
        if not body:
            continue
            
        text = list(body.stripped_strings)
        if not text:
            continue
            
        total_len = len(text)
        fold_idx = max(1, int(total_len * 0.2))
        
        above_fold_text = " ".join(text[:fold_idx])
        below_fold_text = " ".join(text[fold_idx:])
        
        above_matches = trans_keywords.findall(above_fold_text)
        below_matches = trans_keywords.findall(below_fold_text)
        
        if not above_matches and below_matches:
            deficit_pages += 1
            
    if deficit_pages > len(pages) / 2:
        return {
            "id": "ICA-TRANSACTIONAL-001",
            "title": "Above-Fold Transactional Content Deficit",
            "severity": "high",
            "evidence": f"On {deficit_pages}/{len(pages)} pages, static transactional keywords appear only below the estimated fold.",
            "suggested_action": {
                "summary": (
                    "Burying transactional content impairs LLM extraction of key facts. "
                    "If transactional details are not dynamically injected above the fold post-load, verify that "
                    "critical static information is moved higher in the DOM to maximize AI discoverability and "
                    "drive immediate on-site engagement."
                ),
                "priority": "high"
            }
        }
    return None

def check_ica_005(pages):
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
        
    max_dup = max(title_counts.values()) if title_counts else 0
    dup_count = sum(v for v in title_counts.values() if v > 1)
    
    if (dup_count + missing) > len(pages) / 2:
        return {
            "id": "ICA-TITLE-001",
            "title": "Missing or Generic Page Titles",
            "severity": "high",
            "evidence": f"{dup_count}/{len(pages)} pages share the same static <title> tag. {missing}/{len(pages)} have no <title>.",
            "suggested_action": {
                "summary": (
                    "Generic titles cause ambiguous AI citations. "
                    "If unique titles are not provided to crawlers via dynamic rendering or structured feeds, verify that "
                    "every static route includes a unique, descriptive <title> tag to maximize AI discoverability."
                ),
                "priority": "high"
            }
        }
    return None

def check_ica_006(pages):
    if not pages:
        return None
        
    meta_missing = 0
    og_missing = 0
    
    for page in pages:
        soup = page['soup']
        desc = soup.find('meta', attrs={'name': re.compile(r'^description$', re.I)})
        has_desc = desc and desc.get('content') and len(desc.get('content').strip()) > 20
        if not has_desc:
            meta_missing += 1
            
        og_title = soup.find('meta', attrs={'property': 'og:title'})
        og_desc = soup.find('meta', attrs={'property': 'og:description'})
        
        has_og_title = og_title and og_title.get('content') and len(og_title.get('content').strip()) > 5
        has_og_desc = og_desc and og_desc.get('content') and len(og_desc.get('content').strip()) > 20
        
        if not (has_og_title or has_og_desc):
            og_missing += 1
            
    if meta_missing > len(pages) / 2 or og_missing > len(pages) / 2:
        return {
            "id": "ICA-META-001",
            "title": "Missing Meta Descriptions / OG Tags",
            "severity": "high",
            "evidence": f"{meta_missing}/{len(pages)} pages lack static <meta description>. {og_missing}/{len(pages)} lack static og:title or og:description.",
            "suggested_action": {
                "summary": (
                    "Missing metadata prevents rich preview generation in AI conversational interfaces. "
                    "If meta tags are not dynamically rendered for crawlers, verify that unique static "
                    "<meta name='description'> and Open Graph tags are present to ensure correct summarization."
                ),
                "priority": "high"
            }
        }
    return None

def check_ica_007(pages):
    if not pages:
        return None
        
    action_patterns = re.compile(r'\b(buy|order|book|contact|get started|sign up|learn more|schedule|request|try|subscribe|add to cart|shop|explore|download|register|apply|join|start|enroll|enquire|inquire)\b', re.I)
    
    no_cta_pages = 0
    
    for page in pages:
        content = get_content_area(page['soup'])
        ctas = content.find_all(['a', 'button'])
        has_cta = False
        for cta in ctas:
            text = cta.get_text(strip=True)
            if action_patterns.search(text):
                has_cta = True
                break
        if not has_cta:
            no_cta_pages += 1
            
    if no_cta_pages > 0:
        return {
            "id": "ICA-CTA-001",
            "title": "No CTAs in Main Content",
            "severity": "high",
            "evidence": f"{no_cta_pages}/{len(pages)} pages have no static action-oriented links or buttons in their main content area.",
            "suggested_action": {
                "summary": (
                    "Lack of actionable links prevents AI from surfacing next steps for the user. "
                    "If calls-to-action are not provided via dynamic widgets or structured APIs, verify that "
                    "contextual CTAs are directly embedded within the main HTML content area to boost on-site engagement."
                ),
                "priority": "high"
            }
        }
    return None

def check_ica_008(pages):
    if not pages:
        return None
        
    conv_elements = 0
    conv_patterns = re.compile(r'contact|book|schedule|quote|enquiry|appointment', re.I)
    
    for page in pages:
        soup = page['soup']
        tel_links = soup.find_all('a', href=re.compile(r'^tel:'))
        mailto_links = soup.find_all('a', href=re.compile(r'^mailto:'))
        
        forms = [f for f in soup.find_all('form') if 'search' not in (f.get('action') or '').lower() and 'search' not in (f.get('class') or [])]
        
        keyword_links = [a for a in soup.find_all('a', href=True) if conv_patterns.search(a.get('href', ''))]
        
        conv_elements += len(tel_links) + len(mailto_links) + len(forms) + len(keyword_links)
        
    if conv_elements == 0:
        return {
            "id": "ICA-CONVERSION-001",
            "title": "No Conversion Path Across Site",
            "severity": "high",
            "evidence": f"Scanned {len(pages)} pages: found 0 static conversion elements (forms, tel: links, mailto: links, or contact pages).",
            "suggested_action": {
                "summary": (
                    "Without clear conversion endpoints, AI agents cannot seamlessly route user intent. "
                    "If conversions are not handled through dynamic client-side applications like chat interfaces, "
                    "verify that a persistent, statically discoverable conversion path exists to capture on-site engagement."
                ),
                "priority": "high"
            }
        }
    return None

def check_ica_009(pages):
    if len(pages) <= 1:
        return None
        
    missing_breadcrumbs = 0
    non_home_pages = 0
    
    for page in pages:
        url = page['url']
        parsed = urllib.parse.urlparse(url)
        if parsed.path in ['', '/'] and not parsed.query:
            continue
            
        non_home_pages += 1
        soup = page['soup']
        
        has_breadcrumb = False
        
        navs = soup.find_all('nav')
        for nav in navs:
            aria = nav.get('aria-label', '').lower()
            if 'breadcrumb' in aria:
                has_breadcrumb = True
                break
                
        if not has_breadcrumb:
            for el in soup.find_all(True):
                cls = ' '.join(el.get('class', [])).lower()
                if 'breadcrumb' in cls:
                    has_breadcrumb = True
                    break
                    
        if not has_breadcrumb:
            for ol in soup.find_all('ol'):
                if ol.get('itemtype') and 'BreadcrumbList' in ol.get('itemtype'):
                    has_breadcrumb = True
                    break
                    
        if not has_breadcrumb:
            scripts = soup.find_all('script', type='application/ld+json')
            for script in scripts:
                try:
                    data = json.loads(script.string)
                    items = data if isinstance(data, list) else [data]
                    for item in items:
                        if item.get('@type') == 'BreadcrumbList':
                            has_breadcrumb = True
                            break
                except:
                    pass
                    
        if not has_breadcrumb:
            missing_breadcrumbs += 1
            
    if non_home_pages > 0 and (missing_breadcrumbs / non_home_pages) > 0.7:
        return {
            "id": "ICA-BREADCRUMB-001",
            "title": "Missing Breadcrumb Navigation",
            "severity": "low",
            "evidence": f"{missing_breadcrumbs}/{non_home_pages} non-homepage pages lack static breadcrumb indicators.",
            "suggested_action": {
                "summary": (
                    "Missing breadcrumbs make it difficult for AI to infer site taxonomy. "
                    "If breadcrumb hierarchies are not provided via structured API feeds, verify that static "
                    "breadcrumb navigation with JSON-LD is included to clarify architecture for AI discoverability."
                ),
                "priority": "low"
            }
        }
    return None

def check_ica_010(pages):
    if not pages:
        return None
        
    orphan_pages = 0
    
    for page in pages:
        url = page['url']
        parsed = urllib.parse.urlparse(url)
        domain = parsed.netloc
        
        content = get_content_area(copy.copy(page['soup']))
        
        for el in content.find_all(['nav', 'footer']):
            el.extract()
            
        links = content.find_all('a', href=True)
        internal_links = 0
        for link in links:
            href = link.get('href', '')
            if href.startswith('#') or href.startswith('javascript:'):
                continue
            href_parsed = urllib.parse.urlparse(href)
            if not href_parsed.netloc or href_parsed.netloc == domain:
                internal_links += 1
                
        if internal_links == 0:
            orphan_pages += 1
            
    if orphan_pages > 0:
        return {
            "id": "ICA-ORPHAN-001",
            "title": "Orphan Pages",
            "severity": "medium",
            "evidence": f"{orphan_pages}/{len(pages)} pages contain 0 contextual internal links in their static main content area.",
            "suggested_action": {
                "summary": (
                    "A disconnected content graph impairs AI discoverability. "
                    "If routing and topic relationships are not mapped through a structured API or dynamic SPA router, "
                    "verify that contextual internal HTML links are included within the body of every page to "
                    "encourage deeper on-site engagement."
                ),
                "priority": "medium"
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
    
    checks = [
        check_ica_001, check_ica_002, check_ica_003, check_ica_004, check_ica_005,
        check_ica_006, check_ica_007, check_ica_008, check_ica_009, check_ica_010
    ]
    
    for check in checks:
        try:
            finding = check(pages)
            if finding:
                findings.append(finding)
        except Exception as e:
            print(f"Error in {check.__name__}: {e}", file=sys.stderr)
            
    # If no issues were identified, append a positive "info" finding explicitly stating what passed.
    if not findings:
        findings.append({
            "id": "ICA-INFO-ALL-CLEAR",
            "title": "AI Content Readiness Validated",
            "severity": "info",
            "evidence": f"Analyzed {len(pages)} cached pages. Structural tags, deep links, content hierarchies, metadata, and above-the-fold interactions all pass static validation without blocking elements.",
            "suggested_action": {
                "summary": "The site successfully balances strong AI discoverability with frictionless on-site engagement. Continue maintaining clean semantic HTML, deep linking targets, and explicit conversion pathways.",
                "priority": "info"
            }
        })
            
    print(json.dumps(findings, indent=2))
    sys.exit(0)

if __name__ == "__main__":
    main()