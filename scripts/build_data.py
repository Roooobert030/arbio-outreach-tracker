#!/usr/bin/env python3
"""
Arbio Outreach Tracker — Data Builder
Merges Instagram contacts, response logs, and scraped contact data
into a single unified JSON + CSV for the dashboard.
"""

import json
import csv
import re
import time
import os
import sys
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from ddgs import DDGS
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")

# Source paths (adjust if moved)
INSTA_DIR = os.path.expanduser("~/Robert/Claude Insta")
AGGREGATED_JSON = os.path.join(INSTA_DIR, "state/all_contacted_aggregated.json")
RESPONSES_JSON  = os.path.join(INSTA_DIR, "state/responses_log.json")
HUBSPOT_CSV     = os.path.join(INSTA_DIR, "hubspot_leads.csv")
SCRAPE_STATE    = os.path.join(INSTA_DIR, "state/scrape_contact_state.json")

OUTPUT_JSON = os.path.join(DATA_DIR, "contacts.json")
OUTPUT_CSV  = os.path.join(DATA_DIR, "contacts.csv")

# ── Statuses that mean a DM was actually sent ─────────────────────────────────
SENT_STATUSES = {
    "sent_ok", "sent_confirmed_blue_bubble", "sent_ok_after_retry",
    "sent", "sent_unknown", "cant_receive_message",
    "general_tab_message_request", "message_requests_restricted",
    "not_received_confirmed",
}

# ── Contact classification ────────────────────────────────────────────────────
def classify_response(handle: str, responses: dict) -> tuple[str, dict]:
    if handle not in responses:
        return "ghosted", {}
    r = responses[handle]
    fs = r.get("final_status", "")
    sentiment = r.get("sentiment", "")
    if fs in ("closed_no", "closed_no_misunderstood"):
        return "rejected", r
    if fs == "closed_postponed_open_for_future":
        return "postponed", r
    if fs in ("open_email_followup",) or sentiment in ("positive", "positive_acknowledgement", "neutral"):
        return "replied_positive", r
    return "replied", r

# ── Scraping helpers ──────────────────────────────────────────────────────────
EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+\s*[\[\(]?\s*@\s*[\]\)]?\s*[a-zA-Z0-9.\-]+\s*\.\s*[a-zA-Z]{2,}"
)
PHONE_RE = re.compile(
    r"(?:(?:\+|00)\d{1,3}[\s\-/.]?)?(?:\(0\d+\)|\(?\d{2,5}\)?)[\s\-/.]?\d{2,5}[\s\-/.]?\d{2,5}(?:[\s\-/.]?\d{1,5})?"
)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}
FAKE_DOMAINS = {"example.com","example.net","example.org","test.com","domain.com","email.com","sentry.io"}
SKIP_DOMAINS = [
    "instagram.com","facebook.com","booking.com","airbnb","fewo-direkt",
    "holidaycheck","tripadvisor","google.com","youtube.com","tiktok.com",
    "pinterest.com","twitter.com","linkedin.com","wikipedia.org",
    "smoobu.com","microsoft.com","apple.com","bing.com",
    "ferienwohnungen.de","traum-ferienwohnungen.de","hometogo.de",
    "casamundo.de","atraveo.de","e-domizil.de","fewo24.com",
]
IMPRESSUM_PATHS = ["/impressum","/impressum.html","/kontakt","/kontakt.html","/contact"]
IMPRESSUM_KEYWORDS = ["impressum","kontakt","contact","über uns","about"]

def clean_email(raw):
    return re.sub(r"\s*[\[\(]\s*@\s*[\]\)]\s*", "@", raw).replace(" ", "").strip()

def extract_emails(text):
    found = EMAIL_RE.findall(text)
    result = []
    for e in [clean_email(x) for x in found]:
        if "@" not in e: continue
        domain = e.split("@")[-1].lower()
        if domain in FAKE_DOMAINS or "." not in domain: continue
        result.append(e)
    return list(dict.fromkeys(result))

def extract_phones(text):
    found = PHONE_RE.findall(text)
    result = []
    for p in found:
        p = p.strip()
        digits = re.sub(r"\D", "", p)
        if len(digits) < 6 or len(digits) > 15: continue
        if re.match(r'^\d{4}[-./]\d{2}[-./]\d{2}', p): continue
        if not re.match(r'^(?:\+|00|0)', p): continue
        result.append(p)
    seen = set(); deduped = []
    for p in result:
        k = re.sub(r"\D","",p)
        if k not in seen: seen.add(k); deduped.append(p)
    return deduped[:3]

def fetch(url, timeout=10):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if r.status_code == 200: return r.text
    except Exception: pass
    return None

def is_own_website(url):
    return url and not any(s in url for s in SKIP_DOMAINS)

def scrape_website(url):
    emails, phones = [], []
    html = fetch(url)
    if not html: return {"emails": [], "phones": []}
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    emails += extract_emails(text)
    phones += extract_phones(text)
    imp_link = None
    for a in soup.find_all("a", href=True):
        t = a.get_text(strip=True).lower()
        h = a["href"].lower()
        if any(kw in t or kw in h for kw in IMPRESSUM_KEYWORDS):
            imp_link = urljoin(url, a["href"]); break
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    candidates = ([imp_link] if imp_link else []) + [base + p for p in IMPRESSUM_PATHS]
    for imp_url in candidates[:5]:
        if not imp_url: continue
        imp_html = fetch(imp_url)
        if imp_html:
            imp_text = BeautifulSoup(imp_html, "lxml").get_text(" ", strip=True)
            emails += extract_emails(imp_text)
            phones += extract_phones(imp_text)
            if emails: break
    return {"emails": list(dict.fromkeys(emails))[:3], "phones": list(dict.fromkeys(phones))[:3]}

def search_website(handle):
    handle_clean = handle.strip("_").replace("_", " ")
    handle_parts = [p for p in re.split(r'[_\-]+', handle.strip("_")) if len(p) > 2]
    ddgs = DDGS()
    for q in [f'{handle} instagram ferienwohnung', f'"{handle_clean}" ferienwohnung impressum']:
        try:
            results = list(ddgs.text(q, max_results=8))
            for r in results:
                if "instagram.com" not in r.get("href",""): continue
                urls = re.findall(r'https?://(?:www\.)?[a-zA-Z0-9\.\-]+\.[a-zA-Z]{2,}(?:/[^\s,\"\'<>]*)?', r.get("body",""))
                for u in urls:
                    u = u.rstrip("./,")
                    if is_own_website(u): return u
            for r in results:
                url = r.get("href","")
                if not is_own_website(url): continue
                url_lower = url.lower()
                if any(p.lower() in url_lower for p in handle_parts if len(p) > 3): return url
            for r in results:
                url = r.get("href","")
                if is_own_website(url): return url
            time.sleep(0.5)
        except Exception as e:
            print(f"    Search error: {e}")
            time.sleep(3)
    return None

def website_confidence(handle, website):
    if not website: return "none"
    parts = [p for p in re.split(r'[_\-]+', handle.strip("_")) if len(p) > 3]
    url_lower = website.lower()
    if any(p.lower() in url_lower for p in parts): return "high"
    return "medium"

# ── Main build ────────────────────────────────────────────────────────────────
def main():
    scrape_mode = "--scrape" in sys.argv

    print("Loading source data...")
    with open(AGGREGATED_JSON) as f:
        aggregated = json.load(f)
    contacts_raw = aggregated["contacts"]

    with open(RESPONSES_JSON) as f:
        resp_data = json.load(f)
    responses = resp_data.get("responses", {})

    hubspot = {}
    with open(HUBSPOT_CSV) as f:
        for row in csv.DictReader(f):
            hubspot[row["instagram_handle"]] = row

    scrape_state = {}
    if os.path.exists(SCRAPE_STATE):
        with open(SCRAPE_STATE) as f:
            raw = json.load(f)
        for r in raw.get("results", []):
            scrape_state[r["handle"]] = r

    # Filter to actually-sent contacts only
    sent = [c for c in contacts_raw if c.get("status") in SENT_STATUSES]
    print(f"Sent contacts: {len(sent)} | Responses logged: {len(responses)}")

    results = []
    to_scrape = []

    for c in sent:
        handle = c["handle"]
        classification, resp_detail = classify_response(handle, responses)

        # Get website/email from existing data
        hs = hubspot.get(handle, {})
        sc = scrape_state.get(handle, {})

        website = hs.get("website","") or sc.get("website","") or ""
        emails = []
        phones = []
        if hs.get("email_1"): emails.append(hs["email_1"])
        if hs.get("email_2"): emails.append(hs["email_2"])
        if hs.get("email_3"): emails.append(hs["email_3"])
        if hs.get("phone_1"): phones.append(hs["phone_1"])
        if hs.get("phone_2"): phones.append(hs["phone_2"])
        emails = emails or sc.get("emails", [])
        phones = phones or sc.get("phones", [])

        contact = {
            "handle": handle,
            "platform": "instagram",
            "outreach_date": c.get("date",""),
            "status": classification,
            "website": website,
            "website_confidence": website_confidence(handle, website),
            "emails": [e for e in emails if e],
            "phones": [p for p in phones if p],
            "response_snippet": resp_detail.get("response_text_snippet",""),
            "response_date": resp_detail.get("response_date",""),
            "note": resp_detail.get("note",""),
            "persona": c.get("persona",""),
            "icp_segment": c.get("icp_segment",""),
            "has_contact_data": bool(emails or phones),
        }
        results.append(contact)

        if scrape_mode and not website and not emails:
            to_scrape.append(handle)

    # Scrape missing contacts
    if scrape_mode and to_scrape:
        print(f"\nScraping {len(to_scrape)} contacts without website/email...")
        for i, handle in enumerate(to_scrape):
            idx = next(j for j, r in enumerate(results) if r["handle"] == handle)
            print(f"  [{i+1}/{len(to_scrape)}] @{handle}", end="", flush=True)
            website = search_website(handle)
            if website:
                print(f" → {website}", end="", flush=True)
                cd = scrape_website(website)
                results[idx]["website"] = website
                results[idx]["website_confidence"] = website_confidence(handle, website)
                results[idx]["emails"] = cd["emails"]
                results[idx]["phones"] = cd["phones"]
                results[idx]["has_contact_data"] = bool(cd["emails"] or cd["phones"])
                print(f" | emails: {cd['emails']}")
            else:
                print(" → not found")
            time.sleep(1.5)

    # Save JSON
    os.makedirs(DATA_DIR, exist_ok=True)
    output = {
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total_sent": len(results),
        "stats": {
            "replied_positive": sum(1 for r in results if r["status"] == "replied_positive"),
            "rejected": sum(1 for r in results if r["status"] == "rejected"),
            "postponed": sum(1 for r in results if r["status"] == "postponed"),
            "ghosted": sum(1 for r in results if r["status"] == "ghosted"),
            "with_email": sum(1 for r in results if r["emails"]),
            "with_website": sum(1 for r in results if r["website"]),
        },
        "contacts": results,
    }
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    # Save CSV
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        fieldnames = ["handle","platform","outreach_date","status","website",
                      "website_confidence","email_1","email_2","email_3",
                      "phone_1","phone_2","response_snippet","response_date","note","persona","icp_segment"]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in results:
            w.writerow({
                "handle": r["handle"],
                "platform": r["platform"],
                "outreach_date": r["outreach_date"],
                "status": r["status"],
                "website": r["website"],
                "website_confidence": r["website_confidence"],
                "email_1": r["emails"][0] if len(r["emails"]) > 0 else "",
                "email_2": r["emails"][1] if len(r["emails"]) > 1 else "",
                "email_3": r["emails"][2] if len(r["emails"]) > 2 else "",
                "phone_1": r["phones"][0] if len(r["phones"]) > 0 else "",
                "phone_2": r["phones"][1] if len(r["phones"]) > 1 else "",
                "response_snippet": r["response_snippet"],
                "response_date": r["response_date"],
                "note": r["note"],
                "persona": r["persona"],
                "icp_segment": r["icp_segment"],
            })

    print(f"\n✓ Saved {len(results)} contacts")
    print(f"  Replied positive : {output['stats']['replied_positive']}")
    print(f"  Rejected         : {output['stats']['rejected']}")
    print(f"  Postponed        : {output['stats']['postponed']}")
    print(f"  Ghosted          : {output['stats']['ghosted']}")
    print(f"  With email       : {output['stats']['with_email']}")
    print(f"  With website     : {output['stats']['with_website']}")

if __name__ == "__main__":
    main()
