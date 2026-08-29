"""Google Sheets adapter.

Owns Google authentication, worksheet lookup, reads, upserts, deletes, the
bounded retry policy and error translation. The gspread client is created lazily
and cached only as a warm-instance optimization; correctness never depends on
warm state.

The single source of truth for the frozen contract lives here:
- WORKSHEETS maps an API alias to its worksheet title.
- MATCH_KEYS maps an API alias to its composite match keys (for upsert/delete).
- SNAPSHOT_HEADERS is the fixed column order appended to Monthly_Snapshots.
"""

import hashlib
import logging
import random
import time

from .config import get_config
from .errors import UpstreamError, UpstreamTimeoutError

log = logging.getLogger("vf_app")

# ── Frozen contract ──────────────────────────────────────────────────────────
WORKSHEETS = {
    "fi_master": "FI_Master",
    "dealer_master": "Dealer_Master",
    "added_dealers": "Added_Dealers",
    "onboarding": "FI_Onboarding",
    "fi_policy": "FI_Policy",
    "fi_policy_geo": "FI_Policy_Geo",
    "dealer_health": "Dealer_Health",
    "snapshots": "Monthly_Snapshots",
    # DP/IRR tracker. These worksheets are created on first use (see
    # AUTO_CREATE_HEADERS) because they were introduced after the workbook.
    "dpirr_months": "DPIRR_Months",
    "dpirr_entries": "DPIRR_Entries",
    "dpirr_products": "DPIRR_Products",
    "dpirr_models": "DPIRR_Models",
    "dpirr_variants": "DPIRR_Variants",
    # Optional per-state ESP overrides (e.g. a state government subsidy that
    # changes the effective price). Deliberately its own table, not extra
    # columns — adding a new state later is just a new row, never a schema
    # change, no matter how many states eventually offer one.
    "dpirr_variant_state_esp": "DPIRR_VariantStateEsp",
    # PIN-protected identity for DP/IRR entry attribution. Deliberately absent
    # from READ_ALIASES/UPSERT_ALIASES/DELETE_ALIASES below — pinHash must never
    # be exposed via the generic /api/<alias> routes, so routes.py owns explicit
    # handlers (GET/register/verify/change_pin) that filter it out.
    "dpirr_users": "DPIRR_Users",
}

# DP/IRR column orders. Order matters: it is the on-sheet column order and the
# order rows are written in, so these lists are part of the frozen contract.
DPIRR_MONTH_HEADERS = ["id", "label"]
DPIRR_ENTRY_HEADERS = [
    "id", "monthId", "monthLabel", "srNo", "createdBy", "customerName", "cibil",
    "creditRemarks", "product", "model", "variant", "dealerName", "state",
    "city", "salesRm", "vfRm", "financier", "isSelfFinance", "cocoDodo",
    "vfStatus", "remarks", "fundingType", "loanTenure", "irr", "esp", "orp",
    "ltv", "downPayment", "discount", "effectiveDp",
]
DPIRR_PRODUCT_HEADERS = ["name"]
DPIRR_MODEL_HEADERS = ["product", "name"]
DPIRR_VARIANT_HEADERS = ["product", "model", "variant", "esp"]
DPIRR_VARIANT_STATE_ESP_HEADERS = ["product", "model", "variant", "state", "esp"]
DPIRR_USER_HEADERS = ["name", "pinHash", "isAdmin", "createdAt"]


def hash_dpirr_pin(name, pin):
    """Salt the PIN with the (lowercased) name so identical PINs across
    different users never produce identical hashes."""
    raw = (str(name).strip().lower() + ":" + str(pin).strip()).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


# Aliases whose worksheet is auto-created (with this header row) when missing.
# The original 8 worksheets are guaranteed to exist; a missing one is a real
# fault and must keep raising rather than being silently conjured up.
AUTO_CREATE_HEADERS = {
    "dpirr_months": DPIRR_MONTH_HEADERS,
    "dpirr_entries": DPIRR_ENTRY_HEADERS,
    "dpirr_products": DPIRR_PRODUCT_HEADERS,
    "dpirr_models": DPIRR_MODEL_HEADERS,
    "dpirr_variants": DPIRR_VARIANT_HEADERS,
    "dpirr_variant_state_esp": DPIRR_VARIANT_STATE_ESP_HEADERS,
    "dpirr_users": DPIRR_USER_HEADERS,
}

# Composite match keys per resource, exactly as the original server.py used them.
MATCH_KEYS = {
    "fi_master": ["name"],
    "dealer_master": ["dealerName", "location"],
    "added_dealers": ["dealer", "location"],
    "onboarding": ["dealer", "location", "financier"],
    "fi_policy": ["financier", "productKey"],
    "dealer_health": ["dealer", "location"],
    "fi_policy_geo": ["financier", "productKey", "seg", "state", "city"],
    "dpirr_months": ["id"],
    "dpirr_entries": ["id"],
    "dpirr_products": ["name"],
    "dpirr_models": ["product", "name"],
    "dpirr_variants": ["product", "model", "variant"],
    "dpirr_variant_state_esp": ["product", "model", "variant", "state"],
    "dpirr_users": ["name"],
    "snapshots": ["snapshot_date"],
}

# Aliases that support each verb (mirrors server.py exactly).
READ_ALIASES = [a for a in WORKSHEETS.keys() if a != "dpirr_users"]
# dpirr_users is deliberately absent from READ_ALIASES: routes.py owns an
# explicit GET handler that strips pinHash before returning rows.
# dpirr_variants is deliberately absent: its POST needs oldVariant matching so it
# can rename in place, so routes.py owns an explicit handler for it.
UPSERT_ALIASES = ["fi_master", "dealer_master", "added_dealers", "onboarding",
                  "fi_policy", "dealer_health", "fi_policy_geo",
                  "dpirr_months", "dpirr_entries", "dpirr_products",
                  "dpirr_models", "dpirr_variant_state_esp"]
# dpirr_products / dpirr_models / dpirr_months are deliberately absent: their
# DELETEs cascade into child rows, so routes.py owns explicit handlers for them.
# dpirr_variants is deliberately absent too now: deleting a variant must also
# clean up any state-ESP overrides tied to it, so routes.py owns that handler.
# dpirr_users deletion deliberately does NOT cascade to dpirr_entries — a
# deleted user's past entries are left exactly as they are (still attributed
# to that name), so no historical record is ever silently destroyed.
DELETE_ALIASES = ["fi_master", "dealer_master", "added_dealers", "onboarding",
                  "fi_policy_geo", "dpirr_entries", "snapshots", "dpirr_users",
                  "dpirr_variant_state_esp"]

# /api/bootstrap reads every listed sheet in ONE request. With 13 worksheets that
# is 13+ sequential Google round-trips, which risks blowing the Vercel function
# timeout, so bootstrap stays pinned to the original 8. The DP/IRR section loads
# through its own per-alias reads.
BOOTSTRAP_ALIASES = ["fi_master", "dealer_master", "added_dealers",
                     "onboarding", "fi_policy", "fi_policy_geo",
                     "dealer_health", "snapshots"]

# Every worksheet the dashboard's initial load needs, served by /api/bootstrap_all
# through read_many() in a FIXED 2 Google calls. Unrelated to BOOTSTRAP_ALIASES
# above, which is the older per-sheet /api/bootstrap set. dpirr_users is included
# here but the route strips pinHash before it leaves the process - see routes.py.
INITIAL_LOAD_ALIASES = READ_ALIASES + ["dpirr_users"]

SNAPSHOT_HEADERS = [
    "snapshot_date", "snapshot_month", "snapshot_year", "fi_total", "fi_active",
    "fi_onboarded", "fi_suspended", "fi_p1", "fi_p2", "dealer_total",
    "dealer_coco", "dealer_dodo", "health_overall_star", "health_overall_green",
    "health_overall_amber", "health_overall_red", "health_3wc_star",
    "health_3wc_green", "health_3wc_amber", "health_3wc_red", "health_3wp_star",
    "health_3wp_green", "health_3wp_amber", "health_3wp_red", "health_4wcs_star",
    "health_4wcs_green", "health_4wcs_amber", "health_4wcs_red",
    "health_4wct_star", "health_4wct_green", "health_4wct_amber",
    "health_4wct_red", "fi_mou_signed", "fi_mou_wip", "fi_mou_na", "ph_3wc_star",
    "ph_3wc_green", "ph_3wc_amber", "ph_3wc_red", "ph_3wp_star", "ph_3wp_green",
    "ph_3wp_amber", "ph_3wp_red", "ph_4wcs_star", "ph_4wcs_green",
    "ph_4wcs_amber", "ph_4wcs_red", "ph_4wct_star", "ph_4wct_green",
    "ph_4wct_amber", "ph_4wct_red", "fi_dealer_links_3wc", "fi_dealer_links_3wp",
    "fi_dealer_links_4wcs", "fi_dealer_links_4wct", "poc_data", "zone_data",
    "state_data", "dealer_data",
]


# ── Bounded retry + error classification ─────────────────────────────────────
def _is_rate_limited(exc):
    if exc is None:
        return False
    code = getattr(getattr(exc, "response", None), "status_code", None)
    if code == 429:
        return True
    return "429" in str(exc)


def _is_timeout(exc):
    if exc is None:
        return False
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return True
    return "timed out" in str(exc).lower() or "timeout" in str(exc).lower()


def call_google(fn, *, retries=4, base_delay=0.5, max_total_delay=8.0,
                sleep=None):
    """Run a Google Sheets call with a BOUNDED retry policy.

    Retries only transient failures (HTTP 429 rate limits and timeouts) with
    exponential backoff, and caps the *total* time spent sleeping so a retry
    storm can never blow the serverless request budget. On final failure a
    timeout maps to UpstreamTimeoutError (504) and anything else to
    UpstreamError (502). Raw exceptions never escape.
    """
    # Resolved here, not as a default argument: a default binds time.sleep at
    # import, which no test can then substitute.
    sleep = time.sleep if sleep is None else sleep
    slept = 0.0
    last = None
    for attempt in range(retries):
        try:
            return fn()
        except (UpstreamError, UpstreamTimeoutError):
            raise
        except Exception as exc:  # noqa: BLE001 - deliberately broad; reclassified
            last = exc
            transient = _is_rate_limited(exc) or _is_timeout(exc)
            has_more = attempt < retries - 1
            if not (transient and has_more):
                break
            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            remaining = max_total_delay - slept
            if remaining <= 0:
                break
            delay = min(delay, remaining)
            sleep(delay)
            slept += delay
    if _is_timeout(last):
        raise UpstreamTimeoutError() from last
    raise UpstreamError() from last


# ── Real Google Sheets adapter ───────────────────────────────────────────────
class GoogleSheetsAdapter:
    """Reads and writes the configured workbook via gspread."""

    def __init__(self, config=None):
        self._config = config or get_config()
        self._sh = None  # cached spreadsheet handle (warm-instance only)
        self._ws_cache = {}  # title -> Worksheet (warm-instance only)
        self._sheet_titles = None  # titles from ONE metadata fetch

    # -- client -------------------------------------------------------------
    def _spreadsheet(self):
        if self._sh is None:
            import gspread
            from google.oauth2.service_account import Credentials

            scopes = ["https://www.googleapis.com/auth/spreadsheets"]
            creds = Credentials.from_service_account_info(
                self._config.credentials, scopes=scopes)
            gc = gspread.authorize(creds)
            # Bound every Google call. Without a timeout the only limit is
            # Vercel killing the function, which surfaces as an opaque platform
            # 502 with no log line instead of a clean, logged 504.
            gc.set_timeout(self._config.google_timeout_seconds)
            # open_by_key issues no HTTP request in gspread 6 - the handle is
            # lazy - so there is nothing here to retry.
            self._sh = gc.open_by_key(self._config.sheet_id)
        return self._sh

    # -- worksheet resolution ------------------------------------------------
    def _worksheet(self, title):
        """Resolve a title to a Worksheet, cached per instance.

        gspread's Spreadsheet.worksheet() re-fetches the ENTIRE spreadsheet
        metadata on every single call. Those fetches count against the same
        60-reads-per-minute-per-user Sheets quota as the data reads themselves,
        so a request that touched three worksheets spent three of its Google
        calls re-learning information that had not changed. Cache the handle.

        Warm-instance optimisation only, exactly like _sh: a cold instance still
        resolves correctly, and worksheet identity is stable for the lifetime of
        one request, which is all any single operation depends on.
        """
        if title not in self._ws_cache:
            self._ws_cache[title] = self._spreadsheet().worksheet(title)
        return self._ws_cache[title]

    def _titles(self, refresh=False):
        """The set of worksheet titles, from ONE metadata fetch, cached."""
        if self._sheet_titles is None or refresh:
            data = call_google(self._spreadsheet().fetch_sheet_metadata)
            self._sheet_titles = {
                sheet["properties"]["title"]
                for sheet in data.get("sheets", [])
                if sheet.get("properties", {}).get("title")
            }
        return self._sheet_titles

    def _ws(self, title):
        return call_google(lambda: self._worksheet(title))

    def _ws_or_create(self, title, headers):
        """Like _ws, but creates the worksheet with a header row if absent.

        ONLY a genuine WorksheetNotFound may trigger creation. The previous bare
        `except Exception` also swallowed rate limits, so a 429 on the lookup was
        misread as "sheet missing" and tried to add_worksheet() a title that
        already existed - which Google rejects with a 400. That laundered a
        retryable 429 into a non-retryable error, so call_google() skipped its
        backoff and returned 502 immediately instead of waiting out the limit.
        """
        from gspread.exceptions import WorksheetNotFound

        def _do():
            try:
                return self._worksheet(title)
            except WorksheetNotFound:
                created = self._spreadsheet().add_worksheet(
                    title=title, rows=500, cols=max(10, len(headers) + 2))
                created.append_row(headers)
                self._ws_cache[title] = created
                if self._sheet_titles is not None:
                    self._sheet_titles.add(title)
                return created

        return call_google(_do)

    def _ws_for(self, alias):
        """Resolve an alias to a worksheet, auto-creating the DP/IRR sheets."""
        headers = AUTO_CREATE_HEADERS.get(alias)
        if headers is not None:
            return self._ws_or_create(WORKSHEETS[alias], headers)
        return self._ws(WORKSHEETS[alias])

    def _headers_for(self, alias, worksheet):
        return AUTO_CREATE_HEADERS.get(alias) or worksheet.row_values(1)

    # -- reads --------------------------------------------------------------
    def _rows_to_dicts(self, worksheet):
        """Read all rows as dicts. Preserves the original duplicate-header
        handling and manual-parse fallback from server.py."""
        try:
            raw_headers = call_google(lambda: worksheet.row_values(1))
            if len(raw_headers) != len(set(raw_headers)):
                raise ValueError("duplicate headers detected")
            return call_google(
                lambda: worksheet.get_all_records(
                    expected_headers=raw_headers, default_blank="")
            ) or []
        except (UpstreamError, UpstreamTimeoutError):
            raise
        except Exception:  # noqa: BLE001 - fall back to manual parse
            return self._manual_parse(worksheet)

    def _manual_parse(self, worksheet):
        return _dedupe_parse(call_google(worksheet.get_all_values))

    def read(self, alias):
        return self._rows_to_dicts(self._ws_for(alias))

    def read_many(self, aliases):
        """Read several worksheets in a FIXED number of Google calls.

        One metadata fetch (usually already cached) plus ONE values:batchGet,
        however many aliases are asked for - against 3 calls per alias when the
        caller loops over read(). The dashboard's initial load touches 15
        worksheets, so this is the difference between ~45 Google requests and 2,
        under a 60-reads-per-minute-per-user quota.

        Returns {alias: rows}. An auto-creatable worksheet that does not exist
        yet comes back as [] rather than failing the whole batch - the same rows
        a freshly created empty sheet would yield. Creating it is left to the
        first write, so a read never provokes a write.
        """
        aliases = list(aliases)
        if not aliases:
            return {}

        present = self._titles()
        wanted = [a for a in aliases if WORKSHEETS[a] in present]
        if len(wanted) != len(aliases):
            # Something looks absent. Confirm against fresh metadata before
            # acting on it: this instance's cache may simply predate another
            # instance creating the sheet, and treating a populated worksheet as
            # missing would render real rows as a clean empty section.
            present = self._titles(refresh=True)
            wanted = [a for a in aliases if WORKSHEETS[a] in present]

        missing = [a for a in aliases if a not in wanted]
        for alias in missing:
            if alias not in AUTO_CREATE_HEADERS:
                # One of the original 8 is genuinely absent: a real fault, which
                # must keep failing rather than silently reading as empty.
                raise UpstreamError()

        out = {alias: [] for alias in missing}
        if not wanted:
            return out

        sh = self._spreadsheet()
        ranges = ["'" + WORKSHEETS[a].replace("'", "''") + "'" for a in wanted]
        payload = call_google(lambda: sh.values_batch_get(ranges))
        value_ranges = payload.get("valueRanges", [])
        if len(value_ranges) != len(wanted):
            # The response must line up positionally with the request; if it
            # does not, rows cannot be safely attributed to worksheets.
            raise UpstreamError()
        for alias, value_range in zip(wanted, value_ranges):
            out[alias] = _values_to_records(value_range.get("values") or [])
        return out

    # -- writes -------------------------------------------------------------
    def upsert(self, alias, match_keys, data_dict):
        worksheet = self._ws_for(alias)

        def _do():
            headers = worksheet.row_values(1)
            all_vals = worksheet.get_all_values()
            row_idx = None
            for i, row in enumerate(all_vals[1:], start=2):
                if all(
                    (row[headers.index(k)]
                     if k in headers and headers.index(k) < len(row) else "")
                    == str(v)
                    for k, v in match_keys.items()
                ):
                    row_idx = i
                    break
            row_data = [str(data_dict.get(h, "")) for h in headers]
            if row_idx:
                worksheet.update(range_name=f"A{row_idx}", values=[row_data])
            else:
                worksheet.append_row(row_data)

        call_google(_do)

    def delete(self, alias, match_keys):
        worksheet = self._ws_for(alias)

        def _do():
            headers = worksheet.row_values(1)
            all_vals = worksheet.get_all_values()
            for i, row in enumerate(all_vals[1:], start=2):
                if all(
                    (row[headers.index(k)]
                     if k in headers and headers.index(k) < len(row) else "")
                    == str(v)
                    for k, v in match_keys.items()
                ):
                    worksheet.delete_rows(i)
                    return True
            return False

        return call_google(_do)

    def append_snapshot(self, snap_dict):
        worksheet = self._ws_for("snapshots")

        def _do():
            existing = worksheet.row_values(1)
            if not existing:
                worksheet.append_row(SNAPSHOT_HEADERS)
            worksheet.append_row([snap_dict.get(h, "") for h in SNAPSHOT_HEADERS])

        call_google(_do)

    # -- bulk primitives (DP/IRR) -------------------------------------------
    # Both of these MUST issue a fixed, small number of Sheets calls regardless
    # of how many rows match. The original server.py wrote once per matching row,
    # which was survivable on a long-lived server but is a data-loss bug on
    # Vercel: a cascade over ~130 rows is ~130 sequential writes, so the function
    # is killed (or Google 429s) part-way through and the cascade is left half
    # applied — orphaned child rows the dashboard then renders as invisible.
    def bulk_update(self, alias, match_keys, data_dict):
        """Update EVERY row matching match_keys, applying only the fields present
        in data_dict. Returns the number of rows updated.

        Two reads plus ONE batched write, whatever the match count.
        """
        worksheet = self._ws_for(alias)

        def _do():
            headers = worksheet.row_values(1)
            all_vals = worksheet.get_all_values()
            updated = {}
            for i, row in enumerate(all_vals[1:], start=2):
                if not _row_matches(row, headers, match_keys):
                    continue
                # Always write exactly len(headers) columns — never depend on the
                # sheet's provisioned column count, which is often wider.
                new_row = [(row[j] if j < len(row) else "")
                           for j in range(len(headers))]
                for j, header in enumerate(headers):
                    if header in data_dict:
                        new_row[j] = str(data_dict[header])
                updated[i] = new_row
            if updated:
                # Coalesce contiguous rows so a cascade (which usually matches a
                # block) becomes one range; batch_update sends the lot in a
                # single HTTP request either way.
                data = [{"range": f"A{start}",
                         "values": [updated[r] for r in range(start, end + 1)]}
                        for start, end in _contiguous_runs(sorted(updated))]
                worksheet.batch_update(data)
            return len(updated)

        return call_google(_do)

    def bulk_delete(self, alias, match_keys):
        """Delete EVERY row matching match_keys. Returns the number deleted.

        Two reads plus ONE batched spreadsheet request, whatever the match count.
        """
        worksheet = self._ws_for(alias)

        def _do():
            headers = worksheet.row_values(1)
            all_vals = worksheet.get_all_values()
            to_delete = [i for i, row in enumerate(all_vals[1:], start=2)
                         if _row_matches(row, headers, match_keys)]
            if to_delete:
                # Descending order: Google applies the requests in sequence, so
                # deleting from the bottom keeps every later index valid.
                requests = [
                    {"deleteDimension": {"range": {
                        "sheetId": worksheet.id,
                        "dimension": "ROWS",
                        # Sheets row indexes are 0-based and end-exclusive.
                        "startIndex": start - 1,
                        "endIndex": end,
                    }}}
                    for start, end in reversed(_contiguous_runs(to_delete))
                ]
                self._spreadsheet().batch_update({"requests": requests})
            return len(to_delete)

        return call_google(_do)

    def append_rows(self, alias, rows):
        """Append many rows in a SINGLE Sheets call. Returns the count appended."""
        worksheet = self._ws_for(alias)

        def _do():
            headers = self._headers_for(alias, worksheet)
            grid = [[str(r.get(h, "")) for h in headers] for r in rows]
            if grid:
                worksheet.append_rows(grid)
            return len(grid)

        return call_google(_do)

    def replace_all(self, alias, rows):
        """Replace the whole worksheet with a header row plus rows, in two calls.
        Used by the bulk upload so row count never drives API call count."""
        worksheet = self._ws_for(alias)

        def _do():
            headers = self._headers_for(alias, worksheet)
            grid = [list(headers)] + [[str(r.get(h, "")) for h in headers]
                                      for r in rows]
            worksheet.clear()
            worksheet.update(range_name="A1", values=grid)
            return len(rows)

        return call_google(_do)


def _values_to_records(all_vals):
    """Turn a raw value grid into row dicts.

    Mirrors gspread's get_all_records (short rows padded, cells numericised) so
    a batched read returns exactly what the per-worksheet path returns, and
    falls back to the duplicate-header-tolerant parse when headers repeat.
    """
    from gspread.utils import numericise_all, to_records

    if not all_vals:
        return []
    headers = all_vals[0]
    if not headers or len(headers) != len(set(headers)):
        return _dedupe_parse(all_vals)
    # Pad to the WIDEST row, not to the header row: get_all_records reads through
    # get(pad_values=True), so a data row extending past the last header keeps
    # its trailing cells under blank keys rather than losing them.
    width = max(len(row) for row in all_vals)
    padded_headers = headers + [""] * (width - len(headers))
    rows = [
        numericise_all(row + [""] * (width - len(row)),
                       empty2zero=False, default_blank="")
        for row in all_vals[1:]
    ]
    return to_records(padded_headers, rows)


def _dedupe_parse(all_vals):
    """Duplicate-header-tolerant parse: duplicate columns dropped, blank rows
    skipped, every value left as a string."""
    if not all_vals:
        return []
    headers = all_vals[0]
    seen = {}
    deduped = []
    for h in headers:
        if h in seen:
            deduped.append(h + "_dup_" + str(seen[h]))
            seen[h] += 1
        else:
            seen[h] = 1
            deduped.append(h)
    result = []
    for row in all_vals[1:]:
        if not any(row):
            continue
        padded = row + [""] * (len(deduped) - len(row))
        d = {}
        for i, h in enumerate(deduped):
            if "_dup_" not in h:
                d[h] = padded[i]
        result.append(d)
    return result


def _contiguous_runs(rows):
    """Collapse a sorted list of row numbers into (start, end) inclusive runs."""
    runs = []
    for row in rows:
        if runs and row == runs[-1][1] + 1:
            runs[-1][1] = row
        else:
            runs.append([row, row])
    return [(start, end) for start, end in runs]


def _row_matches(row, headers, match_keys):
    """True when every match key's cell in `row` equals the wanted value."""
    return all(
        (row[headers.index(k)]
         if k in headers and headers.index(k) < len(row) else "") == str(v)
        for k, v in match_keys.items()
    )


# ── DP/IRR bulk-upload planning (pure) ───────────────────────────────────────
def plan_variant_bulk(product, rows, existing_models, existing_variants):
    """Work out what a variants bulk upload should write — no Sheets access.

    Matching on model and variant is case-insensitive, mirroring the original
    server.py. Rows missing a model, a variant or an ESP are skipped. Variants
    belonging to other products are carried through untouched, because the caller
    rewrites the whole worksheet in one call.

    Returns (new_model_rows, all_variants_out, stats) where new_model_rows is a
    list of {"product","name"} dicts and stats counts what happened.
    """
    model_names_lower = {str(m.get("name", "")).lower()
                         for m in existing_models
                         if m.get("product") == product}

    variant_lookup = {}
    all_variants_out = [dict(v) for v in existing_variants]
    for v in all_variants_out:
        if v.get("product") == product:
            key = (str(v.get("model", "")).lower(),
                   str(v.get("variant", "")).lower())
            variant_lookup[key] = v

    models_added = variants_added = variants_updated = skipped = 0
    new_model_rows = []
    seen_new_models = set()

    for row in rows:
        model_name = str(row.get("model", "") or "").strip()
        variant_name = str(row.get("variant", "") or "").strip()
        esp = str(row.get("esp", "") or "").strip()
        if not model_name or not variant_name or esp == "":
            skipped += 1
            continue

        model_key = model_name.lower()
        if model_key not in model_names_lower and model_key not in seen_new_models:
            new_model_rows.append({"product": product, "name": model_name})
            seen_new_models.add(model_key)
            models_added += 1

        vkey = (model_key, variant_name.lower())
        existing = variant_lookup.get(vkey)
        if existing:
            if existing.get("esp") != esp:
                existing["esp"] = esp
            variants_updated += 1
        else:
            new_rec = {"product": product, "model": model_name,
                       "variant": variant_name, "esp": esp}
            all_variants_out.append(new_rec)
            variant_lookup[vkey] = new_rec
            variants_added += 1

    stats = {"modelsAdded": models_added, "variantsAdded": variants_added,
             "variantsUpdated": variants_updated, "skipped": skipped}
    return new_model_rows, all_variants_out, stats


# ── Adapter accessor (dependency-injection seam for tests) ───────────────────
_adapter = None


def get_adapter(config=None):
    """Return a process-cached real adapter. Tests monkeypatch this symbol (or
    pass an adapter into create_app) to inject a fake."""
    global _adapter
    if _adapter is None:
        _adapter = GoogleSheetsAdapter(config)
    return _adapter


def reset_adapter():
    global _adapter
    _adapter = None
