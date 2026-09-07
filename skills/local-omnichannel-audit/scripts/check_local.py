import sys
import json
import argparse
import re
import copy
from pathlib import Path
from bs4 import BeautifulSoup

def load_cached_pages(cache_dir):
    index_path = Path(cache_dir) / "cache_index.json"
    if not index_path.exists():
        return []
    
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []

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

def extract_json_ld(soup):
    json_lds = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            if isinstance(data, list):
                json_lds.extend(data)
            elif isinstance(data, dict):
                if "@graph" in data and isinstance(data["@graph"], list):
                    json_lds.extend(data["@graph"])
                json_lds.append(data)
        except Exception:
            continue
    return json_lds

def is_local_business(schema_type):
    if not schema_type:
        return False
    if isinstance(schema_type, list):
        types = schema_type
    else:
        types = [schema_type]
    
    local_business_types = [
        "LocalBusiness", "Store", "Restaurant", "BarOrPub", "MedicalBusiness",
        "LodgingBusiness", "FinancialService", "FoodEstablishment",
        "HealthAndBeautyBusiness", "HomeAndConstructionBusiness",
        "InternetCafe", "LegalService", "Library", "MovieTheater",
        "MusicVenue", "NightClub", "SportsActivityLocation",
        "TouristInformationCenter", "AutoDealer", "AutoRepair", "GasStation"
    ]
    
    for t in types:
        if isinstance(t, str) and any(lb.lower() in t.lower() for lb in local_business_types):
            return True
    return False

def find_postal_address(obj):
    addresses = []
    if not isinstance(obj, dict):
        return addresses
    
    if obj.get("@type") == "PostalAddress":
        addresses.append(obj)
    
    for key, value in obj.items():
        if isinstance(value, dict):
            addresses.extend(find_postal_address(value))
        elif isinstance(value, list):
            for item in value:
                addresses.extend(find_postal_address(item))
    return addresses

def extract_text(element):
    if not element:
        return ""
    el_copy = copy.copy(element)
    for tag in el_copy(["script", "style", "noscript"]):
        tag.decompose()
    return el_copy.get_text(separator=" ", strip=True)

def run_checks(pages):
    findings = []
    
    has_local_business_schema = False
    postal_address_findings = []
    geo_missing_pages = []
    hours_missing_pages = []
    has_local_business_anywhere = False

    for page in pages:
        json_lds = extract_json_ld(page["soup"])
        page_has_lb = False
        
        for schema in json_lds:
            if not isinstance(schema, dict):
                continue
            
            if is_local_business(schema.get("@type")):
                has_local_business_schema = True
                has_local_business_anywhere = True
                page_has_lb = True
                
                # Check geo
                geo = schema.get("geo")
                has_geo = False
                if geo and isinstance(geo, dict):
                    if "latitude" in geo and "longitude" in geo:
                        has_geo = True
                if not has_geo:
                    geo_missing_pages.append(page["url"])
                
                # Check hours
                if "openingHoursSpecification" not in schema and "openingHours" not in schema:
                    hours_missing_pages.append(page["url"])
                    
            # Check PostalAddress completeness
            addresses = find_postal_address(schema)
            for addr in addresses:
                fields = ["streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry"]
                missing = [f for f in fields if f not in addr or not addr[f]]
                if len(missing) >= 2:
                    postal_address_findings.append({
                        "url": page["url"],
                        "missing": missing
                    })

        # Microdata fallback for LocalBusiness
        if not page_has_lb:
            for tag in page["soup"].find_all(attrs={"itemtype": True}):
                itemtype = tag.get("itemtype", "")
                if any(lb in itemtype for lb in ["LocalBusiness", "Store", "Restaurant"]):
                    has_local_business_schema = True
                    has_local_business_anywhere = True
                    break

    # LOA-001
    try:
        if not has_local_business_schema:
            findings.append({
                "id": "LOA-001",
                "title": "Missing LocalBusiness Schema",
                "severity": "high",
                "evidence": f"Scanned {len(pages)} pages: 0/{len(pages)} contain LocalBusiness, Store, or related schema.org structured data. AI assistants cannot confirm this is a physical business or surface store details.",
                "suggested_action": {
                    "summary": "Add JSON-LD structured data with the appropriate LocalBusiness subtype to the homepage and location pages. Include name, address, telephone, and openingHours at minimum.",
                    "priority": "high"
                }
            })
    except Exception as e:
        sys.stderr.write(f"Error in LOA-001: {e}\n")

    # LOA-002
    try:
        if postal_address_findings:
            f = postal_address_findings[0]
            findings.append({
                "id": "LOA-002",
                "title": "Incomplete PostalAddress",
                "severity": "medium",
                "evidence": f"Found PostalAddress schema on {f['url']} but missing {', '.join(f['missing'])}. Incomplete address prevents AI from giving accurate location answers.",
                "suggested_action": {
                    "summary": "Complete all PostalAddress fields: streetAddress, addressLocality, addressRegion, postalCode, addressCountry.",
                    "priority": "medium"
                }
            })
    except Exception as e:
        sys.stderr.write(f"Error in LOA-002: {e}\n")

    # LOA-003
    try:
        if has_local_business_anywhere and geo_missing_pages:
            urls_str = ", ".join(geo_missing_pages[:2]) + ("..." if len(geo_missing_pages) > 2 else "")
            findings.append({
                "id": "LOA-003",
                "title": "Missing GeoCoordinates",
                "severity": "medium",
                "evidence": f"LocalBusiness schema found but lacks GeoCoordinates on {len(geo_missing_pages)} pages (e.g. {urls_str}). AI cannot answer 'near me' queries.",
                "suggested_action": {
                    "summary": "Add geo coordinates: 'geo': {'@type': 'GeoCoordinates', 'latitude': '...', 'longitude': '...'}.",
                    "priority": "medium"
                }
            })
    except Exception as e:
        sys.stderr.write(f"Error in LOA-003: {e}\n")

    # LOA-004
    try:
        if has_local_business_anywhere and hours_missing_pages:
            urls_str = ", ".join(hours_missing_pages[:2]) + ("..." if len(hours_missing_pages) > 2 else "")
            findings.append({
                "id": "LOA-004",
                "title": "Missing Opening Hours",
                "severity": "medium",
                "evidence": f"LocalBusiness schema found but lacks openingHoursSpecification on {len(hours_missing_pages)} pages (e.g. {urls_str}). AI cannot answer 'Are they open?' queries.",
                "suggested_action": {
                    "summary": "Add openingHoursSpecification with day-by-day hours. Keep synchronized with Google Business Profile.",
                    "priority": "medium"
                }
            })
    except Exception as e:
        sys.stderr.write(f"Error in LOA-004: {e}\n")

    # LOA-005
    try:
        address_patterns = [
            r'\b\d+\s+[\w\s]{2,20}\s+(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Lane|Ln|Drive|Dr|Way|Court|Ct|Place|Pl|Circle|Cir|Highway|Hwy)\b'
        ]
        
        map_without_address = []
        for page in pages:
            soup = page["soup"]
            has_map = False
            for iframe in soup.find_all("iframe"):
                src = iframe.get("src", "").lower()
                if any(m in src for m in ["google.com/maps", "maps.google", "mapbox", "openstreetmap"]):
                    has_map = True
                    break
            if not has_map:
                for el in soup.find_all(class_=lambda c: c and "map" in c.lower()):
                    has_map = True
                    break
            
            if has_map:
                # check address
                has_address = False
                if soup.find("address"):
                    has_address = True
                else:
                    text = extract_text(soup)
                    for pat in address_patterns:
                        if re.search(pat, text, re.IGNORECASE):
                            has_address = True
                            break
                if not has_address:
                    map_without_address.append(page["url"])

        if map_without_address:
            findings.append({
                "id": "LOA-005",
                "title": "Address Not in HTML Text",
                "severity": "high",
                "evidence": f"Found map embed on {map_without_address[0]} but no visible street address in HTML text. Address locked inside iframe is invisible to AI.",
                "suggested_action": {
                    "summary": "Display full street address as visible HTML text alongside map embeds. Use the <address> element.",
                    "priority": "high"
                }
            })
    except Exception as e:
        sys.stderr.write(f"Error in LOA-005: {e}\n")

    # LOA-006
    try:
        phone_pattern = re.compile(r'[\+]?[\d][\d\s\-\.\(\)]{6,}[\d]')
        page_phones = {}
        for page in pages:
            text = extract_text(page["soup"])
            phones = phone_pattern.findall(text)
            norm_phones = set()
            for p in phones:
                norm = re.sub(r'\D', '', p)
                if len(norm) >= 7:
                    norm_phones.add(norm)
            if norm_phones:
                page_phones[page["url"]] = list(norm_phones)[0] # Just take one to compare

        if len(page_phones) > 1:
            urls = list(page_phones.keys())
            first_url = urls[0]
            first_phone = page_phones[first_url]
            inconsistent_url = None
            inconsistent_phone = None
            for u in urls[1:]:
                if page_phones[u] != first_phone:
                    inconsistent_url = u
                    inconsistent_phone = page_phones[u]
                    break
            
            if inconsistent_url:
                findings.append({
                    "id": "LOA-006",
                    "title": "NAP Inconsistency",
                    "severity": "medium",
                    "evidence": f"NAP inconsistency detected: phone appears as '{first_phone}' on {first_url} and '{inconsistent_phone}' on {inconsistent_url}.",
                    "suggested_action": {
                        "summary": "Standardize NAP across every page and structured data. Use single canonical format matching Google Business Profile.",
                        "priority": "medium"
                    }
                })
    except Exception as e:
        sys.stderr.write(f"Error in LOA-006: {e}\n")

    # LOA-007
    try:
        contact_terms = ["contact", "location", "store", "stores", "find-us", "directions", "visit", "visit-us", "where", "branches", "our-stores", "find-a-store", "locations", "outlets"]
        has_contact_page = False
        
        for page in pages:
            url_lower = page["url"].lower()
            if any(term in url_lower for term in contact_terms):
                has_contact_page = True
                break
            
            title = page["soup"].title.string.lower() if page["soup"].title and page["soup"].title.string else ""
            if any(term in title for term in contact_terms):
                has_contact_page = True
                break
            
            for h1 in page["soup"].find_all("h1"):
                h1_text = h1.get_text().lower()
                if any(term in h1_text for term in contact_terms):
                    has_contact_page = True
                    break
            
            if has_contact_page:
                break
                
            for a in page["soup"].find_all("a", href=True):
                href = a["href"].lower()
                if any(term in href for term in contact_terms):
                    has_contact_page = True
                    break
                    
            if has_contact_page:
                break

        if not has_contact_page:
            findings.append({
                "id": "LOA-007",
                "title": "No Contact or Location Page",
                "severity": "high",
                "evidence": f"Scanned {len(pages)} pages and internal links: no dedicated contact or location page found.",
                "suggested_action": {
                    "summary": "Create a dedicated /contact or /locations page with NAP, hours, map, and contact form. Link prominently from site navigation.",
                    "priority": "high"
                }
            })
    except Exception as e:
        sys.stderr.write(f"Error in LOA-007: {e}\n")

    # LOA-008
    try:
        locator_classes = [
            "store-locator", "find-a-store", "location-finder", "store-finder", 
            "map-container", "locations-list", "branch-locator", "dealer-locator", 
            "find-store"
        ]
        
        js_locked = None
        for page in pages:
            for cls in locator_classes:
                elements = page["soup"].find_all(attrs={"class": lambda c: c and cls in c.lower()})
                elements.extend(page["soup"].find_all(attrs={"id": lambda i: i and cls in i.lower()}))
                
                for el in elements:
                    text_len = len(extract_text(el).strip())
                    if text_len < 20:
                        js_locked = {
                            "url": page["url"],
                            "selector": cls,
                            "chars": text_len
                        }
                        break
                if js_locked:
                    break
            if js_locked:
                break

        if js_locked:
            findings.append({
                "id": "LOA-008",
                "title": "Store Locator JS-Locked",
                "severity": "high",
                "evidence": f"Found store locator widget ('{js_locked['selector']}') on {js_locked['url']} containing only {js_locked['chars']} chars of text in static HTML. Store locations require JavaScript.",
                "suggested_action": {
                    "summary": "Server-render the list of store names, addresses, and phone numbers as HTML text. Client-side interactivity can enhance but baseline data must be in initial HTML.",
                    "priority": "high"
                }
            })
    except Exception as e:
        sys.stderr.write(f"Error in LOA-008: {e}\n")

    return findings

def main():
    parser = argparse.ArgumentParser(description="Local Omnichannel Audit")
    parser.add_argument("--url", required=True, help="Target URL")
    parser.add_argument("--cache-dir", required=True, help="Cache directory")
    args = parser.parse_args()

    pages = load_cached_pages(args.cache_dir)
    findings = run_checks(pages)
    
    print(json.dumps(findings, indent=2))

if __name__ == "__main__":
    main()
