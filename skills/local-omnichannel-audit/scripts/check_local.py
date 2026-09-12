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
                pages.append({
                    "url": page["url"], 
                    "html": html, 
                    "soup": soup,
                    "content_classification": page.get("content_classification")
                })
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
    usable_pages = [p for p in pages if p.get("content_classification") == "normal"]

    if not usable_pages:
        return findings

    # --- 1. GATE CHECK: Does this brand have a physical footprint? ---
    physical_signals = []
    retail_terms = [
        "store locator", "find a store", "our stores", "our locations", 
        "visit us", "find us", "where to buy", "retailers", "stockists", "locations"
    ]
    locator_classes = [
        "store-locator", "find-a-store", "location-finder", "store-finder", 
        "locations-list", "branch-locator", "dealer-locator"
    ]
    
    for page in usable_pages:
        soup = page["soup"]
        
        json_lds = extract_json_ld(soup)
        for schema in json_lds:
            if not isinstance(schema, dict): continue
            if is_local_business(schema.get("@type")):
                physical_signals.append("LocalBusiness Schema")
            if find_postal_address(schema):
                physical_signals.append("PostalAddress Schema")
        
        for iframe in soup.find_all("iframe"):
            src = iframe.get("src", "").lower()
            if any(m in src for m in ["google.com/maps", "maps.google", "mapbox", "openstreetmap"]):
                physical_signals.append("Embedded Map")
                
        for cls in locator_classes:
            if soup.find(attrs={"class": lambda c: c and cls in c.lower()}) or soup.find(attrs={"id": lambda i: i and cls in i.lower()}):
                physical_signals.append("Store Locator Widget")
                
        for a in soup.find_all("a", href=True):
            a_text = a.get_text(separator=" ", strip=True).lower()
            href = a["href"].lower()
            if any(term in a_text for term in retail_terms) or any(term.replace(" ", "-") in href for term in retail_terms):
                physical_signals.append("Retail Navigation Links")

    physical_signals = list(set(physical_signals))

    # Graceful exit for pure digital brands
    if not physical_signals:
        return [{
            "id": "LOA-PASS-000",
            "title": "Digital-First Brand Detected (Omnichannel Skipped)",
            "severity": "info",
            "evidence": "No physical retail signals (e.g., PostalAddress schema, map embeds, 'store locator' links) were detected across sampled pages. Assuming pure e-commerce or digital footprint; local omnichannel SEO audits bypassed to prevent false positives.",
            "suggested_action": None
        }]

    lb_page_count = 0
    postal_address_findings = []
    geo_missing_pages = []
    hours_missing_pages = []
    has_local_business_anywhere = False

    for page in usable_pages:
        json_lds = extract_json_ld(page["soup"])
        page_has_lb = False
        
        for schema in json_lds:
            if not isinstance(schema, dict):
                continue
            
            if is_local_business(schema.get("@type")):
                page_has_lb = True
                has_local_business_anywhere = True
                
                geo = schema.get("geo")
                has_geo = False
                if geo and isinstance(geo, dict):
                    if "latitude" in geo and "longitude" in geo:
                        has_geo = True
                if not has_geo:
                    geo_missing_pages.append(page["url"])
                
                if "openingHoursSpecification" not in schema and "openingHours" not in schema:
                    hours_missing_pages.append(page["url"])
                    
            addresses = find_postal_address(schema)
            for addr in addresses:
                fields = ["streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry"]
                missing = [f for f in fields if f not in addr or not addr[f]]
                if len(missing) >= 2:
                    postal_address_findings.append({
                        "url": page["url"],
                        "missing": missing
                    })

        if not page_has_lb:
            for tag in page["soup"].find_all(attrs={"itemtype": True}):
                itemtype = tag.get("itemtype", "")
                if any(lb in itemtype for lb in ["LocalBusiness", "Store", "Restaurant"]):
                    page_has_lb = True
                    has_local_business_anywhere = True
                    break
                    
        if page_has_lb:
            lb_page_count += 1

    # LOA-001: Dynamic Severity based on coverage
    try:
        if lb_page_count < len(usable_pages):
            if lb_page_count == 0:
                severity = "high"
                title = "Missing LocalBusiness Schema on Physical Brand"
                evidence_text = f"Physical footprint signals detected ({', '.join(physical_signals)}), but 0/{len(usable_pages)} sampled pages contain LocalBusiness, Store, or related structured data."
            else:
                ratio = lb_page_count / len(usable_pages)
                severity = "high" if ratio < 0.3 else "medium"
                title = "Sparse LocalBusiness Schema Coverage"
                evidence_text = f"LocalBusiness schema found, but only on {lb_page_count}/{len(usable_pages)} pages. Incomplete coverage limits AI confidence."

            findings.append({
                "id": "LOA-001",
                "title": title,
                "severity": severity,
                "evidence": evidence_text,
                "suggested_action": {
                    "summary": "Ensure JSON-LD structured data with the appropriate LocalBusiness subtype is embedded consistently on the homepage and all location pages.",
                    "priority": severity
                }
            })
    except Exception as e:
        sys.stderr.write(f"Error in LOA-001: {e}\n")

    # LOA-002: Dynamic Severity based on quantity of incomplete addresses
    try:
        if postal_address_findings:
            ratio = len(postal_address_findings) / max(1, lb_page_count)
            severity = "high" if ratio > 0.5 else "medium"
            f = postal_address_findings[0]
            findings.append({
                "id": "LOA-002",
                "title": "Incomplete PostalAddress",
                "severity": severity,
                "evidence": f"Found incomplete PostalAddress schema on {len(postal_address_findings)} page(s) (e.g. {f['url']} missing {', '.join(f['missing'])}). Incomplete addresses prevent AI from giving accurate location answers.",
                "suggested_action": {
                    "summary": "Complete all PostalAddress fields: streetAddress, addressLocality, addressRegion, postalCode, addressCountry.",
                    "priority": severity
                }
            })
    except Exception as e:
        sys.stderr.write(f"Error in LOA-002: {e}\n")

    # LOA-003: Missing Geo
    try:
        if has_local_business_anywhere and geo_missing_pages:
            ratio = len(geo_missing_pages) / max(1, lb_page_count)
            severity = "high" if ratio > 0.5 else "medium"
            urls_str = ", ".join(geo_missing_pages[:2]) + ("..." if len(geo_missing_pages) > 2 else "")
            findings.append({
                "id": "LOA-003",
                "title": "Missing GeoCoordinates",
                "severity": severity,
                "evidence": f"LocalBusiness schema lacks GeoCoordinates on {len(geo_missing_pages)} pages (e.g. {urls_str}). AI cannot answer 'near me' queries.",
                "suggested_action": {
                    "summary": "Add geo coordinates: 'geo': {'@type': 'GeoCoordinates', 'latitude': '...', 'longitude': '...'}.",
                    "priority": severity
                }
            })
    except Exception as e:
        sys.stderr.write(f"Error in LOA-003: {e}\n")

    # LOA-004: Missing Hours
    try:
        if has_local_business_anywhere and hours_missing_pages:
            ratio = len(hours_missing_pages) / max(1, lb_page_count)
            severity = "high" if ratio > 0.5 else "medium"
            urls_str = ", ".join(hours_missing_pages[:2]) + ("..." if len(hours_missing_pages) > 2 else "")
            findings.append({
                "id": "LOA-004",
                "title": "Missing Opening Hours",
                "severity": severity,
                "evidence": f"LocalBusiness schema lacks openingHoursSpecification on {len(hours_missing_pages)} pages (e.g. {urls_str}). AI cannot answer 'Are they open?' queries.",
                "suggested_action": {
                    "summary": "Add openingHoursSpecification with day-by-day hours. Keep synchronized with Google Business Profile.",
                    "priority": severity
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
        for page in usable_pages:
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
            severity = "high" if len(map_without_address) > 1 else "medium"
            findings.append({
                "id": "LOA-005",
                "title": "Address Not in HTML Text",
                "severity": severity,
                "evidence": f"Found map embeds on {len(map_without_address)} page(s) (e.g. {map_without_address[0]}) but no visible street address in HTML text. Addresses locked inside iframes are invisible to AI.",
                "suggested_action": {
                    "summary": "Display full street address as visible HTML text alongside map embeds. Use the <address> element.",
                    "priority": severity
                }
            })
    except Exception as e:
        sys.stderr.write(f"Error in LOA-005: {e}\n")

    # LOA-006: Phone inconsistency
    try:
        page_phones = {}
        for page in usable_pages:
            soup = page.get("soup")
            if not soup:
                continue
                
            tel_links = soup.find_all("a", href=re.compile(r"^tel:", re.I))
            norm_phones = set()
            for link in tel_links:
                raw_phone = link.get("href", "").replace("tel:", "").strip()
                norm = re.sub(r'[^\d\+]', '', raw_phone)
                if 7 <= len(norm) <= 15:
                    norm_phones.add(norm)
                    
            if norm_phones:
                page_phones[page["url"]] = list(norm_phones)[0]

        if len(set(page_phones.values())) > 1:
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
            
            # Quality: Multiple conflicting semantic numbers cause major AI hallucinations.
            severity = "high" if len(set(page_phones.values())) >= 3 else "medium"
            
            if inconsistent_url:
                findings.append({
                    "id": "LOA-006",
                    "title": "Inconsistent Phone Numbers Detected",
                    "severity": severity,
                    "evidence": f"Found {len(set(page_phones.values()))} conflicting semantic phone links (tel:) across pages. Example: '{first_phone}' on {first_url} vs '{inconsistent_phone}' on {inconsistent_url}.",
                    "suggested_action": {
                        "summary": "Standardize your primary contact number across all 'tel:' links and footer elements to ensure AI agents do not surface conflicting contact info.",
                        "priority": severity
                    }
                })
    except Exception as e:
        sys.stderr.write(f"Error in LOA-006: {e}\n")

    # LOA-007
    try:
        contact_terms = ["location", "store", "stores", "find-us", "directions", "visit", "visit-us", "where", "branches", "our-stores", "find-a-store", "locations", "outlets"]
        has_contact_page = False
        
        for page in usable_pages:
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
                "title": "No Omnichannel Location Page",
                "severity": "high",
                "evidence": f"Physical brand footprint detected ({', '.join(physical_signals)}), but scanned {len(usable_pages)} pages/links and found no dedicated location page.",
                "suggested_action": {
                    "summary": "Create a dedicated /locations page with NAP, hours, and map. Link prominently from site navigation.",
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
        for page in usable_pages:
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

    # -------------------------------------------------------------------
    # Proactive beyond-problem recommendations
    # -------------------------------------------------------------------

    # LOA-PRO-001: areaServed for geo/near-me queries
    try:
        has_area_served = False
        for page in usable_pages:
            json_lds = extract_json_ld(page["soup"])
            for schema in json_lds:
                if not isinstance(schema, dict):
                    continue
                if is_local_business(schema.get("@type")) and schema.get("areaServed"):
                    has_area_served = True
                    break
            if has_area_served:
                break

        if has_local_business_anywhere and not has_area_served:
            findings.append({
                "id": "LOA-PRO-001",
                "title": "Add areaServed to Capture 'Near Me' Queries",
                "severity": "low",
                "evidence": f"LocalBusiness schema was found but none of the {len(usable_pages)} sampled pages declare an areaServed property. AI assistants use areaServed to match businesses with location-based queries.",
                "suggested_action": {
                    "summary": "Add areaServed to your LocalBusiness JSON-LD with either a GeoCircle, an AdministrativeArea, or a GeoShape to explicitly tell AI assistants which geographic queries your locations match.",
                    "priority": "low"
                }
            })
    except Exception as e:
        sys.stderr.write(f"Error in LOA-PRO-001: {e}\n")

    # LOA-PRO-002: Google Business Profile cross-linking via sameAs
    try:
        has_gbp_link = False
        gbp_patterns = ["google.com/maps", "goo.gl/maps", "business.google.com", "maps.app.goo.gl"]
        
        for page in usable_pages:
            json_lds = extract_json_ld(page["soup"])
            for schema in json_lds:
                if not isinstance(schema, dict):
                    continue
                if not is_local_business(schema.get("@type")):
                    continue
                same_as = schema.get("sameAs", [])
                if isinstance(same_as, str):
                    same_as = [same_as]
                if isinstance(same_as, list):
                    for link in same_as:
                        if isinstance(link, str) and any(p in link.lower() for p in gbp_patterns):
                            has_gbp_link = True
                            break
                if has_gbp_link:
                    break
            if has_gbp_link:
                break

        if has_local_business_anywhere and not has_gbp_link:
            findings.append({
                "id": "LOA-PRO-002",
                "title": "Cross-Link LocalBusiness Schema With Google Business Profile",
                "severity": "low",
                "evidence": "LocalBusiness schema was detected but none include a sameAs link to a Google Business Profile or Google Maps listing. Cross-linking establishes entity equivalence for AI knowledge graphs.",
                "suggested_action": {
                    "summary": "Add your Google Business Profile URL to the sameAs array in your LocalBusiness JSON-LD to build confident, citation-rich answers about your business.",
                    "priority": "low"
                }
            })
    except Exception as e:
        sys.stderr.write(f"Error in LOA-PRO-002: {e}\n")

    # LOA-PRO-003: potentialAction for voice-assistant direct actions
    try:
        has_action = False
        for page in usable_pages:
            json_lds = extract_json_ld(page["soup"])
            for schema in json_lds:
                if not isinstance(schema, dict):
                    continue
                if is_local_business(schema.get("@type")) and schema.get("potentialAction"):
                    has_action = True
                    break
            if has_action:
                break

        if has_local_business_anywhere and not has_action:
            findings.append({
                "id": "LOA-PRO-003",
                "title": "Add potentialAction for Voice-Assistant Direct Actions",
                "severity": "low",
                "evidence": "LocalBusiness schema was detected but none declare potentialAction properties. Voice assistants use potentialAction to enable hands-free actions directly from the search result.",
                "suggested_action": {
                    "summary": "Add potentialAction to your LocalBusiness JSON-LD (e.g., ReserveAction for booking systems or OrderAction for retail) to enable direct conversions from AI chat interfaces.",
                    "priority": "low"
                }
            })
    except Exception as e:
        sys.stderr.write(f"Error in LOA-PRO-003: {e}\n")

    # Gate logic for ALL-CLEAR info finding
    has_defects = any(f.get("severity") in ["critical", "high", "medium"] for f in findings)
    if not has_defects:
        findings.append({
            "id": "LOA-INFO-ALL-CLEAR",
            "title": "Omnichannel Discoverability Verified",
            "severity": "info",
            "evidence": f"Physical footprint signals detected ({', '.join(physical_signals)}). No major omnichannel schema, NAP inconsistency, or locator discoverability issues were observed.",
            "suggested_action": None
        })

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