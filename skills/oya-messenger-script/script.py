# --- scripts/browser_submit.py ---
"""Browser-driven onboarding submission for Jumper Local.

Drives the multi-step onboarding form on local.jumpermedia.co (Bubble.io app
with Google Places autocomplete, per the flowchart):

    Step 1 — GMB search/autocomplete: type business name+city, click suggestion
    Step 2 — GMB selection: confirm the picked listing
    Step 3 — Lead details: name / email / phone, then submit

Stage detection is content-driven — after each `analyze_page` we inspect the
markdown + elements to decide what page we're on, so the script handles
single-page and multi-step variants without hardcoding a click sequence.
On any hard failure we fall through to the caller's Xano fallback.

Uses the AgentChrome REST API (GET /api/pool + POST /api/browsers/{id}/command)
directly — same backend the `browser` MCP wraps.
"""

import os
import time
from typing import Optional

import httpx

API_KEY = os.environ.get("BROWSER_API_KEY", "")
API_BASE = os.environ.get("BROWSER_API_BASE", "https://browser.oya.ai").rstrip("/")
FORM_URL = os.environ.get(
    "ONBOARDING_FORM_URL",
    "https://local.jumpermedia.co/onboarding?utm=oya",
)
HEADERS = {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}

_CLIENT: Optional[httpx.Client] = None


def _client() -> httpx.Client:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = httpx.Client(timeout=60, headers=HEADERS)
    return _CLIENT


# ---------------------------------------------------------------------------
# Low-level browser HTTP helpers
# ---------------------------------------------------------------------------

def _resolve_browser() -> str:
    for attempt in range(3):
        try:
            r = _client().get(f"{API_BASE}/api/pool", timeout=10)
            if r.status_code == 200:
                browsers = r.json().get("browsers") or []
                if browsers:
                    return browsers[0].get("id") or ""
        except Exception:
            pass
        if attempt < 2:
            time.sleep(2)
    return ""


def _cmd(bid: str, action: str, params: dict | None = None, timeout: int = 35) -> dict:
    for attempt in range(2):
        try:
            r = _client().post(
                f"{API_BASE}/api/browsers/{bid}/command",
                json={"action": action, "params": params or {}},
                timeout=timeout,
            )
            if r.status_code == 404 and attempt == 0:
                time.sleep(2)
                continue
            try:
                return r.json()
            except Exception:
                return {"ok": False, "error": f"non-JSON ({r.status_code})"}
        except httpx.TimeoutException:
            return {"ok": False, "error": f"{action} timed out after {timeout}s"}
        except Exception as e:
            if attempt == 0:
                time.sleep(2)
                continue
            return {"ok": False, "error": str(e)}
    return {"ok": False, "error": "command failed after retries"}


def _sel(eid: int) -> str:
    return f'[data-ac-id="{eid}"]'


def _analyze(bid: str) -> tuple[str, list[dict]]:
    for attempt in range(2):
        result = _cmd(bid, "analyze", timeout=45)
        if result.get("ok"):
            data = result.get("data") or {}
            md = data.get("markdown") or ""
            els = data.get("elements") or []
            if md or els:
                return md, els
        if attempt == 0:
            time.sleep(1.5)
    return "", []


# ---------------------------------------------------------------------------
# Element-matching helpers
# ---------------------------------------------------------------------------

def _blob(el: dict) -> str:
    return " ".join(
        str(v)
        for v in (
            el.get("type", ""),
            el.get("text", ""),
            el.get("ariaLabel", ""),
            el.get("placeholder", ""),
            el.get("name", ""),
            el.get("id", ""),
            el.get("role", ""),
        )
    ).lower()


def _is_input(el: dict) -> bool:
    etype = str(el.get("type") or "").lower()
    return etype.startswith("input") or etype == "textarea"


def _is_button(el: dict) -> bool:
    etype = str(el.get("type") or "").lower()
    return etype in ("button", "input:submit", "link") or el.get("role") == "button"


def _find_input(elements: list[dict], *any_keywords: str) -> Optional[int]:
    keywords = [k.lower() for k in any_keywords]
    for el in elements:
        if not _is_input(el):
            continue
        if any(k in _blob(el) for k in keywords):
            eid = el.get("id")
            if isinstance(eid, int):
                return eid
    return None


def _find_button(elements: list[dict], *any_keywords: str) -> Optional[int]:
    keywords = [k.lower() for k in any_keywords]
    for el in elements:
        if not _is_button(el):
            continue
        if any(k in _blob(el) for k in keywords):
            eid = el.get("id")
            if isinstance(eid, int):
                return eid
    return None


def _find_first_input(elements: list[dict]) -> Optional[int]:
    for el in elements:
        if _is_input(el):
            eid = el.get("id")
            if isinstance(eid, int):
                return eid
    return None


def _find_suggestion(elements: list[dict], *needles: str) -> Optional[int]:
    """Find an autocomplete suggestion / list item matching any needle text."""
    needles_lc = [n.lower() for n in needles if n]
    for el in elements:
        etype = str(el.get("type") or "").lower()
        role = str(el.get("role") or "").lower()
        if etype not in ("option", "listitem", "div", "li", "link") and "option" not in role and "listitem" not in role:
            continue
        blob = _blob(el)
        if any(n in blob for n in needles_lc):
            eid = el.get("id")
            if isinstance(eid, int):
                return eid
    return None


# ---------------------------------------------------------------------------
# Field actions
# ---------------------------------------------------------------------------

def _click(bid: str, eid: int) -> bool:
    return bool(_cmd(bid, "click", {"selector": _sel(eid)}).get("ok"))


def _type(bid: str, eid: int, text: str) -> bool:
    selector = _sel(eid)
    _cmd(bid, "click", {"selector": selector})
    time.sleep(0.3)
    return bool(_cmd(bid, "type", {"selector": selector, "text": text}).get("ok"))


# ---------------------------------------------------------------------------
# Stage detection
# ---------------------------------------------------------------------------

_SUCCESS_MARKERS = (
    "thank you", "thanks for", "you're all set", "you are all set",
    "we'll be in touch", "submitted", "all set!", "successfully",
    "we received", "got it", "confirmed",
)
_GMB_SEARCH_MARKERS = (
    "search your business", "find your business", "business name",
    "google business profile", "type your business", "search...",
    "find your gmb", "select your business",
)
_GMB_SELECT_MARKERS = (
    "is this your business", "confirm your listing", "is this you",
    "select your listing",
)


def _detect_stage(md: str, elements: list[dict]) -> str:
    md_lc = (md or "").lower()
    if any(m in md_lc for m in _SUCCESS_MARKERS):
        return "complete"
    has_email_input = _find_input(elements, "email", "e-mail") is not None
    has_phone_input = _find_input(elements, "phone", "mobile", "tel") is not None
    has_name_input = _find_input(elements, "full name", "your name", "first name", "last name") is not None
    if has_email_input and (has_phone_input or has_name_input):
        return "step3_lead_details"
    if any(m in md_lc for m in _GMB_SELECT_MARKERS):
        return "step2_select_gmb"
    if any(m in md_lc for m in _GMB_SEARCH_MARKERS):
        return "step1_gmb_search"
    if _find_input(elements, "search", "business", "gmb") is not None:
        return "step1_gmb_search"
    return "unknown"


# ---------------------------------------------------------------------------
# Stage handlers
# ---------------------------------------------------------------------------

def _split_name(full_name: str) -> tuple[str, str]:
    parts = (full_name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _short_query(gmb_name: str) -> str:
    """Google Places autocomplete needs short queries — first 4 words max."""
    words = (gmb_name or "").strip().split()
    return " ".join(words[:4])


def _do_step1_gmb_search(bid: str, payload: dict, elements: list[dict]) -> dict:
    """Type the business name into the autocomplete, click the matching suggestion."""
    gmb_name = (payload.get("gmb_name") or "").strip()
    gmb_address = (payload.get("gmb_address") or "").strip()
    if not gmb_name:
        return {"ok": False, "reason": "no gmb_name in payload"}

    search_eid = (
        _find_input(elements, "search", "business name", "google business", "find your business", "gmb")
        or _find_first_input(elements)
    )
    if search_eid is None:
        return {"ok": False, "reason": "no search input found on step 1"}

    short = _short_query(gmb_name)
    if not _type(bid, search_eid, short):
        return {"ok": False, "reason": "type into search input failed"}

    # Wait for autocomplete dropdown, re-analyze, click first matching suggestion.
    time.sleep(1.2)
    _, els2 = _analyze(bid)
    needles = [gmb_name, short]
    if gmb_address:
        needles.append(" ".join(gmb_address.split(",")[0].split()[:3]))
    suggestion = _find_suggestion(els2, *needles)
    if suggestion is None:
        even_shorter = " ".join(short.split()[:2])
        if even_shorter and even_shorter != short:
            _type(bid, search_eid, even_shorter)
            time.sleep(1.2)
            _, els2 = _analyze(bid)
            suggestion = _find_suggestion(els2, *needles)
    if suggestion is None:
        return {"ok": False, "reason": "no autocomplete suggestion matched"}

    if not _click(bid, suggestion):
        return {"ok": False, "reason": "click suggestion failed"}

    # Advance if a Next/Continue button is present.
    time.sleep(1.0)
    _, els3 = _analyze(bid)
    next_btn = _find_button(els3, "next", "continue", "confirm")
    if next_btn is not None:
        _click(bid, next_btn)
        time.sleep(1.5)

    return {"ok": True}


def _do_step2_select_gmb(bid: str, payload: dict, elements: list[dict]) -> dict:
    """Click the confirm/yes button on the 'is this your business?' page."""
    confirm = _find_button(elements, "yes", "confirm", "this is", "correct", "continue", "next")
    if confirm is None:
        return {"ok": False, "reason": "no confirm button on step 2"}
    if not _click(bid, confirm):
        return {"ok": False, "reason": "click confirm failed"}
    time.sleep(1.5)
    return {"ok": True}


def _do_step3_lead_details(bid: str, payload: dict, elements: list[dict]) -> dict:
    """Fill name / email / phone fields and submit."""
    full_name = (payload.get("full_name") or "").strip()
    email = (payload.get("email") or "").strip()
    phone = (payload.get("phone") or "").strip()
    first_name, last_name = _split_name(full_name)

    plan: list[tuple[str, list[str], str]] = [
        ("full_name", ["full name", "your name"], full_name),
        ("first_name", ["first name", "given name", "fname"], first_name),
        ("last_name", ["last name", "surname", "family name", "lname"], last_name),
        ("email", ["email", "e-mail"], email),
        ("phone", ["phone", "mobile", "tel"], phone),
    ]

    filled: list[str] = []
    skipped: list[str] = []
    current_els = elements
    for label, keywords, value in plan:
        if not value:
            continue
        eid = _find_input(current_els, *keywords)
        if eid is None:
            skipped.append(label)
            continue
        if _type(bid, eid, value):
            filled.append(label)
            time.sleep(0.3)
            _, current_els = _analyze(bid)
        else:
            skipped.append(label)

    if not filled:
        return {"ok": False, "reason": "no lead-detail inputs matched", "skipped": skipped}

    submit = _find_button(
        current_els, "submit", "create account", "get started", "finish",
        "complete", "sign up", "send", "continue", "next",
    )
    if submit is None:
        return {"ok": False, "reason": "submit button not found", "filled": filled}

    if not _click(bid, submit):
        return {"ok": False, "reason": "submit click failed", "filled": filled}
    time.sleep(3)
    return {"ok": True, "filled": filled, "skipped": skipped}


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def submit_onboarding_via_browser(payload: dict) -> dict:
    """Drive the onboarding form. Returns {status, ...}.

    status ∈ {"submitted", "submitted_unverified", "error"}. On any failure
    the caller falls back to the Xano `onboarding_lead_submit` API.
    """
    if not API_KEY:
        return {"status": "error", "reason": "BROWSER_API_KEY not set"}

    bid = _resolve_browser()
    if not bid:
        return {"status": "error", "reason": "no browser available in pool"}

    nav = _cmd(bid, "navigate", {"url": FORM_URL}, timeout=90)
    if not nav.get("ok"):
        return {"status": "error", "reason": f"navigate failed: {nav.get('error', 'unknown')}"}

    time.sleep(2.5)  # Bubble apps need a beat to hydrate.

    trace: list[dict] = []
    last_stage = ""
    stage_repeat = 0
    for iteration in range(8):
        md, els = _analyze(bid)
        if not els:
            trace.append({"iter": iteration, "error": "analyze returned no elements"})
            break

        stage = _detect_stage(md, els)
        trace.append({"iter": iteration, "stage": stage, "elements": len(els)})

        if stage == "complete":
            return {"status": "submitted", "trace": trace, "url": FORM_URL}

        if stage == last_stage:
            stage_repeat += 1
            if stage_repeat >= 2:
                return {"status": "error", "reason": f"stuck at stage={stage}", "trace": trace}
        else:
            stage_repeat = 0
            last_stage = stage

        if stage == "step1_gmb_search":
            r = _do_step1_gmb_search(bid, payload, els)
        elif stage == "step2_select_gmb":
            r = _do_step2_select_gmb(bid, payload, els)
        elif stage == "step3_lead_details":
            r = _do_step3_lead_details(bid, payload, els)
        else:
            return {"status": "error", "reason": "stage=unknown — could not detect form layout", "trace": trace}

        trace[-1]["result"] = r
        if not r.get("ok"):
            return {"status": "error", "reason": r.get("reason", "stage failed"), "trace": trace}

        time.sleep(0.8)

    md, _ = _analyze(bid)
    md_lc = (md or "").lower()
    if any(m in md_lc for m in _SUCCESS_MARKERS):
        return {"status": "submitted", "trace": trace, "url": FORM_URL}
    return {
        "status": "submitted_unverified",
        "trace": trace,
        "url": FORM_URL,
        "post_excerpt": md[:500] if md else "",
    }


# --- scripts/clients.py ---
"""External-service clients used by the messenger SDR handler.

Replaces the runtime portion of the old `_legacy.py`. Only contains what
`handler.py` actually calls at runtime — Xano MCP, Retool DB, Slack notify,
Facebook Graph API. The previous file's `do_*` debug actions are gone.
"""

import json
import os
import uuid

import httpx
import psycopg2
import psycopg2.extras

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ONBOARDING_URL = "https://local.jumpermedia.co/onboarding?utm=oya"

# Retool postgres — credentials should come from env in prod. Fallback kept
# to match the prior _legacy.py behavior so the live skill keeps working
# without forcing an env-var rollout. Rotate and remove the fallback later.
RETOOL_DB_URL = os.environ.get(
    "RETOOL_DB_URL",
    "postgresql://retool:npg_H0EaIfvzmg3Q@ep-small-surf-a6occgdz-pooler.us-west-2"
    ".retooldb.com/retool?sslmode=require",
)

MCP_PROTOCOL_VERSION = "2024-11-05"
MCP_CLIENT_NAME = "oya-messenger-script"
MCP_CLIENT_VERSION = "1.0.0"

SLACK_CHANNEL = os.environ.get("MESSENGER_SLACK_CHANNEL", "jumper-local-tech-support")


# ---------------------------------------------------------------------------
# Retool DB helpers
# ---------------------------------------------------------------------------

def _db_exec(sql: str, params: tuple = ()) -> list[dict]:
    conn = psycopg2.connect(RETOOL_DB_URL, connect_timeout=20)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            conn.commit()
            try:
                return [dict(r) for r in cur.fetchall()]
            except Exception:
                return []
    finally:
        conn.close()


def _retool_lookup(place_id=None, address=None, business_name=None):
    """Look up a lead's email in `backfill_gmbs_names_and_other`.

    Tries place_id → address → business_name in that order. Returns the
    matched email string or None.
    """
    try:
        conn = psycopg2.connect(RETOOL_DB_URL, connect_timeout=15)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if place_id:
                    cur.execute(
                        "SELECT email FROM backfill_gmbs_names_and_other "
                        "WHERE place_id = %s AND email IS NOT NULL LIMIT 1",
                        (place_id,),
                    )
                    row = cur.fetchone()
                    if row:
                        return row["email"]
                if address:
                    cur.execute(
                        "SELECT email FROM backfill_gmbs_names_and_other "
                        "WHERE address ILIKE %s AND email IS NOT NULL LIMIT 1",
                        (f"%{address.strip()}%",),
                    )
                    row = cur.fetchone()
                    if row:
                        return row["email"]
                if business_name:
                    cur.execute(
                        "SELECT email FROM backfill_gmbs_names_and_other "
                        "WHERE business_name ILIKE %s AND email IS NOT NULL LIMIT 1",
                        (f"%{business_name.strip()}%",),
                    )
                    row = cur.fetchone()
                    if row:
                        return row["email"]
        finally:
            conn.close()
    except Exception:
        pass
    return None


def _ensure_onboarding_leads_table() -> None:
    """Create the onboarding leads table on first use. Idempotent."""
    _db_exec("""
        CREATE TABLE IF NOT EXISTS oya_onboarding_leads (
            id SERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            sender_id TEXT,
            gmb_name TEXT,
            gmb_address TEXT,
            place_id TEXT,
            full_name TEXT,
            email TEXT,
            phone TEXT,
            keywords TEXT,
            source TEXT DEFAULT 'oya_messenger',
            tags TEXT DEFAULT 'CHAT LEAD DO NOT CALL',
            status TEXT DEFAULT 'pending'
        )
    """)


# ---------------------------------------------------------------------------
# Xano MCP helpers — initialize + tool call in one session
# ---------------------------------------------------------------------------

def mcp_call_tool(stream_url, tool_name, arguments, api_key=None, timeout=30):
    """MCP initialize → notifications/initialized → tools/call in one client.

    Avoids "Server not initialized" errors by reusing the same httpx.Client
    so Xano sees a single session.
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if api_key:
        headers["Authorization"] = api_key

    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": MCP_CLIENT_NAME, "version": MCP_CLIENT_VERSION},
        },
    }
    notif_payload = {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    }
    tool_payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }

    with httpx.Client(timeout=timeout) as c:
        r = c.post(stream_url, headers=headers, json=init_payload)
        if r.status_code >= 400:
            raise Exception(
                f"MCP initialize error {r.status_code}: "
                f"{r.content.decode('utf-8', errors='replace')[:400]}"
            )

        # Xano returns mcp-session-id as a response header — variations seen
        # across versions, so try a few. Fall back to SSE body if absent.
        session_id = (
            r.headers.get("mcp-session-id")
            or r.headers.get("x-mcp-session-id")
            or r.headers.get("session-id")
            or ""
        )
        if not session_id:
            for line in r.content.decode("utf-8", errors="replace").splitlines():
                if line.startswith("data:"):
                    try:
                        body = json.loads(line[5:].strip())
                        session_id = (
                            body.get("sessionId")
                            or body.get("session_id")
                            or body.get("result", {}).get("sessionId", "")
                        )
                        if session_id:
                            break
                    except (json.JSONDecodeError, ValueError):
                        pass

        # Empty mcp-session-id makes Xano return 400. Only attach if present.
        session_headers = {**headers}
        if session_id:
            session_headers["mcp-session-id"] = session_id

        rn = c.post(stream_url, headers=session_headers, json=notif_payload)
        if rn.status_code >= 400:
            raise Exception(
                f"MCP notifications/initialized error {rn.status_code}: "
                f"{rn.content.decode('utf-8', errors='replace')[:200]}"
            )

        r2 = c.post(stream_url, headers=session_headers, json=tool_payload)
        if r2.status_code >= 400:
            raise Exception(
                f"MCP tool call error {r2.status_code}: "
                f"{r2.content.decode('utf-8', errors='replace')[:400]}"
            )

        raw = r2.content.decode("utf-8")

    data = None
    for line in raw.splitlines():
        if line.startswith("data:"):
            try:
                data = json.loads(line[5:].strip())
                break
            except (json.JSONDecodeError, ValueError):
                pass
    if data is None:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            data = {}

    error = data.get("error")
    if error:
        raise Exception(f"MCP error: {error}")

    result = data.get("result", {})
    content = result.get("content", [])
    if content and isinstance(content, list):
        first = content[0]
        if isinstance(first, dict) and "text" in first:
            try:
                return json.loads(first["text"])
            except (json.JSONDecodeError, TypeError):
                return first["text"]
        return first
    return result


def xano_mcp_get(stream_url, tool_name, arguments, api_key=None, timeout=15):
    """Read-style MCP wrapper. Returns None on not-found, raises on other errors."""
    try:
        result = mcp_call_tool(stream_url, tool_name, arguments, api_key=api_key, timeout=timeout)
    except Exception as e:
        msg = str(e).lower()
        if "not found" in msg or "404" in msg:
            return None
        raise
    if result is None:
        return None
    if isinstance(result, dict) and result.get("status") == "not_found":
        return None
    return result


# ---------------------------------------------------------------------------
# Slack notification
# ---------------------------------------------------------------------------

def _slack_notify_lead(lead: dict) -> None:
    """Post a 'new lead' notification to the Jumper Local Slack channel.

    Reads SLACK_BOT_TOKEN / SLACK_TOKEN from env. Silent failure on any
    error — Slack outages must never block onboarding submission.
    """
    token = (
        os.environ.get("SLACK_BOT_TOKEN")
        or os.environ.get("SLACK_TOKEN")
        or os.environ.get("MESSENGER_SLACK_TOKEN")
        or ""
    ).strip()
    if not token:
        return
    kw_str = ", ".join(lead.get("keywords") or []) or "—"
    text = (
        f":bell: *New Oya Chat Lead — Action Required*\n"
        f"*Business:* {lead.get('gmb_name', '')}\n"
        f"*Address:* {lead.get('gmb_address', '')}\n"
        f"*Place ID:* `{lead.get('place_id', '')}`\n"
        f"*Name:* {lead.get('full_name', '')}\n"
        f"*Email:* {lead.get('email', '')}\n"
        f"*Phone:* {lead.get('phone', '')}\n"
        f"*Keywords:* {kw_str}\n"
        f"*Source:* oya_messenger  •  *Tag:* CHAT LEAD DO NOT CALL\n"
        f"Please create their Jumper Local account: {ONBOARDING_URL}"
    )
    try:
        httpx.post(
            "https://slack.com/api/chat.postMessage",
            json={"channel": SLACK_CHANNEL, "text": text},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Facebook Graph API
# ---------------------------------------------------------------------------

def get_fb_first_name(sender_id: str) -> str:
    """Look up the lead's first name from FB Graph. Empty string on any failure."""
    token = (
        os.environ.get("FB_PAGE_ACCESS_TOKEN")
        or os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN")
        or os.environ.get("PAGE_ACCESS_TOKEN")
        or ""
    ).strip()
    if not token or not sender_id:
        return ""
    try:
        with httpx.Client(timeout=5) as c:
            r = c.get(
                f"https://graph.facebook.com/v19.0/{sender_id}",
                params={"fields": "first_name", "access_token": token},
            )
            if r.status_code == 200:
                return (r.json().get("first_name") or "").strip()
    except Exception:
        pass
    return ""


# --- scripts/dfseo.py ---
"""DataForSEO replacement for Google Places.

Single endpoint — `business_data/google/my_business_info/live` — handles both
the text search (by `keyword=<business name>`) and the place-by-id lookup
(by `keyword=place_id:<id>`). Field shape returned by this module matches
what handler.py used to consume from the Google Places client in _legacy.py.

Auth: DATAFORSEO_LOGIN + DATAFORSEO_PASSWORD (HTTP Basic). No IP allowlist.
"""

import os
import json
from base64 import b64encode

import httpx

BASE = "https://api.dataforseo.com/v3"
ENDPOINT = "business_data/google/my_business_info/live"


def _auth_header():
    login = (os.environ.get("DATAFORSEO_LOGIN") or "").strip()
    password = (os.environ.get("DATAFORSEO_PASSWORD") or "").strip()
    if not login or not password:
        raise RuntimeError("DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD not configured")
    token = b64encode(f"{login}:{password}".encode()).decode()
    return {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
    }


def _post(payload, timeout=30):
    with httpx.Client(timeout=timeout) as c:
        r = c.post(f"{BASE}/{ENDPOINT}", headers=_auth_header(), json=[payload])
        if r.status_code >= 400:
            raise RuntimeError(f"DataForSEO HTTP {r.status_code}: {r.text[:300]}")
        body = r.json()
    tasks = body.get("tasks") or []
    if not tasks:
        return []
    task = tasks[0]
    if task.get("status_code") not in (20000, 20100):
        msg = task.get("status_message") or "DataForSEO task error"
        raise RuntimeError(f"DataForSEO task error: {msg}")
    result = task.get("result") or []
    if not result:
        return []
    return result[0].get("items") or []


def _normalize(item):
    """Convert a DataForSEO GBP item into the dict shape handler.py expects.

    Note: DataForSEO returns `rating` as a nested object
    {rating_type, value, votes_count, rating_max} — we flatten it here.
    """
    rating_obj = item.get("rating") or {}
    if not isinstance(rating_obj, dict):
        rating_obj = {}
    return {
        "place_id": item.get("place_id") or "",
        "name": item.get("title") or "",
        "address": item.get("address") or "",
        "phone": item.get("phone") or "",
        "website": item.get("url") or "",
        "rating": float(rating_obj.get("value") or 0),
        "review_count": int(rating_obj.get("votes_count") or 0),
        "work_time": item.get("work_time") or {},
        "is_claimed": item.get("is_claimed", None),
        "category": item.get("category") or "",
        "raw": item,
    }


# ---------------------------------------------------------------------------
# Public API — drop-in replacements for places_* helpers in _legacy.py
# ---------------------------------------------------------------------------

def places_text_search(query, _places_key_unused=""):
    """Search Google Maps by text. Returns a list of normalized GBP results.

    Compatibility: signature mirrors _legacy.places_text_search(query, key) —
    the second arg is ignored. Empty query returns []. The DataForSEO
    endpoint returns the top match for the keyword (typically 1 item),
    which is fine for the SDR flow that branches on result count.
    """
    q = (query or "").strip()
    if not q:
        return []
    location = os.environ.get("DATAFORSEO_LOCATION", "United States")
    items = _post({
        "keyword": q,
        "location_name": location,
        "language_code": "en",
    })
    return [_normalize(it) for it in items]


def places_details(place_id, _places_key_unused=""):
    """Lookup full GBP detail by place_id. Returns the normalized dict or {}."""
    pid = (place_id or "").strip()
    if not pid:
        return {}
    location = os.environ.get("DATAFORSEO_LOCATION", "United States")
    items = _post({
        "keyword": f"place_id:{pid}",
        "location_name": location,
        "language_code": "en",
    })
    if not items:
        return {}
    return _normalize(items[0])


def extract_place_summary(item):
    """Return {name, address, place_id} from a normalized item."""
    if not isinstance(item, dict):
        return {"name": "", "address": "", "place_id": ""}
    return {
        "name": item.get("name") or item.get("title") or "",
        "address": item.get("address") or "",
        "place_id": item.get("place_id") or "",
    }


def places_full_qualification(place_id, _places_key_unused=""):
    """Run the SDR qualification check against the GBP at place_id.

    Returns {"pass": bool, "reason": str, "details": {...}}. Reason values
    line up with the disqualified_* keys in assets/messages.yaml.
    """
    info = places_details(place_id)
    if not info:
        return {"pass": False, "reason": "no_listing", "details": {}}

    work_time = info.get("work_time") or {}
    has_hours = bool(
        work_time.get("work_hours")
        or work_time.get("timetable")
        or work_time.get("current_status")
    )
    has_website = bool((info.get("website") or "").strip())
    review_count = int(info.get("review_count") or 0)
    rating = float(info.get("rating") or 0)

    details = {
        "has_hours": has_hours,
        "has_website": has_website,
        "review_count": review_count,
        "rating": rating,
    }

    # Check in priority order — first failure wins.
    if not has_hours:
        return {"pass": False, "reason": "no_hours", "details": details}
    if not has_website:
        return {"pass": False, "reason": "no_website", "details": details}
    if review_count < 10:
        return {"pass": False, "reason": "low_reviews", "details": details}
    if rating <= 3.0:
        return {"pass": False, "reason": "low_rating", "details": details}
    return {"pass": True, "reason": "qualified", "details": details}


# --- scripts/handler.py ---
"""Single-entry orchestrator for the Messenger SDR flow.

The agent's LLM calls `oya-messenger-script` with `action=handle_message`
and gets back `{reply, step}`. This module owns the entire state machine:
gate, GMB lookup, qualification, returning-customer check, lead info,
onboarding submission. All verbatim copy is loaded from assets/messages.yaml.

Lower-level integrations (Xano MCP, Retool, Slack, FB Graph) live in
clients.py. Google Places (DataForSEO) lives in dfseo.py.
"""

import os
import re
import sys
import traceback

import state
import messages


def _log_err(label: str, exc: Exception) -> None:
    """Write a one-line error to stderr so run logs capture the real failure
    instead of a generic 'submission_failed' shrug."""
    try:
        print(
            f"[messenger:{label}] {type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        print(traceback.format_exc(limit=4), file=sys.stderr, flush=True)
    except Exception:
        pass

from dfseo import (
    places_text_search,
    places_details,
    places_full_qualification,
    extract_place_summary,
)
from clients import (
    mcp_call_tool,
    xano_mcp_get,
    _retool_lookup,
    _slack_notify_lead,
    _ensure_onboarding_leads_table,
    get_fb_first_name,
)

# ---------------------------------------------------------------------------
# Pattern matchers
# ---------------------------------------------------------------------------

_YES_PAT = re.compile(
    r"^\s*(yes|yep|yeah|yup|y|sure|correct|right|that['s]*\s+(it|us)|confirmed?)\s*[!.]*\s*$",
    re.IGNORECASE,
)
_NO_PAT = re.compile(r"^\s*(no|nope|nah|n|not\s|wrong)\b", re.IGNORECASE)
_EMAIL_PAT = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_PHONE_PAT = re.compile(r"^[+\d][\d\s\-().]{6,}$")
_TRIGGER = "MAPS"
_TERMINAL_STEPS = {
    "completed",
    "session_done",
    "disqualified_no_website",
    "disqualified_low_reviews",
    "disqualified_low_rating",
    "returning_active_sent",
    "returning_expired_sent",
}


def _is_yes(s: str) -> bool:
    return bool(_YES_PAT.match(s or ""))


def _is_no(s: str) -> bool:
    return bool(_NO_PAT.match(s or ""))


def _looks_like_email(s: str) -> bool:
    return bool(_EMAIL_PAT.match((s or "").strip()))


def _looks_like_phone(s: str) -> bool:
    digits = re.sub(r"\D", "", s or "")
    return len(digits) >= 7 and bool(_PHONE_PAT.match((s or "").strip()))


# ---------------------------------------------------------------------------
# Env access
# ---------------------------------------------------------------------------

def _places_key() -> str:
    # Kept for legacy signature compat. DataForSEO uses login/password
    # (read inside dfseo.py) — not a Google API key.
    return ""


def _xano_stream_url() -> str:
    default = "https://xktx-zdsw-4yq2.n7.xano.io/x2/mcp/hEfoWGi_/mcp/stream"
    raw = (os.environ.get("XANO_MCP_STREAM_URL") or os.environ.get("XANO_MCP_STREAM") or default).rstrip("/")
    return raw if raw.startswith(("http://", "https://")) else default


def _xano_api_key() -> str:
    return os.environ.get("XANO_MCP_API_KEY", "").strip()


# ---------------------------------------------------------------------------
# Sub-flows
# ---------------------------------------------------------------------------

def _send_welcome(sender_id: str, lead_first_name: str = "") -> dict:
    state.reset(sender_id)
    state.upsert(sender_id, step="welcome_sent", last_message=_TRIGGER)
    first = (lead_first_name or "").strip()
    if not first:
        first = (get_fb_first_name(sender_id) or "").strip() if sender_id else ""
    if first:
        reply = messages.render("welcome", first_name=first)
    else:
        reply = messages.render("welcome_no_name")
    return {"reply": reply, "step": "welcome_sent"}


def _do_gmb_lookup(sender_id: str, gmb_name: str, address_hint: str = "") -> dict:
    """Search GBPs by name (and address hint), branch on result count."""
    query = gmb_name if not address_hint else f"{gmb_name} {address_hint}"
    try:
        results = places_text_search(query) or []
    except Exception as e:
        _log_err("gmb_lookup", e)
        # Treat as 'couldn't find it' — ask for address so we can retry with hint.
        state.upsert(sender_id, step="awaiting_address", gmb_name=gmb_name)
        return {"reply": messages.render("gmb_no_results"), "step": "awaiting_address"}
    if len(results) == 1:
        summary = extract_place_summary(results[0])
        state.upsert(
            sender_id,
            step="gmb_proposed",
            place_id=summary["place_id"],
            gmb_name=summary["name"],
            gmb_address=summary["address"],
        )
        return {
            "reply": messages.render(
                "gmb_one_result",
                gmb_name=summary["name"],
                gmb_address=summary["address"],
            ),
            "step": "gmb_proposed",
        }
    # 0 or many → ask for address (or treat second pass as confirmed)
    if address_hint and len(results) > 1:
        # Pick first match after address narrowing
        summary = extract_place_summary(results[0])
        state.upsert(
            sender_id,
            step="gmb_proposed",
            place_id=summary["place_id"],
            gmb_name=summary["name"],
            gmb_address=summary["address"],
        )
        return {
            "reply": messages.render(
                "gmb_one_result",
                gmb_name=summary["name"],
                gmb_address=summary["address"],
            ),
            "step": "gmb_proposed",
        }
    state.upsert(sender_id, step="awaiting_address", gmb_name=gmb_name)
    return {
        "reply": messages.render("gmb_multiple_results" if results else "gmb_no_results"),
        "step": "awaiting_address",
    }


def _send_disqual(sender_id: str, reason: str) -> dict:
    """Persist the failure, return the matching verbatim message, set terminal step."""
    key_map = {
        "no_hours": ("disqualified_no_hours", "disqualified_no_hours"),
        "no_website": ("disqualified_no_website", "disqualified_no_website"),
        "low_reviews": ("disqualified_low_reviews", "disqualified_low_reviews"),
        "low_rating": ("disqualified_low_rating", "disqualified_low_rating"),
    }
    msg_key, step_id = key_map.get(reason, ("off_topic_redirect", "session_done"))
    state.upsert(sender_id, step=step_id, disqualification_reason=reason)
    return {"reply": messages.render(msg_key), "step": step_id}


def _check_returning_customer(session: dict) -> dict:
    """Look up an existing Jumper Local account.

    Two-stage: (1) resolve the lead's email from the Retool backfill table by
    place_id → address → business_name; (2) ask Xano `get_gmb` for that email
    and read `nonPayingClient` to decide active vs expired.

    Any integration failure degrades to `new_lead` so we don't strand a real
    lead behind a transient outage.
    """
    place_id = (session.get("place_id") or "").strip()
    address = (session.get("gmb_address") or "").strip()
    business_name = (session.get("gmb_name") or "").strip()
    if not (place_id or address or business_name):
        return {"status": "new_lead"}

    try:
        email = _retool_lookup(
            place_id=place_id or None,
            address=address or None,
            business_name=business_name or None,
        )
    except Exception as e:
        _log_err("retool_lookup", e)
        email = None

    if not email:
        return {"status": "new_lead"}

    try:
        data = xano_mcp_get(
            _xano_stream_url(),
            "get_gmb",
            {"email": email},
            api_key=_xano_api_key(),
            timeout=20,
        )
    except Exception as e:
        _log_err("xano_get_gmb", e)
        data = None

    if not isinstance(data, dict):
        return {"status": "new_lead", "info": {"email": email}}

    non_paying = data.get("nonPayingClient", True)
    status = "expired" if non_paying else "active"
    return {"status": status, "info": {"email": email, **data}}


def _check_email_existing(email: str) -> str:
    """Returns 'current_customer' if email matches an active account, else 'new_lead'.

    Wraps the Xano MCP `customer_lookup_by_email` tool. Errors degrade to 'new_lead'
    so we don't strand a real lead behind an integration hiccup.
    """
    if not email:
        return "new_lead"
    try:
        resp = mcp_call_tool(
            _xano_stream_url(),
            "customer_lookup_by_email",
            {"email": email},
            api_key=_xano_api_key(),
            timeout=20,
        )
        body = resp if isinstance(resp, dict) else {}
        if (body.get("status") or "").lower() == "active":
            return "current_customer"
    except Exception:
        pass
    return "new_lead"


def _qualify_and_advance(sender_id: str, session: dict) -> dict:
    """After GMB confirmation: returning-customer check → qualification → next step."""
    customer = _check_returning_customer(session)
    status = customer.get("status", "new_lead")
    if status == "active":
        state.upsert(sender_id, step="returning_active_sent")
        return {"reply": messages.render("returning_active"), "step": "returning_active_sent"}
    if status == "expired":
        state.upsert(sender_id, step="returning_expired_sent")
        return {"reply": messages.render("returning_expired"), "step": "returning_expired_sent"}
    # New lead — run qualification
    place_id = session.get("place_id") or ""
    qual = places_full_qualification(place_id, _places_key())
    if not qual.get("pass"):
        return _send_disqual(sender_id, qual.get("reason", "no_hours"))
    state.upsert(sender_id, step="collecting_full_name")
    return {"reply": messages.render("ask_full_name"), "step": "collecting_full_name"}


def _submit_onboarding(session: dict) -> dict:
    """Submit the onboarding form. Browser-first, Xano fallback. Slack on success."""
    try:
        _ensure_onboarding_leads_table()
    except Exception:
        pass
    payload = {
        "place_id": session.get("place_id"),
        "gmb_name": session.get("gmb_name"),
        "gmb_address": session.get("gmb_address"),
        "full_name": session.get("full_name"),
        "email": session.get("email"),
        "phone": session.get("phone"),
        "source": "messenger_sdr",
    }

    browser_result: dict = {}
    try:
        from browser_submit import submit_onboarding_via_browser
        browser_result = submit_onboarding_via_browser(payload) or {}
        if browser_result.get("status") not in ("submitted", "submitted_unverified"):
            print(
                f"[messenger:browser_submit] {browser_result.get('reason') or 'unknown failure'}",
                file=sys.stderr,
                flush=True,
            )
    except Exception as e:
        _log_err("browser_submit", e)
        browser_result = {"status": "error", "reason": f"browser path raised: {e}"}

    if browser_result.get("status") in ("submitted", "submitted_unverified"):
        try:
            _slack_notify_lead(payload)
        except Exception:
            pass
        return {"status": "submitted", "via": "browser", "details": browser_result}

    try:
        resp = mcp_call_tool(
            _xano_stream_url(),
            "onboarding_lead_submit",
            payload,
            api_key=_xano_api_key(),
            timeout=30,
        )
        body = resp if isinstance(resp, dict) else {}
        if body.get("status") == "submitted" or body.get("ok"):
            try:
                _slack_notify_lead(payload)
            except Exception:
                pass
            return {"status": "submitted", "via": "xano_fallback", "browser_error": browser_result}
        return {"status": "error", "raw": body, "browser_error": browser_result}
    except Exception as e:
        return {"status": "error", "error": str(e), "browser_error": browser_result}


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def handle_message(sender_id: str, message_text: str, lead_first_name: str = "") -> dict:
    """Single entry point. Returns {reply, step}.

    `reply` is the exact text Hannah should send to the lead. Empty string
    means "send nothing" (gate blocked or terminal silence). `step` echoes
    the new session step for observability — the agent does not act on it.
    """
    sender_id = (sender_id or "").strip()
    msg = (message_text or "").strip()
    msg_upper = msg.upper()

    if not sender_id:
        return {"reply": "", "step": "missing_sender_id", "error": "sender_id required"}

    session = state.get(sender_id)
    step = session.get("step", "new")

    # ---- Activation gate -------------------------------------------------
    # MAPS always resets the session and re-welcomes — even mid-flow. This
    # is the user explicitly re-triggering the agent.
    is_trigger = msg_upper == _TRIGGER
    if is_trigger:
        return _send_welcome(sender_id, lead_first_name)

    # No trigger and no session → silent (gate blocked).
    if step == "new":
        return {"reply": "", "step": step}

    # Terminal sessions: anything other than MAPS is silent.
    if step in _TERMINAL_STEPS:
        return {"reply": "", "step": step}

    # Returning disqualified-by-hours lead — recheck on any inbound
    if step == "disqualified_no_hours":
        place_id = session.get("place_id") or ""
        if place_id:
            qual = places_full_qualification(place_id, _places_key())
            if qual.get("pass"):
                state.upsert(sender_id, step="collecting_full_name", disqualification_reason=None)
                return {"reply": messages.render("ask_full_name"), "step": "collecting_full_name"}
            return _send_disqual(sender_id, qual.get("reason", "no_hours"))
        return {"reply": "", "step": step}

    # ---- Mid-flow dispatch ----------------------------------------------
    if step == "welcome_sent":
        if "jumper" in msg.lower() and "media" in msg.lower():
            # Special case: lead self-lookup as Jumper Media itself
            return {"reply": messages.render("jumper_media_self_lookup"), "step": step}
        if not msg:
            return {"reply": messages.render("off_topic_redirect"), "step": step}
        return _do_gmb_lookup(sender_id, gmb_name=msg)

    if step == "gmb_proposed":
        if _is_yes(msg):
            return _qualify_and_advance(sender_id, session)
        if _is_no(msg):
            state.upsert(sender_id, step="awaiting_address")
            return {"reply": messages.render("gmb_multiple_results"), "step": "awaiting_address"}
        return {"reply": messages.render("off_topic_redirect"), "step": step}

    if step == "awaiting_address":
        if not msg:
            return {"reply": messages.render("off_topic_redirect"), "step": step}
        return _do_gmb_lookup(
            sender_id,
            gmb_name=session.get("gmb_name") or "",
            address_hint=msg,
        )

    if step == "collecting_full_name":
        if not msg or len(msg) > 120:
            return {"reply": messages.render("off_topic_redirect"), "step": step}
        state.upsert(sender_id, full_name=msg, step="collecting_email")
        return {"reply": messages.render("ask_email"), "step": "collecting_email"}

    if step == "collecting_email":
        if not _looks_like_email(msg):
            return {"reply": messages.render("off_topic_redirect"), "step": step}
        state.upsert(sender_id, email=msg)
        if _check_email_existing(msg) == "current_customer":
            state.upsert(sender_id, step="returning_active_sent")
            return {"reply": messages.render("returning_active"), "step": "returning_active_sent"}
        state.upsert(sender_id, step="collecting_phone")
        return {"reply": messages.render("ask_phone"), "step": "collecting_phone"}

    if step == "collecting_phone":
        if not _looks_like_phone(msg):
            return {"reply": messages.render("off_topic_redirect"), "step": step}
        state.upsert(sender_id, phone=msg)
        fresh = state.get(sender_id)
        result = _submit_onboarding(fresh)
        if result.get("status") == "submitted":
            state.upsert(sender_id, step="awaiting_booking")
            return {"reply": messages.render("book_call"), "step": "awaiting_booking"}
        # Submission failed — keep state but tell the lead a human will follow up.
        state.upsert(sender_id, step="submission_failed")
        return {"reply": messages.render("submission_failed"), "step": "submission_failed"}

    if step == "awaiting_booking":
        # Booking confirmation arrives via Calendly webhook, not lead chat.
        # Anything the lead sends here is off-topic until then.
        return {"reply": messages.render("off_topic_redirect"), "step": step}

    # Unknown step — be silent rather than say something wrong.
    return {"reply": "", "step": step}


def post_booking(sender_id: str) -> dict:
    """Called by the Calendly webhook flow when a booking is confirmed."""
    sender_id = (sender_id or "").strip()
    if not sender_id:
        return {"reply": "", "step": "missing_sender_id"}
    state.upsert(sender_id, step="completed")
    return {"reply": messages.render("post_booking"), "step": "completed"}


# --- scripts/messages.py ---
"""Loader for the verbatim copy in assets/messages.yaml + assets/urls.yaml.

Cached at module load. Use render(key, **vars) to produce the final string
with URL/copy variables interpolated. Returns "" for unknown keys so the
orchestrator can degrade silently rather than crashing on a typo.
"""

import os
import yaml

ASSETS_DIR = os.environ.get("SKILL_ASSETS_DIR", "/home/daytona/_skill/assets")


def _load_yaml(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = yaml.safe_load(f) or {}
        except Exception:
            return {}
    return data if isinstance(data, dict) else {}


_MESSAGES = _load_yaml(os.path.join(ASSETS_DIR, "messages.yaml"))
_URLS = _load_yaml(os.path.join(ASSETS_DIR, "urls.yaml"))


def render(key, **vars):
    """Return messages[key] with {placeholders} substituted from urls + vars."""
    template = _MESSAGES.get(key, "")
    if not isinstance(template, str) or not template:
        return ""
    merged = {**_URLS, **vars}
    out = template
    for var_key, value in merged.items():
        out = out.replace("{" + var_key + "}", str(value))
    return out.rstrip()


def url(key):
    return _URLS.get(key, "") if isinstance(_URLS, dict) else ""


# --- scripts/script.py ---
"""Entry point for the messenger-onboarding skill.

Reads INPUT_JSON, dispatches to either:
  • handle_message — the LLM-facing orchestrator (the only action in tool_schema)
  • post_booking_webhook — Calendly webhook hook for post-booking message

Stdout for handle_message / post_booking_webhook is the verbatim reply text
or the literal token `<<SILENT>>` if the script wants the parent to send
nothing. Errors also collapse to `<<SILENT>>`.
"""

import io
import json
import os
import sys

# Force UTF-8 stdout — the Daytona sandbox defaults to ASCII.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import handler  # noqa: E402  — imports messages, state, clients


def _dispatch(inp: dict) -> dict:
    action = (inp.get("action") or "").strip()

    if action == "handle_message":
        return handler.handle_message(
            sender_id=inp.get("sender_id") or "",
            message_text=inp.get("message_text") or "",
            lead_first_name=inp.get("lead_first_name") or "",
        )

    if action == "post_booking_webhook":
        return handler.post_booking(sender_id=inp.get("sender_id") or "")

    return {
        "error": (
            f"Unknown action: '{action}'. "
            "The agent should call action='handle_message' with sender_id + message_text."
        )
    }


def main() -> None:
    # Capture incidental prints from helper modules so they don't pollute the
    # single line we write at the end (the executor reads stdout).
    real_stdout = sys.stdout
    captured = io.StringIO()
    sys.stdout = captured
    inp: dict = {}
    try:
        inp = json.loads(os.environ.get("INPUT_JSON", "{}")) or {}
        result = _dispatch(inp)
    except Exception as e:
        result = {"error": str(e)}
    finally:
        sys.stdout = real_stdout

    action = (inp.get("action") or "").strip() if isinstance(inp, dict) else ""
    is_handle = action in ("handle_message", "post_booking_webhook")

    if is_handle and isinstance(result, dict):
        if result.get("error"):
            print("<<SILENT>>", file=real_stdout)
            return
        reply = (result.get("reply") or "").strip()
        print(reply if reply else "<<SILENT>>", file=real_stdout)
        return

    print(json.dumps(result, default=str, ensure_ascii=False), file=real_stdout)


if __name__ == "__main__":
    main()


# --- scripts/state.py ---
"""SQLite session store for the Messenger SDR state machine.

Lives at /home/daytona/_skill/state.db (the sandbox FS persists across runs
of the same agent). Columns map 1:1 to the orchestrator's working set:
which step we're on, the confirmed GMB, what's been collected so far.
"""

import os
import sqlite3
from typing import Any

DB_PATH = os.environ.get("MESSENGER_STATE_DB", "/home/daytona/_skill/state.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    sender_id TEXT PRIMARY KEY,
    step TEXT NOT NULL DEFAULT 'new',
    place_id TEXT,
    gmb_name TEXT,
    gmb_address TEXT,
    full_name TEXT,
    email TEXT,
    phone TEXT,
    disqualification_reason TEXT,
    last_message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

_FIELDS = (
    "step", "place_id", "gmb_name", "gmb_address",
    "full_name", "email", "phone",
    "disqualification_reason", "last_message",
)


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def get(sender_id: str) -> dict[str, Any]:
    """Return the session row for sender_id, or a fresh {step: 'new'} stub."""
    if not sender_id:
        return {"step": "new"}
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE sender_id = ?", (sender_id,)
        ).fetchone()
    if not row:
        return {"step": "new", "sender_id": sender_id}
    return dict(row)


def upsert(sender_id: str, **fields: Any) -> dict[str, Any]:
    """Create or patch the session for sender_id with the given fields.

    Only known columns are written; unknown keys are silently dropped so
    callers can pass through arbitrary kwargs without a guard.
    """
    if not sender_id:
        raise ValueError("sender_id required")
    clean = {k: v for k, v in fields.items() if k in _FIELDS}
    with _connect() as conn:
        existing = conn.execute(
            "SELECT 1 FROM sessions WHERE sender_id = ?", (sender_id,)
        ).fetchone()
        if existing:
            if clean:
                sets = ", ".join(f"{k} = ?" for k in clean) + ", updated_at = CURRENT_TIMESTAMP"
                conn.execute(
                    f"UPDATE sessions SET {sets} WHERE sender_id = ?",
                    (*clean.values(), sender_id),
                )
        else:
            cols = ["sender_id"] + list(clean.keys())
            placeholders = ", ".join(["?"] * len(cols))
            conn.execute(
                f"INSERT INTO sessions ({', '.join(cols)}) VALUES ({placeholders})",
                (sender_id, *clean.values()),
            )
        conn.commit()
    return get(sender_id)


def reset(sender_id: str) -> None:
    """Drop the session row entirely. Used when a returning lead retriggers MAPS."""
    if not sender_id:
        return
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE sender_id = ?", (sender_id,))
        conn.commit()
