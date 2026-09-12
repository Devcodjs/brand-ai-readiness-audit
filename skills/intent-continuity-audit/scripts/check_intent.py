import argparse
import copy
import json
import re
import sys
import urllib.parse
from pathlib import Path
from bs4 import BeautifulSoup
import copy

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
            "title": "Overlay & Interstitial Blockade",
            "severity": severity,
            "evidence": f"Found {len(found_overlays)} overlay-pattern elements across {len(pages)} pages: {list(set(found_overlays))[:5]}. Elements with position:fixed/absolute block above-fold content on initial page load.",
            "suggested_action": {
                "summary": "Replace full-viewport overlay modals with non-blocking inline banners. Defer newsletter popups until after meaningful engagement. Avoid login walls for informational content.",
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
            "title": "Missing Deep-Link Anchor Targets",
            "severity": "medium",
            "evidence": f"Across {len(pages)} pages, average of {avg:.1f} deep-linkable sections. {low_anchor_pages}/{len(pages)} pages have fewer than 3 anchor targets, limiting AI's ability to link to specific content.",
            "suggested_action": {
                "summary": "Add descriptive id attributes to all major content sections and headings (e.g., <section id='pricing'>). This enables AI assistants to deep-link users directly to the relevant section.",
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
            "title": "No URL Parameter Handling",
            "severity": "medium",
            "evidence": f"Scanned scripts across {len(pages)} pages; 0/{len(pages)} contain URL parameter parsing. The site cannot adapt content based on AI referral context.",
            "suggested_action": {
                "summary": "Implement URL parameter parsing to detect AI referral sources. Use referral context to surface relevant content or highlight the answer the user sought.",
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
            "title": "Above-Fold Transactional Content Deficit",
            "severity": "high",
            "evidence": f"On {deficit_pages}/{len(pages)} pages, transactional keywords appear only below the estimated fold. Key facts require scrolling to discover.",
            "suggested_action": {
                "summary": "Move critical transactional information above the fold. Use anchor cards or summary panels at the top of the page.",
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
            "title": "Missing or Generic Page Titles",
            "severity": "high",
            "evidence": f"{dup_count}/{len(pages)} pages share the same <title> tag. {missing}/{len(pages)} have no <title>. Identical titles make AI citations ambiguous.",
            "suggested_action": {
                "summary": "Give every page a unique, descriptive <title> including both page topic and brand name.",
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
            "title": "Missing Meta Descriptions / OG Tags",
            "severity": "high",
            "evidence": f"{meta_missing}/{len(pages)} pages lack <meta description>. {og_missing}/{len(pages)} lack og:title or og:description.",
            "suggested_action": {
                "summary": "Add unique <meta name='description'> (120-160 chars) and Open Graph tags to every page.",
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
            "title": "No CTAs in Main Content",
            "severity": "high",
            "evidence": f"{no_cta_pages}/{len(pages)} pages have no action-oriented links or buttons in their main content area.",
            "suggested_action": {
                "summary": "Add contextual CTAs within content area of every page. Match CTAs to page intent.",
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
            "title": "No Conversion Path Across Site",
            "severity": "high",
            "evidence": f"Scanned {len(pages)} pages: found 0 conversion elements (forms, tel: links, mailto: links, or contact pages).",
            "suggested_action": {
                "summary": "Add a persistent conversion path accessible from every page — contact form, phone number, or booking widget.",
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
            "title": "Missing Breadcrumb Navigation",
            "severity": "low",
            "evidence": f"{missing_breadcrumbs}/{non_home_pages} non-homepage pages lack breadcrumb navigation.",
            "suggested_action": {
                "summary": "Add breadcrumb navigation with BreadcrumbList JSON-LD to all pages below homepage level.",
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
            "title": "Orphan Pages",
            "severity": "medium",
            "evidence": f"{orphan_pages}/{len(pages)} pages contain 0 contextual internal links in their content area.",
            "suggested_action": {
                "summary": "Add contextual internal links within content body. Each page should offer 2-3 relevant internal paths.",
                "priority": "medium"
            }
        }
    return None


# ---------------------------------------------------------------------------
# Proactive recommendations — beyond-problem suggestions that strengthen
# on-site engagement and AI-referral continuity even where no explicit
# defect was detected.
# ---------------------------------------------------------------------------

def proactive_search_action(pages):
    """Recommend WebSite + SearchAction schema for AI sitelinks search box."""
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
            "evidence": f"Scanned {len(pages)} pages; none declare a WebSite schema with SearchAction. AI assistants and search engines use SearchAction to offer a sitelinks search box directly in results, letting users jump to specific content without navigating the full site.",
            "suggested_action": {
                "summary": "Add a WebSite JSON-LD node on the homepage with a SearchAction that points to your site's internal search endpoint. Example: '{\"@type\": \"WebSite\", \"url\": \"https://example.com\", \"potentialAction\": {\"@type\": \"SearchAction\", \"target\": \"https://example.com/search?q={search_term_string}\", \"query-input\": \"required name=search_term_string\"}}'. This enables AI assistants to deep-link users into search results even when no direct page URL is available.",
                "priority": "medium"
            }
        }
    return None


def proactive_faq_schema(pages):
    """Recommend FAQPage schema for direct AI quoting."""
    has_faq_schema = False
    has_faq_content = False

    for page in pages:
        soup = page['soup']
        # Check if FAQPage schema already exists
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                text = script.string or ""
                if "FAQPage" in text:
                    has_faq_schema = True
                    break
            except Exception:
                continue

        # Check if there's FAQ-like content (questions and answers pattern)
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
        severity = "medium" if has_faq_content else "low"

        return {
            "id": "ICA-PRO-002",
            "title": "Add FAQPage Schema to Increase AI Citation Rate",
            "severity": severity,
            "evidence": f"{evidence_detail}. AI assistants like ChatGPT and Perplexity preferentially quote content from pages with FAQPage structured data because the question-answer format maps directly to conversational Q&A — the exact interaction pattern of AI chat.",
            "suggested_action": {
                "summary": "Create or enhance a FAQ section using FAQPage JSON-LD schema. Each Q&A pair becomes a directly quotable answer for AI assistants. Structure your most common customer questions as FAQ schema entries — these are the queries AI users will ask. Even non-FAQ pages benefit from marking up common questions addressed in the content.",
                "priority": severity
            }
        }
    return None


def proactive_smart_404(pages):
    """Recommend a helpful 404 page for broken AI-generated deep links."""
    # AI assistants sometimes generate URLs that don't exist on the target site
    # (hallucinated deep links). A smart 404 page with search + popular links
    # recovers these visitors instead of losing them.
    has_custom_404 = False
    has_search_on_404 = False

    for page in pages:
        soup = page['soup']
        # Check if any page has a visible search form in the main content
        # (we can't directly test the 404 page, but we can recommend it)
        forms = soup.find_all('form')
        for form in forms:
            action = (form.get('action') or '').lower()
            role = (form.get('role') or '').lower()
            if 'search' in action or role == 'search':
                has_custom_404 = True  # Site at least has search capability
                break

    if not has_custom_404:
        return {
            "id": "ICA-PRO-003",
            "title": "Implement a Recovery-Oriented 404 Page for AI Deep Links",
            "severity": "low",
            "evidence": f"AI assistants occasionally generate or hallucinate URLs that don't exist on a site (e.g., /product/widget-xyz when the actual path is /products/widget-xyz-2025). When these links break, a default 404 page loses the visitor entirely.",
            "suggested_action": {
                "summary": "Build a custom 404 page that includes: (1) a prominent site search box so visitors can find what the AI was trying to link to, (2) links to your most popular categories and pages, and (3) suggested content based on the URL path keywords. This recovers visitors from broken AI-generated deep links instead of bouncing them.",
                "priority": "low"
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
    
    # Defect detection checks
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
            
    for i, finding in enumerate(findings):
        finding["id"] = f"ICA-{i+1:03d}"

    # Proactive beyond-problem recommendations — these run regardless of
    # whether defects were found, surfacing improvements that strengthen
    # AI-referral engagement even on already-passing pages.
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
            print(f"Proactive check {check.__name__} error: {e}", file=sys.stderr)

    print(json.dumps(findings, indent=2))
    sys.exit(0)

if __name__ == "__main__":
    main()
