import argparse
import json
import sys
import re
from pathlib import Path
from bs4 import BeautifulSoup
import urllib.parse

def load_cached_pages(cache_dir):
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

def check_cea_001(pages):
    total_images = 0
    missing_alt = 0
    affected_urls = set()

    for page in pages:
        content_area = get_content_area(page["soup"])
        images = content_area.find_all("img")
        for img in images:
            width = img.get("width")
            height = img.get("height")
            if width and width.isdigit() and int(width) < 5:
                continue
            if height and height.isdigit() and int(height) < 5:
                continue
            src = img.get("src", "")
            if any(x in src.lower() for x in ["spacer", "pixel", "blank", "1x1"]):
                continue
            if src.startswith("data:image") and len(src) < 100:
                continue
            
            total_images += 1
            alt = img.get("alt")
            if alt is None or alt.strip() == "":
                missing_alt += 1
                affected_urls.add(page["url"])

    if total_images > 0 and (missing_alt / total_images) > 0.3:
        pct = round((missing_alt / total_images) * 100, 1)
        urls = list(affected_urls)[:5]
        return {
            "id": "CEA-001",
            "title": "Images Without Alt Text",
            "severity": "high",
            "evidence": f"Scanned {total_images} content images across {len(pages)} pages; {missing_alt}/{total_images} ({pct}%) lack meaningful alt text. Affected pages: [{', '.join(urls)}]",
            "suggested_action": {
                "summary": "Add descriptive alt text to all informational images. For product images, include the product name, key visual attributes, and any text visible in the image.",
                "priority": "high"
            }
        }
    return None

def check_cea_002(pages):
    canvas_count = 0
    affected_urls = set()

    for page in pages:
        soup = page["soup"]
        canvases = soup.find_all("canvas")
        for canvas in canvases:
            # Skip if inside nav or footer
            parent = canvas.find_parent(["nav", "footer"])
            if parent:
                continue
            
            has_fallback = False
            # check siblings
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
        urls = list(affected_urls)[:5]
        return {
            "id": "CEA-002",
            "title": "Canvas Elements Without Text Fallbacks",
            "severity": "high",
            "evidence": f"Found {canvas_count} <canvas> elements in content areas across {len(pages)} pages with no adjacent text-based data equivalent. Pages: [{', '.join(urls)}]",
            "suggested_action": {
                "summary": "Provide a text-based data table or description adjacent to each canvas element. Use <figcaption> or aria-label to describe the data being visualized.",
                "priority": "high"
            }
        }
    return None

def check_cea_003(pages):
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
        return {
            "id": "CEA-003",
            "title": "SVGs Without Accessible Text",
            "severity": "medium",
            "evidence": f"Found {svg_count} SVG elements in content areas lacking <title>, <desc>, <text>, or ARIA labels across {len(pages)} pages.",
            "suggested_action": {
                "summary": "Add <title> and <desc> elements inside each informational SVG. For icons, add aria-label. For decorative SVGs, add aria-hidden='true'.",
                "priority": "medium"
            }
        }
    return None

def check_cea_004(pages):
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
                # Check nearby text for 'transcript'
                parent = media.parent
                if parent and "transcript" in parent.get_text().lower():
                    has_caption = True

            if not has_caption:
                media_count += 1
                affected_urls.add(page["url"])

    if media_count > 0:
        return {
            "id": "CEA-004",
            "title": "Video/Audio Without Captions",
            "severity": "medium",
            "evidence": f"Found {media_count} video/audio elements across {len(pages)} pages without <track> captions or adjacent transcript links.",
            "suggested_action": {
                "summary": "Add WebVTT caption tracks (<track kind='captions'>) to all video and audio elements. Provide a text transcript link adjacent to embedded media.",
                "priority": "medium"
            }
        }
    return None

def check_cea_005(pages):
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
        example = list(examples)[0] if examples else "table"
        return {
            "id": "CEA-005",
            "title": "Data Tables as Images",
            "severity": "high",
            "evidence": f"Found {img_count} images with data-suggestive filenames/alt text (e.g., '{example}') without adjacent <table> or <dl> markup on {len(pages)} pages.",
            "suggested_action": {
                "summary": "Replace image-based data presentations with semantic HTML <table> or <dl> elements. Keep the image as a visual supplement but ensure the data is also available as structured text.",
                "priority": "high"
            }
        }
    return None

def check_cea_006(pages):
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
                # External domain
                if any(x in domain for x in ["youtube.com", "vimeo.com", "google.com"]):
                    continue
                iframe_count += 1
                if not iframe.get("title"):
                    missing_title += 1
                    affected_urls.add(page["url"])
                    domains.add(domain)

    if missing_title > 0:
        domain_list = list(domains)[:5]
        return {
            "id": "CEA-006",
            "title": "External Iframes Without Title",
            "severity": "low",
            "evidence": f"Found {iframe_count} external iframes across {len(pages)} pages. {missing_title} lack a title attribute, making their content opaque to crawlers and screen readers. Sources: [{', '.join(domain_list)}]",
            "suggested_action": {
                "summary": "Add descriptive title attributes to all iframes. Where possible, provide key content from the iframe as native HTML text on the host page.",
                "priority": "low"
            }
        }
    return None

def check_cea_007(pages):
    noscript_count = 0
    total_chars = 0
    affected_urls = set()

    for page in pages:
        soup = page["soup"]
        noscripts = soup.find_all("noscript")
        for noscript in noscripts:
            content = noscript.get_text()
            if not content:
                # sometimes content is inside the tag as unparsed HTML
                # trying to parse it
                ns_soup = BeautifulSoup(str(noscript.string) if noscript.string else "", "html.parser")
                content = ns_soup.get_text()
            
            # Filter out tiny images and enable js
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
        avg_chars = total_chars // noscript_count
        return {
            "id": "CEA-007",
            "title": "Noscript Suggesting JS-Locked Content",
            "severity": "medium",
            "evidence": f"Found {noscript_count} <noscript> blocks containing substantive content ({avg_chars} avg chars) across {len(pages)} pages, suggesting primary content requires JavaScript to render.",
            "suggested_action": {
                "summary": "Implement server-side rendering (SSR) or static site generation (SSG) for critical content. Ensure key facts are present in the initial HTML response without requiring JavaScript execution.",
                "priority": "medium"
            }
        }
    return None

def check_cea_008(pages):
    bg_count = 0
    affected_urls = set()

    for page in pages:
        soup = page["soup"]
        # Find elements with style attr
        elements = soup.find_all(style=re.compile(r"background(-image)?\s*:\s*url\(", re.IGNORECASE))
        for el in elements:
            # Check if in content area
            parent = el.find_parent(["nav", "header", "footer"])
            if parent:
                continue
            
            # Check text content
            text = el.get_text(strip=True)
            if not text:
                bg_count += 1
                affected_urls.add(page["url"])

    if bg_count > 0:
        return {
            "id": "CEA-008",
            "title": "Background Images for Informational Content",
            "severity": "medium",
            "evidence": f"Found {bg_count} elements using CSS background-image in content areas with no text children across {len(pages)} pages.",
            "suggested_action": {
                "summary": "Move informational images from CSS backgrounds to <img> tags with descriptive alt text. Reserve CSS background-image for decorative purposes only.",
                "priority": "medium"
            }
        }
    return None

def check_cea_009(pages):
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
        return {
            "title": "No JSON-LD Structured Data on Product Pages",
            "severity": "high",
            "evidence": f"Found {len(missing_schema)} product pages without Product JSON-LD schema (e.g., {missing_schema[0]}).",
            "suggested_action": {
                "summary": "Add Product structured data (JSON-LD) to all product pages to ensure AI assistants can extract price, availability, and specifications.",
                "priority": "high"
            }
        }
    return None

def run_audit(url, cache_dir):
    pages = load_cached_pages(cache_dir)
    if not pages:
        print(json.dumps([]))
        return
    usable_pages = [
        p for p in pages
        if p.get("content_classification") == "normal"
    ]

    findings = []
    # Added check_cea_009 to the array
    checks = [check_cea_001, check_cea_002, check_cea_003, check_cea_004, 
              check_cea_005, check_cea_006, check_cea_007, check_cea_008, check_cea_009]
    
    for idx, check in enumerate(checks):
        try:
            finding = check(usable_pages)
            if finding:
                findings.append(finding)
        except Exception as e:
            sys.stderr.write(f"Check {idx+1} failed: {e}\n")

    # Rename IDs sequentially
    for i, finding in enumerate(findings):
        finding["id"] = f"CEA-{i+1:03d}"

    # Fallback info finding if everything passes
    if not findings:
        findings.append({
            "id": "CEA-PASS-000",
            "title": "Content Extractability Verified",
            "severity": "info",
            "evidence": f"Scanned {len(usable_pages)} pages and found no missing product schemas, inaccessible SVGs, uncaptioned media, or JS-locked content blocks.",
            "suggested_action": None
        })
        
    print(json.dumps(findings, indent=2))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Target URL")
    parser.add_argument("--cache-dir", required=True, help="Cache directory")
    args = parser.parse_args()
    
    run_audit(args.url, args.cache_dir)
