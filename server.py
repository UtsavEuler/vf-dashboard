#!/usr/bin/env python3
"""
Euler Motors VF Dashboard — Server with Google Sheets Backend
Local:   python server.py
Railway: auto-started via Procfile
"""

import json, os, threading, traceback, secrets, time, random
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ── CONFIG ────────────────────────────────────────────────────
SHEET_ID   = "1jWmwJJZJzLX0oCSeRm24bCNNQ29pn0jAOl9Y9pUlU-4"
CREDS_FILE = "credentials.json"
PORT       = int(os.environ.get("PORT", 9000))  # Railway sets PORT env var

# ── AUTH ── (set LOGIN_ID and LOGIN_PASS in Railway env variables)
LOGIN_ID      = os.environ.get("LOGIN_ID",   "admin")
LOGIN_PASS    = os.environ.get("LOGIN_PASS", "euler@1234$")
SESSION_TTL   = int(os.environ.get("SESSION_TTL", 3600))  # seconds, default 60 min

# ── SESSION STORE ─────────────────────────────────────────────
_sessions     = {}   # token -> expiry timestamp
_session_lock = threading.Lock()

def create_session():
    token = secrets.token_hex(32)
    with _session_lock:
        # Clean expired sessions
        now = time.time()
        expired = [t for t, exp in _sessions.items() if exp < now]
        for t in expired: del _sessions[t]
        _sessions[token] = now + SESSION_TTL
    return token

def validate_session(token):
    if SESSION_TTL == 0: return True  # dev mode: disable auth
    if not token: return False
    with _session_lock:
        exp = _sessions.get(token)
        if not exp or exp < time.time():
            if token in _sessions: del _sessions[token]
            return False
        _sessions[token] = time.time() + SESSION_TTL  # refresh on activity
        return True

def invalidate_session(token):
    with _session_lock:
        _sessions.pop(token, None)

# ── GOOGLE SHEETS CLIENT ──────────────────────────────────────
_sh   = None
_lock = threading.Lock()

def get_sheet():
    global _sh
    with _lock:
        if _sh is None:
            import gspread
            from google.oauth2.service_account import Credentials
            SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

            # Support credentials from environment variable (Railway) or file (local)
            creds_json = os.environ.get("GOOGLE_CREDENTIALS")
            if creds_json:
                creds_dict = json.loads(creds_json)
                # Railway sometimes double-escapes newlines in private key — fix it
                if 'private_key' in creds_dict:
                    creds_dict['private_key'] = creds_dict['private_key'].replace('\\n', '\n')
                creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
                print("  Using credentials from environment variable")
            else:
                creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPES)
                print("  Using credentials from credentials.json")

            gc  = gspread.authorize(creds)
            _sh = gc.open_by_key(SHEET_ID)
            print(f"  ✅ Connected to Google Sheet: {_sh.title}")
    return _sh

def with_retry(fn, retries=5):
    """Call fn(), retrying on 429 rate limit errors with exponential backoff."""
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            if '429' in str(e) and attempt < retries - 1:
                wait = (2 ** attempt) + random.uniform(0, 1)
                print(f"  ⚠ Rate limited — retrying in {wait:.1f}s (attempt {attempt+1}/{retries})")
                time.sleep(wait)
            else:
                raise

def ws(title):
    return with_retry(lambda: get_sheet().worksheet(title))

def ws_or_create(title, headers):
    """Like ws(), but auto-creates the worksheet with the given headers if it doesn't exist yet."""
    def _do():
        sh = get_sheet()
        try:
            return sh.worksheet(title)
        except Exception:
            new_ws = sh.add_worksheet(title=title, rows=500, cols=max(10, len(headers) + 2))
            new_ws.append_row(headers)
            return new_ws
    return with_retry(_do)

def bulk_update_rows(worksheet, match_keys, data_dict):
    """Update ALL rows matching match_keys, applying only the fields present in data_dict. Returns count updated."""
    def _do():
        headers = worksheet.row_values(1)
        all_vals = worksheet.get_all_values()
        count = 0
        for i, row in enumerate(all_vals[1:], start=2):
            if all((row[headers.index(k)] if k in headers and headers.index(k) < len(row) else "") == str(v)
                   for k, v in match_keys.items()):
                # Always write exactly len(headers) columns — never depend on the
                # real sheet's provisioned column count (which is often wider than headers).
                new_row = [(row[h_idx] if h_idx < len(row) else "") for h_idx in range(len(headers))]
                for h_idx, h in enumerate(headers):
                    if h in data_dict:
                        new_row[h_idx] = str(data_dict[h])
                worksheet.update(range_name=f"A{i}", values=[new_row])
                count += 1
        return count
    return with_retry(_do)

def bulk_delete_rows(worksheet, match_keys):
    """Delete ALL rows matching match_keys. Returns count deleted."""
    def _do():
        headers = worksheet.row_values(1)
        all_vals = worksheet.get_all_values()
        to_delete = []
        for i, row in enumerate(all_vals[1:], start=2):
            if all((row[headers.index(k)] if k in headers and headers.index(k) < len(row) else "") == str(v)
                   for k, v in match_keys.items()):
                to_delete.append(i)
        for i in reversed(to_delete):
            worksheet.delete_rows(i)
        return len(to_delete)
    return with_retry(_do)

def rows_to_dicts(worksheet):
    try:
        raw_headers = with_retry(lambda: worksheet.row_values(1))
        # Check for duplicates — if found, use manual parse instead
        if len(raw_headers) != len(set(raw_headers)):
            raise Exception(f"duplicate headers detected: {[h for h in raw_headers if raw_headers.count(h) > 1]}")
        return with_retry(lambda: worksheet.get_all_records(expected_headers=raw_headers, default_blank="")) or []
    except Exception as e:
        print(f"  ⚠ rows_to_dicts error ({worksheet.title}): {e} — falling back to manual parse")
        try:
            all_vals = with_retry(lambda: worksheet.get_all_values())
            if not all_vals: return []
            headers = all_vals[0]
            result = []
            seen_headers = {}
            deduped = []
            for h in headers:
                if h in seen_headers:
                    deduped.append(h + '_dup_' + str(seen_headers[h]))
                    seen_headers[h] += 1
                else:
                    seen_headers[h] = 1
                    deduped.append(h)
            for row in all_vals[1:]:
                if not any(row): continue
                padded = row + [''] * (len(deduped) - len(row))
                d = {}
                for i, h in enumerate(deduped):
                    if '_dup_' not in h:
                        d[h] = padded[i]
                result.append(d)
            return result
        except Exception as e2:
            print(f"  ❌ rows_to_dicts fallback error ({worksheet.title}): {e2}")
            return []

def upsert_row(worksheet, match_keys, data_dict):
    def _do():
        headers = worksheet.row_values(1)
        all_vals = worksheet.get_all_values()
        row_idx = None
        for i, row in enumerate(all_vals[1:], start=2):
            if all((row[headers.index(k)] if k in headers and headers.index(k) < len(row) else "") == str(v)
                   for k, v in match_keys.items()):
                row_idx = i
                break
        row_data = [str(data_dict.get(h, "")) for h in headers]
        if row_idx:
            worksheet.update(range_name=f"A{row_idx}", values=[row_data])
        else:
            worksheet.append_row(row_data)
    with_retry(_do)

def delete_row(worksheet, match_keys):
    def _do():
        headers = worksheet.row_values(1)
        all_vals = worksheet.get_all_values()
        for i, row in enumerate(all_vals[1:], start=2):
            if all((row[headers.index(k)] if k in headers and headers.index(k) < len(row) else "") == str(v)
                   for k, v in match_keys.items()):
                worksheet.delete_rows(i)
                return True
        return False
    return with_retry(_do)

# ── API FUNCTIONS ─────────────────────────────────────────────
def api_get(sheet_name):        return rows_to_dicts(ws(sheet_name))
def api_append_snapshot(snap_dict):
    sheet = ws("Monthly_Snapshots")
    headers = ["snapshot_date","snapshot_month","snapshot_year","fi_total","fi_active","fi_onboarded","fi_suspended","fi_p1","fi_p2","dealer_total","dealer_coco","dealer_dodo","health_overall_star","health_overall_green","health_overall_amber","health_overall_red","health_3wc_star","health_3wc_green","health_3wc_amber","health_3wc_red","health_3wp_star","health_3wp_green","health_3wp_amber","health_3wp_red","health_4wcs_star","health_4wcs_green","health_4wcs_amber","health_4wcs_red","health_4wct_star","health_4wct_green","health_4wct_amber","health_4wct_red","fi_mou_signed","fi_mou_wip","fi_mou_na","ph_3wc_star","ph_3wc_green","ph_3wc_amber","ph_3wc_red","ph_3wp_star","ph_3wp_green","ph_3wp_amber","ph_3wp_red","ph_4wcs_star","ph_4wcs_green","ph_4wcs_amber","ph_4wcs_red","ph_4wct_star","ph_4wct_green","ph_4wct_amber","ph_4wct_red","fi_dealer_links_3wc","fi_dealer_links_3wp","fi_dealer_links_4wcs","fi_dealer_links_4wct","poc_data","zone_data","state_data","dealer_data"]
    existing = sheet.row_values(1)
    if not existing:
        sheet.append_row(headers)
    sheet.append_row([snap_dict.get(h, "") for h in headers])
def api_save_fi_master(d):      upsert_row(ws("FI_Master"),      {"name": d["name"]}, d)
def api_delete_fi_master(n):    delete_row(ws("FI_Master"),      {"name": n})
def api_save_dealer_master(d):  upsert_row(ws("Dealer_Master"),  {"dealerName": d["dealerName"], "location": d["location"]}, d)
def api_delete_dealer_master(n, l): delete_row(ws("Dealer_Master"), {"dealerName": n, "location": l})
def api_save_added_dealer(d):   upsert_row(ws("Added_Dealers"),  {"dealer": d["dealer"], "location": d["location"]}, d)
def api_delete_added_dealer(d, l): delete_row(ws("Added_Dealers"), {"dealer": d, "location": l})
def api_save_onboarding(d):     upsert_row(ws("FI_Onboarding"),  {"dealer": d["dealer"], "location": d["location"], "financier": d["financier"]}, d)
def api_delete_onboarding(d, l, f): delete_row(ws("FI_Onboarding"), {"dealer": d, "location": l, "financier": f})
def api_save_fi_policy(d):      upsert_row(ws("FI_Policy"),      {"financier": d["financier"], "productKey": d["productKey"]}, d)
def api_save_dealer_health(d):  upsert_row(ws("Dealer_Health"),  {"dealer": d["dealer"], "location": d["location"]}, d)
def api_get_fi_policy_geo():     return rows_to_dicts(ws("FI_Policy_Geo")) or []
def api_save_fi_policy_geo(d):  upsert_row(ws("FI_Policy_Geo"), {"financier": d["financier"], "productKey": d["productKey"], "seg": d["seg"], "state": d["state"], "city": d["city"]}, d)
def api_delete_fi_policy_geo(fi, pk, seg, state, city): delete_row(ws("FI_Policy_Geo"), {"financier": fi, "productKey": pk, "seg": seg, "state": state, "city": city})

# ── DP/IRR TRACKER ──────────────────────────────────────────
DPIRR_MONTH_HEADERS   = ["id", "label"]
DPIRR_ENTRY_HEADERS   = ["id","monthId","monthLabel","srNo","customerName","cibil","creditRemarks",
                         "product","model","variant","dealerName","state","city","salesRm","vfRm",
                         "financier","cocoDodo","vfStatus","remarks","fundingType","irr","esp","orp",
                         "ltv","downPayment","discount","effectiveDp"]
DPIRR_PRODUCT_HEADERS = ["name"]
DPIRR_MODEL_HEADERS   = ["product", "name"]
DPIRR_VARIANT_HEADERS = ["product", "model", "variant", "esp"]

def api_get_dpirr_months():    return rows_to_dicts(ws_or_create("DPIRR_Months",   DPIRR_MONTH_HEADERS)) or []
def api_get_dpirr_entries():   return rows_to_dicts(ws_or_create("DPIRR_Entries",  DPIRR_ENTRY_HEADERS)) or []
def api_get_dpirr_products():  return rows_to_dicts(ws_or_create("DPIRR_Products", DPIRR_PRODUCT_HEADERS)) or []
def api_get_dpirr_models():    return rows_to_dicts(ws_or_create("DPIRR_Models",   DPIRR_MODEL_HEADERS)) or []
def api_get_dpirr_variants():  return rows_to_dicts(ws_or_create("DPIRR_Variants", DPIRR_VARIANT_HEADERS)) or []

def api_save_dpirr_month(d):
    upsert_row(ws_or_create("DPIRR_Months", DPIRR_MONTH_HEADERS), {"id": d["id"]}, d)

def api_save_dpirr_entry(d):
    upsert_row(ws_or_create("DPIRR_Entries", DPIRR_ENTRY_HEADERS), {"id": d["id"]}, d)

def api_delete_dpirr_entry(entry_id):
    delete_row(ws_or_create("DPIRR_Entries", DPIRR_ENTRY_HEADERS), {"id": entry_id})

def api_save_dpirr_product(d):
    """Add a new product (idempotent upsert by name)."""
    upsert_row(ws_or_create("DPIRR_Products", DPIRR_PRODUCT_HEADERS), {"name": d["name"]}, d)

def api_rename_dpirr_product(old_name, new_name):
    """Rename a product and cascade the rename to its models and variants."""
    if not old_name or not new_name:
        raise ValueError(f"dpirr_products_rename requires non-empty oldName and newName (got oldName={old_name!r}, newName={new_name!r})")
    upsert_row(ws_or_create("DPIRR_Products", DPIRR_PRODUCT_HEADERS), {"name": old_name}, {"name": new_name})
    bulk_update_rows(ws_or_create("DPIRR_Models",   DPIRR_MODEL_HEADERS),   {"product": old_name}, {"product": new_name})
    bulk_update_rows(ws_or_create("DPIRR_Variants", DPIRR_VARIANT_HEADERS), {"product": old_name}, {"product": new_name})

def api_delete_dpirr_product(name):
    """Delete a product and cascade-delete all its models and variants."""
    delete_row(ws_or_create("DPIRR_Products", DPIRR_PRODUCT_HEADERS), {"name": name})
    bulk_delete_rows(ws_or_create("DPIRR_Models",   DPIRR_MODEL_HEADERS),   {"product": name})
    bulk_delete_rows(ws_or_create("DPIRR_Variants", DPIRR_VARIANT_HEADERS), {"product": name})

def api_save_dpirr_model(d):
    """Add a new model under a product (idempotent upsert by product+name)."""
    upsert_row(ws_or_create("DPIRR_Models", DPIRR_MODEL_HEADERS), {"product": d["product"], "name": d["name"]}, d)

def api_rename_dpirr_model(product, old_name, new_name):
    """Rename a model and cascade the rename to its variants."""
    if not product or not old_name or not new_name:
        raise ValueError(f"dpirr_models_rename requires non-empty product, oldName, newName (got product={product!r}, oldName={old_name!r}, newName={new_name!r})")
    upsert_row(ws_or_create("DPIRR_Models", DPIRR_MODEL_HEADERS), {"product": product, "name": old_name}, {"product": product, "name": new_name})
    bulk_update_rows(ws_or_create("DPIRR_Variants", DPIRR_VARIANT_HEADERS), {"product": product, "model": old_name}, {"model": new_name})

def api_delete_dpirr_model(product, name):
    """Delete a model and cascade-delete all its variants."""
    delete_row(ws_or_create("DPIRR_Models", DPIRR_MODEL_HEADERS), {"product": product, "name": name})
    bulk_delete_rows(ws_or_create("DPIRR_Variants", DPIRR_VARIANT_HEADERS), {"product": product, "model": name})

def api_save_dpirr_variant(d):
    """Add/update a variant. If oldVariant is provided and differs from variant, this renames in place."""
    old_variant = d.get("oldVariant") or d["variant"]
    match = {"product": d["product"], "model": d["model"], "variant": old_variant}
    data  = {"product": d["product"], "model": d["model"], "variant": d["variant"], "esp": d["esp"]}
    upsert_row(ws_or_create("DPIRR_Variants", DPIRR_VARIANT_HEADERS), match, data)

def api_delete_dpirr_variant(product, model, variant):
    delete_row(ws_or_create("DPIRR_Variants", DPIRR_VARIANT_HEADERS), {"product": product, "model": model, "variant": variant})

def api_bulk_save_dpirr_variants(product, rows):
    """
    Bulk upsert models+variants for a product in a FIXED small number of Sheets API calls,
    regardless of how many rows are in the uploaded file. Used by the Excel bulk-upload flow
    to avoid Google Sheets write-rate-limit errors from firing one API call per row.

    rows: list of {"model": str, "variant": str, "esp": str}
    Returns {"modelsAdded": n, "variantsAdded": n, "variantsUpdated": n, "skipped": n}
    """
    models_ws   = ws_or_create("DPIRR_Models", DPIRR_MODEL_HEADERS)
    variants_ws = ws_or_create("DPIRR_Variants", DPIRR_VARIANT_HEADERS)

    def _do():
        existing_models   = rows_to_dicts(models_ws)
        existing_variants = rows_to_dicts(variants_ws)

        model_names_lower = {m["name"].lower() for m in existing_models if m.get("product") == product}

        variant_lookup = {}
        all_variants_out = list(existing_variants)  # mutable working copy, preserves other products' rows
        for v in all_variants_out:
            if v.get("product") == product:
                variant_lookup[(v.get("model","").lower(), v.get("variant","").lower())] = v

        modelsAdded = variantsAdded = variantsUpdated = skipped = 0
        new_model_rows = []
        seen_new_models = set()

        for row in rows:
            model_name   = str(row.get("model", "") or "").strip()
            variant_name = str(row.get("variant", "") or "").strip()
            esp          = str(row.get("esp", "") or "").strip()
            if not model_name or not variant_name or esp == "":
                skipped += 1
                continue

            model_key = model_name.lower()
            if model_key not in model_names_lower and model_key not in seen_new_models:
                new_model_rows.append([product, model_name])
                seen_new_models.add(model_key)
                modelsAdded += 1

            vkey = (model_key, variant_name.lower())
            existing = variant_lookup.get(vkey)
            if existing:
                if existing.get("esp") != esp:
                    existing["esp"] = esp
                variantsUpdated += 1
            else:
                new_rec = {"product": product, "model": model_name, "variant": variant_name, "esp": esp}
                all_variants_out.append(new_rec)
                variant_lookup[vkey] = new_rec
                variantsAdded += 1

        # ── Write back in as few API calls as possible ──
        if new_model_rows:
            models_ws.append_rows(new_model_rows)

        variant_grid = [DPIRR_VARIANT_HEADERS] + [
            [str(v.get(h, "")) for h in DPIRR_VARIANT_HEADERS] for v in all_variants_out
        ]
        variants_ws.clear()
        variants_ws.update(range_name="A1", values=variant_grid)

        return {"modelsAdded": modelsAdded, "variantsAdded": variantsAdded,
                "variantsUpdated": variantsUpdated, "skipped": skipped}

    return with_retry(_do)


# ── HTTP HANDLER ──────────────────────────────────────────────
class Handler(SimpleHTTPRequestHandler):

    def log_message(self, fmt, *args):
        if args and ('.well-known' in str(args[0]) or 'favicon' in str(args[0])):
            return
        print(f"  {self.address_string()} → {fmt % args}")

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type",   "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Session-Token")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Session-Token")
        self.end_headers()

    def read_body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n)) if n else {}

    def do_GET(self):
        path = urlparse(self.path).path
        # Suppress favicon silently
        if path == '/favicon.ico':
            self.send_response(204)
            self.end_headers()
            return
        # ── API routes ──────────────────────────────────────
        if path.startswith("/api/"):
            try:
                # Public read-only endpoints (no auth required) for Loan Eligibility standalone
                PUBLIC_GET = {"/api/fi_master", "/api/dealer_master", "/api/added_dealers",
                              "/api/onboarding", "/api/fi_policy", "/api/fi_policy_geo",
                              "/api/dealer_health", "/api/ping", "/api/login", "/api/snapshots",
                              "/api/dpirr_months", "/api/dpirr_entries", "/api/dpirr_products",
                              "/api/dpirr_models", "/api/dpirr_variants"}
                if path not in PUBLIC_GET:
                    token = self.headers.get("X-Session-Token","")
                    if not validate_session(token):
                        self.send_json(401, {"error": "Unauthorized"}); return
                if   path == "/api/ping":
                    self.send_json(200, {"ok": True, "login_id_set": bool(os.environ.get("LOGIN_ID")), "sessions_active": len(_sessions)})
                elif path == "/api/fi_master":      self.send_json(200, api_get("FI_Master"))
                elif path == "/api/dealer_master":  self.send_json(200, api_get("Dealer_Master"))
                elif path == "/api/added_dealers":  self.send_json(200, api_get("Added_Dealers"))
                elif path == "/api/onboarding":     self.send_json(200, api_get("FI_Onboarding"))
                elif path == "/api/fi_policy":      self.send_json(200, api_get("FI_Policy"))
                elif path == "/api/dealer_health":  self.send_json(200, api_get("Dealer_Health"))
                elif path == "/api/fi_policy_geo":   self.send_json(200, api_get_fi_policy_geo())
                elif path == "/api/dpirr_months":    self.send_json(200, api_get_dpirr_months())
                elif path == "/api/dpirr_entries":   self.send_json(200, api_get_dpirr_entries())
                elif path == "/api/dpirr_products":  self.send_json(200, api_get_dpirr_products())
                elif path == "/api/dpirr_models":    self.send_json(200, api_get_dpirr_models())
                elif path == "/api/dpirr_variants":  self.send_json(200, api_get_dpirr_variants())
                elif path == "/api/snapshots":
                    try:
                        self.send_json(200, api_get("Monthly_Snapshots") or [])
                    except Exception:
                        self.send_json(200, [])
                else:                               self.send_json(404, {"error": f"Unknown: {path}"})
            except Exception as e:
                traceback.print_exc()
                self.send_json(500, {"error": str(e)})
        # ── Static files ─────────────────────────────────────
        else:
            super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            self.send_json(404, {"error": "Not found"}); return
        try:
            body = self.read_body()
            if path not in ("/api/login",):
                token = self.headers.get("X-Session-Token","")
                if not validate_session(token):
                    self.send_json(401, {"error": "Unauthorized"}); return
            if   path == "/api/login":
                ok = (body.get("id","") == LOGIN_ID and body.get("pass","") == LOGIN_PASS)
                if ok:
                    token = create_session()
                    self.send_json(200, {"ok": True, "token": token})
                else:
                    self.send_json(200, {"ok": False})
                return
            elif path == "/api/logout":
                invalidate_session(self.headers.get("X-Session-Token",""))
                self.send_json(200, {"ok": True}); return
            elif path == "/api/fi_master":     api_save_fi_master(body)
            elif path == "/api/dealer_master": api_save_dealer_master(body)
            elif path == "/api/added_dealers": api_save_added_dealer(body)
            elif path == "/api/onboarding":    api_save_onboarding(body)
            elif path == "/api/fi_policy":     api_save_fi_policy(body)
            elif path == "/api/dealer_health": api_save_dealer_health(body)
            elif path == "/api/fi_policy_geo":   api_save_fi_policy_geo(body)
            elif path == "/api/dpirr_months":         api_save_dpirr_month(body)
            elif path == "/api/dpirr_entries":        api_save_dpirr_entry(body)
            elif path == "/api/dpirr_products":       api_save_dpirr_product(body)
            elif path == "/api/dpirr_products_rename": api_rename_dpirr_product(body.get("oldName",""), body.get("newName",""))
            elif path == "/api/dpirr_models":         api_save_dpirr_model(body)
            elif path == "/api/dpirr_models_rename":  api_rename_dpirr_model(body.get("product",""), body.get("oldName",""), body.get("newName",""))
            elif path == "/api/dpirr_variants":       api_save_dpirr_variant(body)
            elif path == "/api/dpirr_variants_bulk":
                result = api_bulk_save_dpirr_variants(body["product"], body["rows"])
                self.send_json(200, {"ok": True, "result": result}); return
            elif path == "/api/snapshots":        api_append_snapshot(body); self.send_json(200, {"ok": True})
            else: self.send_json(404, {"error": f"Unknown: {path}"}); return
            self.send_json(200, {"ok": True})
        except Exception as e:
            traceback.print_exc()
            self.send_json(500, {"error": str(e)})

    def do_DELETE(self):
        path = urlparse(self.path).path
        qs   = parse_qs(urlparse(self.path).query)
        q    = lambda k: qs.get(k, [""])[0]
        try:
            token = self.headers.get("X-Session-Token","")
            if not validate_session(token):
                self.send_json(401, {"error": "Unauthorized"}); return
            if   path == "/api/fi_master":     api_delete_fi_master(q("name"))
            elif path == "/api/dealer_master": api_delete_dealer_master(q("dealerName"), q("location"))
            elif path == "/api/added_dealers": api_delete_added_dealer(q("dealer"), q("location"))
            elif path == "/api/onboarding":    api_delete_onboarding(q("dealer"), q("location"), q("financier"))
            elif path == "/api/fi_policy_geo":   api_delete_fi_policy_geo(q("financier"), q("productKey"), q("seg"), q("state"), q("city"))
            elif path == "/api/dpirr_entries":   api_delete_dpirr_entry(q("id"))
            elif path == "/api/dpirr_products":  api_delete_dpirr_product(q("name"))
            elif path == "/api/dpirr_models":    api_delete_dpirr_model(q("product"), q("name"))
            elif path == "/api/dpirr_variants":  api_delete_dpirr_variant(q("product"), q("model"), q("variant"))
            else: self.send_json(404, {"error": f"Unknown: {path}"}); return
            self.send_json(200, {"ok": True})
        except Exception as e:
            traceback.print_exc()
            self.send_json(500, {"error": str(e)})

# ── MAIN ──────────────────────────────────────────────────────
if __name__ == "__main__":
    import socket

    # Test connection on startup
    print("\n  Testing Google Sheets connection...")
    try:
        get_sheet()
    except Exception as e:
        print(f"\n  ❌ Google Sheets connection FAILED: {e}")
        print("  Check GOOGLE_CREDENTIALS environment variable.")

    print(f"\n  ✅ Server starting on port {PORT}")
    print(f"  Open: http://localhost:{PORT}/euler_vf.html\n")

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")
