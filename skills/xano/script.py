import os
import json

try:
    inp = json.loads(os.environ.get("INPUT_JSON", "{}"))
    action = inp.get("action", "")

    instance_domain = os.environ.get("XANO_INSTANCE_DOMAIN", "").strip().rstrip("/")
    api_base = os.environ.get("XANO_API_GROUP_BASE_URL", "").strip().rstrip("/")
    meta_key = os.environ.get("XANO_METADATA_API_KEY", "").strip()
    auth_token = os.environ.get("XANO_AUTH_TOKEN", "").strip()

    import httpx

    def _meta_url(path):
        """Build Metadata API URL."""
        return f"https://{instance_domain}/api:meta{path}"

    def _meta_headers():
        return {
            "Authorization": f"Bearer {meta_key}",
            "Content-Type": "application/json",
        }

    def _api_headers():
        h = {"Content-Type": "application/json"}
        if auth_token:
            h["Authorization"] = f"Bearer {auth_token}"
        return h

    def _parse_json(s):
        if not s:
            return {}
        if isinstance(s, dict):
            return s
        return json.loads(s)

    def _resolve_table_id(table_name, client):
        """Resolve table name to table ID via Metadata API."""
        r = client.get(_meta_url("/table"), headers=_meta_headers())
        tables = r.json() if r.status_code == 200 else []
        table = next((t for t in tables if t.get("name") == table_name), None)
        return table["id"] if table else None

    # ── Custom API endpoint call (GET only) ──────────────────────────

    if action == "call_endpoint":
        path = inp.get("endpoint_path", "")
        if not api_base:
            print(json.dumps({"error": "XANO_API_GROUP_BASE_URL is required for call_endpoint"}))
        elif not path:
            print(json.dumps({"error": "endpoint_path is required for call_endpoint"}))
        else:
            url = f"{api_base}{path}" if path.startswith("/") else f"{api_base}/{path}"
            params = _parse_json(inp.get("params", ""))
            with httpx.Client(timeout=30) as c:
                r = c.get(url, headers=_api_headers(), params=params)
            try:
                resp = r.json()
            except Exception:
                resp = r.text[:4000]
            print(json.dumps({"status": r.status_code, "body": resp}, default=str))

    # ── List tables ──────────────────────────────────────────────────

    elif action == "list_tables":
        if not instance_domain or not meta_key:
            print(json.dumps({"error": "XANO_INSTANCE_DOMAIN and XANO_METADATA_API_KEY are required"}))
        else:
            with httpx.Client(timeout=15) as c:
                r = c.get(_meta_url("/table"), headers=_meta_headers())
            try:
                resp = r.json()
            except Exception:
                resp = r.text[:4000]
            if r.status_code == 200 and isinstance(resp, list):
                tables = [{"id": t.get("id"), "name": t.get("name"), "description": t.get("description", "")} for t in resp]
                print(json.dumps({"tables": tables, "count": len(tables)}))
            else:
                print(json.dumps({"status": r.status_code, "body": resp}, default=str))

    # ── Get table schema ─────────────────────────────────────────────

    elif action == "get_table_schema":
        table_name = inp.get("table_name", "")
        if not instance_domain or not meta_key:
            print(json.dumps({"error": "XANO_INSTANCE_DOMAIN and XANO_METADATA_API_KEY are required"}))
        elif not table_name:
            print(json.dumps({"error": "table_name is required for get_table_schema"}))
        else:
            with httpx.Client(timeout=15) as c:
                tid = _resolve_table_id(table_name, c)
                if not tid:
                    print(json.dumps({"error": f"Table '{table_name}' not found"}))
                else:
                    r = c.get(_meta_url(f"/table/{tid}/schema"), headers=_meta_headers())
                    try:
                        schema = r.json()
                    except Exception:
                        schema = r.text[:4000]
                    print(json.dumps({"table": table_name, "table_id": tid, "schema": schema}, default=str))

    # ── Query table records ──────────────────────────────────────────

    elif action == "query_table":
        table_name = inp.get("table_name", "")
        if not instance_domain or not meta_key:
            print(json.dumps({"error": "XANO_INSTANCE_DOMAIN and XANO_METADATA_API_KEY are required"}))
        elif not table_name:
            print(json.dumps({"error": "table_name is required for query_table"}))
        else:
            with httpx.Client(timeout=30) as c:
                tid = _resolve_table_id(table_name, c)
                if not tid:
                    print(json.dumps({"error": f"Table '{table_name}' not found"}))
                else:
                    params = {
                        "page": inp.get("page", 1),
                        "per_page": min(inp.get("per_page", 50), 100),
                    }
                    search = inp.get("search", "")
                    if search:
                        params["search"] = search
                    sort_col = inp.get("sort_column", "")
                    if sort_col:
                        params["sort_column"] = sort_col
                        params["sort_direction"] = inp.get("sort_direction", "asc")
                    r = c.get(_meta_url(f"/table/{tid}/content"), headers=_meta_headers(), params=params)
                    try:
                        data = r.json()
                    except Exception:
                        data = r.text[:4000]
                    if r.status_code == 200 and isinstance(data, dict):
                        print(json.dumps({
                            "rows": data.get("items", []),
                            "count": len(data.get("items", [])),
                            "curItems": data.get("curItems", 0),
                            "totalItems": data.get("itemsTotal", 0),
                            "page": data.get("curPage", 1),
                            "totalPages": data.get("pageTotal", 1),
                        }, default=str))
                    else:
                        print(json.dumps({"status": r.status_code, "body": data}, default=str))

    else:
        print(json.dumps({"error": f"Unknown action: {action}. Use one of: call_endpoint, list_tables, get_table_schema, query_table"}))

except Exception as e:
    print(json.dumps({"error": str(e)}))
