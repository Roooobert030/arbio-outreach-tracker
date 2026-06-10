#!/usr/bin/env python3
"""
CI version of the data builder.
Reads data/contacts.json (already committed), tries to fill in missing
website/email for up to 30 contacts per run, saves updated data.
"""

import json, csv, re, time, os, requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from ddgs import DDGS
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_JSON = os.path.join(DATA_DIR, "contacts.json")
OUTPUT_CSV  = os.path.join(DATA_DIR, "contacts.csv")

BATCH_LIMIT = 30  # max new contacts to scrape per CI run

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36", "Accept-Language": "de-DE,de;q=0.9,en;q=0.8"}
FAKE_DOMAINS = {"example.com","example.net","test.com","domain.com","email.com","sentry.io"}
SKIP_DOMAINS = ["instagram.com","facebook.com","booking.com","airbnb","tripadvisor","google.com","youtube.com","tiktok.com","pinterest.com","twitter.com","linkedin.com","wikipedia.org","smoobu.com","microsoft.com","apple.com","bing.com","ferienwohnungen.de","traum-ferienwohnungen.de","hometogo.de","casamundo.de","atraveo.de","e-domizil.de","fewo24.com"]
IMPRESSUM_PATHS = ["/impressum","/impressum.html","/kontakt","/kontakt.html","/contact"]
IMPRESSUM_KEYWORDS = ["impressum","kontakt","contact","über uns","about"]
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+\s*[\[\(]?\s*@\s*[\]\)]?\s*[a-zA-Z0-9.\-]+\s*\.\s*[a-zA-Z]{2,}")
PHONE_RE = re.compile(r"(?:(?:\+|00)\d{1,3}[\s\-/.]?)?(?:\(0\d+\)|\(?\d{2,5}\)?)[\s\-/.]?\d{2,5}[\s\-/.]?\d{2,5}(?:[\s\-/.]?\d{1,5})?")

def clean_email(raw): return re.sub(r"\s*[\[\(]\s*@\s*[\]\)]\s*","@",raw).replace(" ","").strip()
def extract_emails(text):
    result = []
    for e in [clean_email(x) for x in EMAIL_RE.findall(text)]:
        if "@" not in e: continue
        d = e.split("@")[-1].lower()
        if d in FAKE_DOMAINS or "." not in d: continue
        result.append(e)
    return list(dict.fromkeys(result))
def extract_phones(text):
    result = []
    for p in PHONE_RE.findall(text):
        p = p.strip(); digits = re.sub(r"\D","",p)
        if len(digits)<6 or len(digits)>15: continue
        if re.match(r'^\d{4}[-./]\d{2}[-./]\d{2}',p): continue
        if not re.match(r'^(?:\+|00|0)',p): continue
        result.append(p)
    seen=set(); deduped=[]
    for p in result:
        k=re.sub(r"\D","",p)
        if k not in seen: seen.add(k); deduped.append(p)
    return deduped[:3]

def fetch(url, timeout=10):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if r.status_code == 200: return r.text
    except Exception: pass
    return None

def is_own_website(url): return url and not any(s in url for s in SKIP_DOMAINS)

def scrape_website(url):
    emails, phones = [], []
    html = fetch(url)
    if not html: return {"emails":[],"phones":[]}
    soup = BeautifulSoup(html,"lxml")
    text = soup.get_text(" ",strip=True)
    emails += extract_emails(text); phones += extract_phones(text)
    imp_link = None
    for a in soup.find_all("a",href=True):
        t=a.get_text(strip=True).lower(); h=a["href"].lower()
        if any(kw in t or kw in h for kw in IMPRESSUM_KEYWORDS):
            imp_link=urljoin(url,a["href"]); break
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    for imp_url in ([imp_link] if imp_link else [])+[base+p for p in IMPRESSUM_PATHS][:4]:
        if not imp_url: continue
        imp_html = fetch(imp_url)
        if imp_html:
            emails += extract_emails(BeautifulSoup(imp_html,"lxml").get_text(" ",strip=True))
            phones += extract_phones(BeautifulSoup(imp_html,"lxml").get_text(" ",strip=True))
            if emails: break
    return {"emails":list(dict.fromkeys(emails))[:3],"phones":list(dict.fromkeys(phones))[:3]}

def search_website(handle):
    handle_clean = handle.strip("_").replace("_"," ")
    handle_parts = [p for p in re.split(r'[_\-]+',handle.strip("_")) if len(p)>2]
    ddgs = DDGS()
    for q in [f'{handle} instagram ferienwohnung', f'"{handle_clean}" ferienwohnung impressum']:
        try:
            results = list(ddgs.text(q, max_results=8))
            for r in results:
                if "instagram.com" not in r.get("href",""): continue
                for u in re.findall(r'https?://(?:www\.)?[a-zA-Z0-9\.\-]+\.[a-zA-Z]{2,}(?:/[^\s,\"\'<>]*)?',r.get("body","")):
                    u=u.rstrip("./,")
                    if is_own_website(u): return u
            for r in results:
                url=r.get("href","")
                if not is_own_website(url): continue
                if any(p.lower() in url.lower() for p in handle_parts if len(p)>3): return url
            for r in results:
                url=r.get("href","")
                if is_own_website(url): return url
            time.sleep(0.5)
        except Exception: time.sleep(3)
    return None

def website_confidence(handle, website):
    if not website: return "none"
    parts = [p for p in re.split(r'[_\-]+',handle.strip("_")) if len(p)>3]
    return "high" if any(p.lower() in website.lower() for p in parts) else "medium"

def save_csv(contacts):
    with open(OUTPUT_CSV,"w",newline="",encoding="utf-8") as f:
        fieldnames=["handle","platform","outreach_date","status","website","website_confidence","email_1","email_2","email_3","phone_1","phone_2","response_snippet","response_date","note","persona","icp_segment"]
        w=csv.DictWriter(f,fieldnames=fieldnames); w.writeheader()
        for c in contacts:
            w.writerow({"handle":c["handle"],"platform":c.get("platform","instagram"),"outreach_date":c.get("outreach_date",""),"status":c.get("status",""),"website":c.get("website",""),"website_confidence":c.get("website_confidence","none"),"email_1":c["emails"][0] if len(c.get("emails",[]))>0 else "","email_2":c["emails"][1] if len(c.get("emails",[]))>1 else "","email_3":c["emails"][2] if len(c.get("emails",[]))>2 else "","phone_1":c["phones"][0] if len(c.get("phones",[]))>0 else "","phone_2":c["phones"][1] if len(c.get("phones",[]))>1 else "","response_snippet":c.get("response_snippet",""),"response_date":c.get("response_date",""),"note":c.get("note",""),"persona":c.get("persona",""),"icp_segment":c.get("icp_segment","")})

def main():
    with open(OUTPUT_JSON) as f: data = json.load(f)
    contacts = data["contacts"]

    to_scrape = [c for c in contacts if not c.get("website") and not c.get("emails")]
    print(f"Contacts missing website/email: {len(to_scrape)} | Will scrape up to {BATCH_LIMIT}")
    to_scrape = to_scrape[:BATCH_LIMIT]

    changed = 0
    for i, c in enumerate(to_scrape):
        handle = c["handle"]
        print(f"  [{i+1}/{len(to_scrape)}] @{handle}", end="", flush=True)
        idx = next(j for j,x in enumerate(contacts) if x["handle"]==handle)
        website = search_website(handle)
        if website:
            print(f" → {website}", end="", flush=True)
            cd = scrape_website(website)
            contacts[idx]["website"] = website
            contacts[idx]["website_confidence"] = website_confidence(handle, website)
            contacts[idx]["emails"] = cd["emails"]
            contacts[idx]["phones"] = cd["phones"]
            contacts[idx]["has_contact_data"] = bool(cd["emails"] or cd["phones"])
            print(f" | {cd['emails']}")
            changed += 1
        else:
            print(" → not found")
        time.sleep(1.5)

    data["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    data["stats"]["with_email"] = sum(1 for c in contacts if c.get("emails"))
    data["stats"]["with_website"] = sum(1 for c in contacts if c.get("website"))
    data["contacts"] = contacts

    with open(OUTPUT_JSON,"w",encoding="utf-8") as f: json.dump(data,f,indent=2,ensure_ascii=False)
    save_csv(contacts)
    print(f"\n✓ Updated {changed} contacts. Total with email: {data['stats']['with_email']}")

if __name__ == "__main__":
    main()
