import os
import json
import time
from base64 import b64encode
import httpx

BASE = "https://api.dataforseo.com/v3"

try:
    login = os.environ["DATAFORSEO_LOGIN"]
    password = os.environ["DATAFORSEO_PASSWORD"]
    auth = b64encode(f"{login}:{password}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json",
    }
    inp = json.loads(os.environ.get("INPUT_JSON", "{}"))
    action = inp.get("action", "")
    loc = inp.get("location_name") or "United States"
    lang = "en"

    def post(endpoint, payload, timeout=30):
        with httpx.Client(timeout=timeout) as c:
            r = c.post(f"{BASE}/{endpoint}", headers=headers, json=[payload])
            r.raise_for_status()
            data = r.json()
            tasks = data.get("tasks", [])
            if tasks and tasks[0].get("result"):
                return tasks[0]["result"]
            return data

    if action == "keyword_suggestions":
        result = post("dataforseo_labs/google/keyword_suggestions/live", {
            "keyword": inp.get("keyword", ""),
            "location_name": loc,
            "language_code": lang,
            "limit": inp.get("limit", 20),
        })
        items = []
        if isinstance(result, list) and result:
            for item in (result[0].get("items") or []):
                kd = item.get("keyword_data", {}) or {}
                ki = kd.get("keyword_info", {}) or {}
                items.append({
                    "keyword": kd.get("keyword", ""),
                    "search_volume": ki.get("search_volume"),
                    "competition": ki.get("competition"),
                    "cpc": ki.get("cpc"),
                })
        print(json.dumps({"keywords": items, "count": len(items)}))

    elif action == "search_volume":
        kws = [k.strip() for k in inp.get("keywords", "").split(",") if k.strip()]
        result = post("keywords_data/google_ads/search_volume/live", {
            "keywords": kws[:700],
            "location_name": loc,
            "language_code": lang,
        })
        items = []
        if isinstance(result, list):
            for item in result:
                items.append({
                    "keyword": item.get("keyword", ""),
                    "search_volume": item.get("search_volume"),
                    "competition": item.get("competition"),
                    "cpc": item.get("cpc"),
                })
        print(json.dumps({"results": items, "count": len(items)}))

    elif action == "keyword_difficulty":
        kws = [k.strip() for k in inp.get("keywords", "").split(",") if k.strip()]
        result = post("dataforseo_labs/google/bulk_keyword_difficulty/live", {
            "keywords": kws[:700],
            "location_name": loc,
            "language_code": lang,
        })
        items = []
        if isinstance(result, list) and result:
            for item in (result[0].get("items") or []):
                items.append({
                    "keyword": item.get("keyword", ""),
                    "difficulty": item.get("keyword_difficulty"),
                })
        print(json.dumps({"results": items, "count": len(items)}))

    elif action == "onpage_analysis":
        result = post("on_page/instant_pages", {
            "url": inp.get("url", ""),
            "load_resources": True,
            "enable_javascript": True,
            "enable_browser_rendering": True,
        }, timeout=120)
        page = {}
        if isinstance(result, list) and result:
            items = result[0].get("items") or []
            if items:
                r0 = items[0]
                meta = r0.get("meta", {}) or {}
                page = {
                    "url": r0.get("url", ""),
                    "status_code": r0.get("status_code"),
                    "title": meta.get("title", ""),
                    "description": meta.get("description", ""),
                    "h1": meta.get("htags", {}).get("h1", []),
                    "h2": meta.get("htags", {}).get("h2", []),
                    "word_count": r0.get("page_timing", {}).get("duration_time"),
                    "size": r0.get("size"),
                    "is_https": r0.get("is_https"),
                    "internal_links": r0.get("internal_links_count"),
                    "external_links": r0.get("external_links_count"),
                }
        print(json.dumps(page))

    elif action == "backlinks_summary":
        result = post("backlinks/summary/live", {
            "target": inp.get("domain", ""),
            "include_subdomains": True,
            "rank_scale": "one_hundred",
        })
        summary = {}
        if isinstance(result, list) and result:
            r0 = result[0]
            summary = {
                "domain": inp.get("domain", ""),
                "backlinks": r0.get("backlinks"),
                "referring_domains": r0.get("referring_domains"),
                "rank": r0.get("rank"),
            }
        print(json.dumps(summary))

    elif action == "domain_age":
        result = post("domain_analytics/whois/overview/live", {
            "limit": 1,
            "filters": [["domain", "=", inp.get("domain", "")]],
        })
        info = {}
        if isinstance(result, list) and result:
            items = result[0].get("items") or []
            if items:
                info = {
                    "domain": inp.get("domain", ""),
                    "created_date": items[0].get("creation_date"),
                    "expiration_date": items[0].get("expiration_date"),
                    "registrar": items[0].get("registrar", {}).get("name"),
                }
        print(json.dumps(info))

    elif action == "gbp_info":
        place_id = inp.get("place_id", "")
        result = post("business_data/google/my_business_info/live", {
            "keyword": f"place_id:{place_id}",
            "location_name": loc,
            "language_code": lang,
        })
        info = {}
        if isinstance(result, list) and result:
            items = result[0].get("items") or []
            if items:
                r0 = items[0]
                info = {
                    "title": r0.get("title"),
                    "category": r0.get("category"),
                    "address": r0.get("address"),
                    "phone": r0.get("phone"),
                    "url": r0.get("url"),
                    "rating": r0.get("rating"),
                    "total_reviews": r0.get("reviews_count"),
                    "place_id": place_id,
                }
        print(json.dumps(info))

    else:
        print(json.dumps({"error": f"Unknown action: {action}"}))

except Exception as e:
    print(json.dumps({"error": str(e)}))
