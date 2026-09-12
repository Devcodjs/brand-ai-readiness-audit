import argparse
import json
import sys
import re
from pathlib import Path
from bs4 import BeautifulSoup
import urllib.parse

def load_cached_pages(cache_dir: str) -> list:
    index_path = Path(cache_dir) / "cache_index.json"
    if not index_path.exists():
        return []
    
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
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
                    "content_classification": page.get("content_classification")
                })
            except Exception:
                continue
    return pages

def get_content_area(soup):
    for selector in ["main", "article", "[role='main']"]:
        el = soup.select_one(selector)
        if el:
            return el
    return soup.find("body") or soup

def check_cea_001(pages: list) -> dict | None:
    total_img_tags = 0
    excluded_images = 0
    content_images = 0
    missing_alt = 0
    affected_urls = set()
    
    ui_keywords = [
        "icon", "logo", "avatar", "profile", "social", "share", "menu", 
        "search", "close", "arrow", "chevron", "thumbnail", "badge", 
        "emoji", "sprite"
    ]

    for page in pages:
        content_area = get_content_area(page["soup"])
        images = content_area.find_all("img")
        
        for img in images:
            total_img_tags += 1
            
            # 1. Skip structural/navigational parents
            if img.find_parent(["nav", "header", "footer", "aside", "button"]):
                excluded_images += 1
                continue
                
            # Skip if inside a role="button" or interactive control
            if img.find_parent(attrs={"role": "button"}):
                excluded_images += 1
                continue

            # 2. Skip explicitly decorative elements
            if img.get("aria-hidden") == "true" or img.get("role") in ["presentation", "none"]:
                excluded_images += 1
                continue

            # 3. Skip UI keywords in class, id, or src
            cls = img.get("class", [])
            cls_str = " ".join(cls) if isinstance(cls, list) else str(cls)
            id_val = str(img.get("id", ""))
            src = str(img.get("src", ""))
            
            cls_id_src = f"{cls_str} {id_val} {src}".lower()
            if any(kw in cls_id_src for kw in ui_keywords):
                excluded_images += 1
                continue

            # 4. Skip tiny images / tracking pixels
            width = img.get("width")
            height = img.get("height")
            if width and str(width).isdigit() and int(width) < 5:
                excluded_images += 1
                continue
            if height and str(height).isdigit() and int(height) < 5:
                excluded_images += 1
                continue
            
            if any(x in src.lower() for x in ["spacer", "pixel", "blank", "1x1"]):
                excluded_images += 1
                continue
            if src.startswith("data:image") and len(src) < 100:
                excluded_images += 1
                continue
            
            # Passed filters: classify as an informational/content image
            content_images += 1
            alt = img.get("alt")
            
            if alt is None or alt.strip() == "":
                missing_alt += 1
                affected_urls.add(page["url"])

    if content_images > 0:
        ratio = missing_alt / content_images
        if ratio > 0.2:
            severity = "high" if ratio > 0.5 else "medium"
            pct = round(ratio * 100, 1)
            urls = list(affected_urls)[:5]
            return {
                "id": "CEA-001",
                "title": "Content Images Without Alt Text",
                "severity": severity,
                "evidence": f"Scanned {total_img_tags} total <img> tags across {len(pages)} pages; excluded {excluded_images} likely UI/decorative images. Of the {content_images} remaining informational/content images, {missing_alt} ({pct}%) lack meaningful alt text. Affected pages: [{', '.join(urls)}]",
                "suggested_action": {
                    "summary": "Add descriptive alt text to all informational images. Provide context relevant to the image (e.g., product specs, historical details) alongside any visible text.",
                    "priority": severity
                }
            }
    return None

def check_cea_002(pages: list) -> dict | None:
    canvas_count = 0
    affected_urls = set()

    for page in pages:
        soup = page["soup"]
        canvases = soup.find_all("canvas")
        for canvas in canvases:
            parent = canvas.find_parent(["nav", "footer"])
            if parent:
                continue
            
            has_fallback = False
            for sibling in canvas.find_next_siblings() + canvas.find_previous_siblings():
                if sibling.name in ["table", "dl", "figcaption"]:
                    has_fallback = True
                    break
            if not has_fallback:
                p = canvas.parent
                if p and p.name in ["table", "dl", "figcaption"]:
                    has_fallback = True
            if not has_fallback and canvas.get("aria-label"):
                has_fallback = True
            
            if not has_fallback:
                canvas_count += 1
                affected_urls.add(page["url"])

    if canvas_count > 0:
        ratio = len(affected_urls) / len(pages)
        severity = "high" if ratio > 0.4 else "medium"
        urls = list(affected_urls)[:5]
        return {
            "id": "CEA-002",
            "title": "Canvas Elements Without Text Fallbacks",
            "severity": severity,
            "evidence": f"Found {canvas_count} <canvas> elements in content areas across {len(affected_urls)} pages with no adjacent text-based data equivalent. Pages: [{', '.join(urls)}]",
            "suggested_action": {
                "summary": "Provide a text-based data table or description adjacent to each canvas element. Use <figcaption> or aria-label to describe the data being visualized.",
                "priority": severity
            }
        }
    return None

def check_cea_003(pages: list) -> dict | None:
    svg_count = 0
    affected_urls = set()

    for page in pages:
        content_area = get_content_area(page["soup"])
        svgs = content_area.find_all("svg")
        for svg in svgs:
            if svg.get("aria-hidden") == "true":
                continue
            
            has_text = False
            if svg.find(["title", "desc", "text"]):
                has_text = True
            if svg.get("aria-label") or svg.get("aria-labelledby"):
                has_text = True
            
            if not has_text:
                svg_count += 1
                affected_urls.add(page["url"])

    if svg_count > 0:
        ratio = len(affected_urls) / len(pages)
        severity = "high" if ratio > 0.5 else "medium"
        return {
            "id": "CEA-003",
            "title": "SVGs Without Accessible Text",
            "severity": severity,
            "evidence": f"Found {svg_count} SVG elements in content areas lacking <title>, <desc>, <text>, or ARIA labels across {len(affected_urls)} pages.",
            "suggested_action": {
                "summary": "If these SVGs are decorative UI elements (which is most common), globally adding aria-hidden='true' to your base icon component will instantly resolve this. If they are informational charts, provide <title> and <desc> tags so AI parsers can read them.",
                "priority": severity
            }
        }
    return None

def check_cea_004(pages: list) -> dict | None:
    media_count = 0
    affected_urls = set()

    for page in pages:
        soup = page["soup"]
        medias = soup.find_all(["video", "audio"])
        for media in medias:
            has_caption = False
            tracks = media.find_all("track")
            for track in tracks:
                kind = track.get("kind", "").lower()
                if kind in ["captions", "subtitles"]:
                    has_caption = True
                    break
            
            if not has_caption:
                parent = media.parent
                if parent and "transcript" in parent.get_text().lower():
                    has_caption = True

            if not has_caption:
                media_count += 1
                affected_urls.add(page["url"])

    if media_count > 0:
        ratio = len(affected_urls) / len(pages)
        severity = "high" if ratio > 0.5 else "medium"
        return {
            "id": "CEA-004",
            "title": "Video/Audio Without Captions",
            "severity": severity,
            "evidence": f"Found {media_count} video/audio elements across {len(affected_urls)} pages without <track> captions or adjacent transcript links.",
            "suggested_action": {
                "summary": "Add WebVTT caption tracks (<track kind='captions'>) to all video and audio elements. Provide a text transcript link adjacent to embedded media. If manual captioning isn't feasible, integrate an automated transcription API into your media upload pipeline, or simply output a machine-readable text summary in an accordion immediately below the player.",
                "priority": severity
            }
        }
    return None

def check_cea_005(pages: list) -> dict | None:
    img_count = 0
    affected_urls = set()
    examples = set()
    keywords = ["table", "spec", "chart", "comparison", "pricing", "schedule", "menu", "rate", "plan"]

    for page in pages:
        soup = page["soup"]
        images = soup.find_all("img")
        for img in images:
            src = img.get("src", "").lower()
            alt = img.get("alt", "").lower()
            if any(kw in src or kw in alt for kw in keywords):
                parent = img.parent
                if parent:
                    if not parent.find(["table", "dl"]):
                        img_count += 1
                        affected_urls.add(page["url"])
                        match = [kw for kw in keywords if kw in src or kw in alt]
                        if match:
                            examples.add(match[0])

    if img_count > 0:
        ratio = len(affected_urls) / len(pages)
        severity = "high" if ratio > 0.3 else "medium"
        example = list(examples)[0] if examples else "table"
        return {
            "id": "CEA-005",
            "title": "Data Tables as Images",
            "severity": severity,
            "evidence": f"Found {img_count} images with data-suggestive filenames/alt text (e.g., '{example}') without adjacent <table> or <dl> markup on {len(affected_urls)} pages.",
            "suggested_action": {
                "summary": "Replace image-based data presentations with semantic HTML <table> or <dl> elements. Keep the image as a visual supplement but ensure the data is also available as structured text.",
                "priority": severity
            }
        }
    return None

def check_cea_006(pages: list) -> dict | None:
    iframe_count = 0
    missing_title = 0
    affected_urls = set()
    domains = set()

    for page in pages:
        content_area = get_content_area(page["soup"])
        iframes = content_area.find_all("iframe")
        for iframe in iframes:
            src = iframe.get("src")
            if not src:
                continue
            try:
                parsed = urllib.parse.urlparse(src)
                domain = parsed.netloc.lower()
            except Exception:
                continue

            if domain and domain not in urllib.parse.urlparse(page["url"]).netloc.lower():
                if any(x in domain for x in ["youtube.com", "vimeo.com", "google.com"]):
                    continue
                iframe_count += 1
                if not iframe.get("title"):
                    missing_title += 1
                    affected_urls.add(page["url"])
                    domains.add(domain)

    if missing_title > 0:
        ratio = len(affected_urls) / len(pages)
        severity = "medium" if ratio > 0.5 else "low"
        domain_list = list(domains)[:5]
        return {
            "id": "CEA-006",
            "title": "External Iframes Without Title",
            "severity": severity,
            "evidence": f"Found {iframe_count} external iframes. {missing_title} lack a title attribute, making their content opaque to crawlers. Affected pages: {len(affected_urls)}. Sources: [{', '.join(domain_list)}]",
            "suggested_action": {
                "summary": "Update your base <Embed/> or <VideoPlayer/> wrapper component to accept and automatically apply a title prop to the underlying <iframe>. This resolves the issue globally for all future content. For existing iframes, add a descriptive title attribute that explains the embedded content (e.g., 'Product Demo Video', 'Interactive Map of Locations').",
                "priority": severity
            }
        }
    return None

def check_cea_007(pages: list) -> dict | None:
    noscript_count = 0
    total_chars = 0
    affected_urls = set()

    for page in pages:
        soup = page["soup"]
        noscripts = soup.find_all("noscript")
        for noscript in noscripts:
            content = noscript.get_text()
            if not content:
                ns_soup = BeautifulSoup(str(noscript.string) if noscript.string else "", "html.parser")
                content = ns_soup.get_text()
            
            ns_soup2 = BeautifulSoup(str(noscript.string) if noscript.string else "", "html.parser")
            imgs = ns_soup2.find_all("img")
            if imgs and len(imgs) == 1:
                img = imgs[0]
                w = img.get("width")
                h = img.get("height")
                if w and w.isdigit() and int(w) <= 1 and h and h.isdigit() and int(h) <= 1:
                    continue

            if "enable javascript" in content.lower():
                continue
                
            char_len = len(content.strip())
            if char_len > 50:
                noscript_count += 1
                total_chars += char_len
                affected_urls.add(page["url"])

    if noscript_count > 0:
        ratio = len(affected_urls) / len(pages)
        severity = "high" if ratio > 0.5 else "medium"
        avg_chars = total_chars // noscript_count
        return {
            "id": "CEA-007",
            "title": "Noscript Suggesting JS-Locked Content",
            "severity": severity,
            "evidence": f"Found {noscript_count} <noscript> blocks containing substantive content ({avg_chars} avg chars) across {len(affected_urls)} pages, suggesting primary content requires JavaScript to render.",
            "suggested_action": {
                "summary": "Implement server-side rendering (SSR) or static site generation (SSG) for critical content. Ensure key facts are present in the initial HTML response.",
                "priority": severity
            }
        }
    return None

def check_cea_008(pages: list) -> dict | None:
    bg_count = 0
    affected_urls = set()

    for page in pages:
        soup = page["soup"]
        elements = soup.find_all(style=re.compile(r"background(-image)?\s*:\s*url\(", re.IGNORECASE))
        for el in elements:
            parent = el.find_parent(["nav", "header", "footer"])
            if parent:
                continue
            text = el.get_text(strip=True)
            if not text:
                bg_count += 1
                affected_urls.add(page["url"])

    if bg_count > 0:
        ratio = len(affected_urls) / len(pages)
        severity = "medium" if ratio > 0.5 else "low"
        return {
            "id": "CEA-008",
            "title": "Background Images for Informational Content",
            "severity": severity,
            "evidence": f"Found {bg_count} elements using CSS background-image in content areas with no text children across {len(affected_urls)} pages.",
            "suggested_action": {
                "summary": "Move informational images from CSS backgrounds to <img> tags with descriptive alt text. Reserve CSS background-image for decorative purposes only.",
                "priority": severity
            }
        }
    return None

def check_cea_009(pages: list) -> dict | None:
    product_pages = [p for p in pages if p.get("page_type") == "product"]
    if not product_pages:
        return None

    missing_schema = []
    for page in product_pages:
        has_product = False
        for script in page["soup"].find_all("script", type="application/ld+json"):
            if script.string and ('"Product"' in script.string or '"@type":"Product"' in script.string.replace(" ", "")):
                has_product = True
                break
        if not has_product:
            missing_schema.append(page["url"])

    if missing_schema:
        ratio = len(missing_schema) / len(product_pages)
        severity = "high" if ratio > 0.4 else "medium"
        return {
            "id": "CEA-009",
            "title": "No JSON-LD Structured Data on Product Pages",
            "severity": severity,
            "evidence": f"Found {len(missing_schema)} product pages without Product JSON-LD schema (e.g., {missing_schema[0]}).",
            "suggested_action": {
                "summary": "Add Product structured data (JSON-LD) to all product pages to ensure AI assistants can extract price, availability, and specifications.",
                "priority": severity
            }
        }
    return None

# --- Proactive Recommendations ---

def proactive_speakable_schema(pages: list) -> dict | None:
    has_speakable = False
    for page in pages:
        for script in page["soup"].find_all("script", type="application/ld+json"):
            if script.string and "speakable" in script.string.lower():
                has_speakable = True
                break
        if has_speakable:
            break

    if not has_speakable:
        return {
            "id": "CEA-PRO-001",
            "title": "Add Speakable Schema for Voice-Assistant Quoting",
            "severity": "info",
            "evidence": f"Scanned {len(pages)} pages; none declare schema.org/speakable markup. Voice assistants use speakable to identify which sections of a page are best suited for text-to-speech readout.",
            "suggested_action": {
                "summary": "Add speakable property to your WebPage or Article JSON-LD, pointing at CSS selectors for headline and summary sections. This makes your content eligible for audio news briefings.",
                "priority": "low"
            }
        }
    return None

def proactive_figure_figcaption(pages: list) -> dict | None:
    total_content_images = 0
    images_in_figure = 0

    for page in pages:
        content_area = get_content_area(page["soup"])
        images = content_area.find_all("img")
        for img in images:
            width = img.get("width")
            height = img.get("height")
            if width and str(width).isdigit() and int(width) < 5:
                continue
            if height and str(height).isdigit() and int(height) < 5:
                continue
            src = img.get("src", "")
            if any(x in src.lower() for x in ["spacer", "pixel", "blank", "1x1"]):
                continue
            if src.startswith("data:image") and len(src) < 100:
                continue

            total_content_images += 1
            if img.find_parent("figure"):
                images_in_figure += 1

    if total_content_images > 0:
        pct_in_figure = round((images_in_figure / total_content_images) * 100, 1)
        if pct_in_figure < 50:
            return {
                "id": "CEA-PRO-002",
                "title": "Wrap Content Images in <figure> With <figcaption>",
                "severity": "low",
                "evidence": f"Only {images_in_figure}/{total_content_images} ({pct_in_figure}%) content images across {len(pages)} pages are wrapped in a <figure> element. AI systems use <figcaption> text as a structured, high-confidence description.",
                "suggested_action": {
                    "summary": "Wrap key informational images in <figure> with a <figcaption> that describes the image's content. This gives AI assistants a machine-readable caption to cite alongside the image.",
                    "priority": "low"
                }
            }
    return None

def proactive_data_nosnippet(pages: list) -> dict | None:
    nosnippet_count = 0
    affected_urls = set()

    for page in pages:
        content_area = get_content_area(page["soup"])
        elements = content_area.find_all(attrs={"data-nosnippet": True})
        for el in elements:
            text_len = len(el.get_text(strip=True))
            if text_len > 50:
                nosnippet_count += 1
                affected_urls.add(page["url"])

    if nosnippet_count > 0:
        ratio = len(affected_urls) / len(pages)
        severity = "medium" if ratio > 0.5 else "low"
        urls = list(affected_urls)[:3]
        return {
            "id": "CEA-PRO-003",
            "title": "Content Blocks Marked data-nosnippet Suppress AI Quoting",
            "severity": severity,
            "evidence": f"Found {nosnippet_count} substantive content element(s) with the data-nosnippet attribute across {len(affected_urls)} pages. This HTML attribute prevents AI assistants from quoting that text.",
            "suggested_action": {
                "summary": "Review data-nosnippet usage and remove it from content sections you want AI assistants to cite. Reserve it only for sensitive information.",
                "priority": severity
            }
        }
    return None

def run_audit(url: str, cache_dir: str):
    pages = load_cached_pages(cache_dir)
    if not pages:
        print(json.dumps([]))
        return
        
    usable_pages = [p for p in pages if p.get("content_classification") == "normal"]

    findings = []
    checks = [
        check_cea_001, check_cea_002, check_cea_003, check_cea_004, 
        check_cea_005, check_cea_006, check_cea_007, check_cea_008, check_cea_009
    ]
    
    for idx, check in enumerate(checks):
        try:
            finding = check(usable_pages)
            if finding:
                findings.append(finding)
        except Exception as e:
            sys.stderr.write(f"Check {idx+1} failed: {e}\n")

    proactive_checks = [
        proactive_speakable_schema,
        proactive_figure_figcaption,
        proactive_data_nosnippet,
    ]
    
    for check in proactive_checks:
        try:
            finding = check(usable_pages)
            if finding:
                findings.append(finding)
        except Exception as e:
            sys.stderr.write(f"Proactive check {check.__name__} failed: {e}\n")

    has_defects = any(f.get("severity") in ["critical", "high", "medium", "low"] for f in findings)
    if not has_defects:
        findings.append({
            "id": "CEA-PASS-000",
            "title": "Content Extractability Verified",
            "severity": "info",
            "evidence": f"Scanned {len(usable_pages)} pages and found no major extractability barriers like inaccessible SVGs, JS-locked blocks, or opaque media assets.",
            "suggested_action": None
        })
        
    print(json.dumps(findings, indent=2))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Target URL")
    parser.add_argument("--cache-dir", required=True, help="Cache directory")
    args = parser.parse_args()
    
    run_audit(args.url, args.cache_dir)