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
}

# Aliases that support each verb (mirrors server.py exactly).
READ_ALIASES = list(WORKSHEETS.keys())
UPSERT_ALIASES = ["fi_master", "dealer_master", "added_dealers", "onboarding",
                  "fi_policy", "dealer_health", "fi_policy_geo"]
DELETE_ALIASES = ["fi_master", "dealer_master", "added_dealers", "onboarding",
                  "fi_policy_geo"]

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
                sleep=time.sleep):
    """Run a Google Sheets call with a BOUNDED retry policy.

    Retries only transient failures (HTTP 429 rate limits and timeouts) with
    exponential backoff, and caps the *total* time spent sleeping so a retry
    storm can never blow the serverless request budget. On final failure a
    timeout maps to UpstreamTimeoutError (504) and anything else to
    UpstreamError (502). Raw exceptions never escape.
    """
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

    # -- client -------------------------------------------------------------
    def _spreadsheet(self):
        if self._sh is None:
            import gspread
            from google.oauth2.service_account import Credentials

            scopes = ["https://www.googleapis.com/auth/spreadsheets"]
            creds = Credentials.from_service_account_info(
                self._config.credentials, scopes=scopes)
            gc = gspread.authorize(creds)
            self._sh = call_google(lambda: gc.open_by_key(self._config.sheet_id))
        return self._sh

    def _ws(self, title):
        return call_google(lambda: self._spreadsheet().worksheet(title))

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
        all_vals = call_google(lambda: worksheet.get_all_values())
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

    def read(self, alias):
        return self._rows_to_dicts(self._ws(WORKSHEETS[alias]))

    # -- writes -------------------------------------------------------------
    def upsert(self, alias, match_keys, data_dict):
        worksheet = self._ws(WORKSHEETS[alias])

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
        worksheet = self._ws(WORKSHEETS[alias])

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
        worksheet = self._ws(WORKSHEETS["snapshots"])

        def _do():
            existing = worksheet.row_values(1)
            if not existing:
                worksheet.append_row(SNAPSHOT_HEADERS)
            worksheet.append_row([snap_dict.get(h, "") for h in SNAPSHOT_HEADERS])

        call_google(_do)


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
