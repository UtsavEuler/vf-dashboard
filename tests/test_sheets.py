"""Sheets adapter tests: bounded retry + stable error mapping.

These exercise the pure retry/classification logic without gspread or a real
Sheet by passing plain callables that raise the relevant exceptions.
"""

import pytest

from vf_app import sheets
from vf_app.config import Config
from vf_app.errors import UpstreamError, UpstreamTimeoutError


def make_config():
    return Config("sid", {"type": "service_account"}, 1048576, 10)


# ── call_google: success + retry ─────────────────────────────────────────────
def test_call_google_returns_result():
    assert sheets.call_google(lambda: 42, sleep=lambda s: None) == 42


def test_retry_on_429_then_success():
    slept = []
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise Exception("APIError 429: rate limit exceeded")
        return "ok"

    result = sheets.call_google(fn, retries=5, base_delay=0.01,
                                max_total_delay=5.0, sleep=slept.append)
    assert result == "ok"
    assert calls["n"] == 3
    assert len(slept) == 2  # retried twice


def test_429_exhausted_maps_to_502():
    def fn():
        raise Exception("429 rate limited")

    with pytest.raises(UpstreamError):
        sheets.call_google(fn, retries=3, base_delay=0.01,
                           max_total_delay=5.0, sleep=lambda s: None)


def test_retry_total_delay_is_bounded():
    slept = []

    def fn():
        raise Exception("429 always")

    with pytest.raises(UpstreamError):
        sheets.call_google(fn, retries=50, base_delay=1.0,
                           max_total_delay=2.0, sleep=slept.append)
    assert sum(slept) <= 2.0 + 1e-9


# ── error classification ─────────────────────────────────────────────────────
def test_timeout_maps_to_504():
    def fn():
        raise TimeoutError("request timed out")

    with pytest.raises(UpstreamTimeoutError):
        sheets.call_google(fn, retries=2, base_delay=0.01,
                           max_total_delay=1.0, sleep=lambda s: None)


def test_generic_google_error_maps_to_502():
    def fn():
        raise Exception("some spreadsheet failure")

    with pytest.raises(UpstreamError):
        sheets.call_google(fn, sleep=lambda s: None)


def test_non_transient_error_does_not_retry():
    slept = []

    def fn():
        raise Exception("permission denied")

    with pytest.raises(UpstreamError):
        sheets.call_google(fn, retries=5, base_delay=0.01,
                           max_total_delay=5.0, sleep=slept.append)
    assert slept == []  # no retries for a non-transient error


# ── contract constants are intact ────────────────────────────────────────────
def test_worksheet_and_match_key_contract():
    assert sheets.WORKSHEETS == {
        "fi_master": "FI_Master",
        "dealer_master": "Dealer_Master",
        "added_dealers": "Added_Dealers",
        "onboarding": "FI_Onboarding",
        "fi_policy": "FI_Policy",
        "fi_policy_geo": "FI_Policy_Geo",
        "dealer_health": "Dealer_Health",
        "snapshots": "Monthly_Snapshots",
        "dpirr_months": "DPIRR_Months",
        "dpirr_entries": "DPIRR_Entries",
        "dpirr_products": "DPIRR_Products",
        "dpirr_models": "DPIRR_Models",
        "dpirr_variants": "DPIRR_Variants",
    }
    assert sheets.MATCH_KEYS["fi_master"] == ["name"]
    assert sheets.MATCH_KEYS["dealer_master"] == ["dealerName", "location"]
    assert sheets.MATCH_KEYS["added_dealers"] == ["dealer", "location"]
    assert sheets.MATCH_KEYS["onboarding"] == ["dealer", "location", "financier"]
    assert sheets.MATCH_KEYS["fi_policy"] == ["financier", "productKey"]
    assert sheets.MATCH_KEYS["dealer_health"] == ["dealer", "location"]
    assert sheets.MATCH_KEYS["fi_policy_geo"] == [
        "financier", "productKey", "seg", "state", "city"]
    assert sheets.MATCH_KEYS["dpirr_months"] == ["id"]
    assert sheets.MATCH_KEYS["dpirr_entries"] == ["id"]
    assert sheets.MATCH_KEYS["dpirr_products"] == ["name"]
    assert sheets.MATCH_KEYS["dpirr_models"] == ["product", "name"]
    assert sheets.MATCH_KEYS["dpirr_variants"] == ["product", "model", "variant"]


def test_dpirr_header_contract():
    """The DP/IRR column order is on-sheet layout: it must not drift."""
    assert sheets.DPIRR_MONTH_HEADERS == ["id", "label"]
    assert sheets.DPIRR_PRODUCT_HEADERS == ["name"]
    assert sheets.DPIRR_MODEL_HEADERS == ["product", "name"]
    assert sheets.DPIRR_VARIANT_HEADERS == ["product", "model", "variant", "esp"]
    assert sheets.DPIRR_ENTRY_HEADERS == [
        "id", "monthId", "monthLabel", "srNo", "customerName", "cibil",
        "creditRemarks", "product", "model", "variant", "dealerName", "state",
        "city", "salesRm", "vfRm", "financier", "cocoDodo", "vfStatus",
        "remarks", "fundingType", "irr", "esp", "orp", "ltv", "downPayment",
        "discount", "effectiveDp",
    ]
    assert set(sheets.AUTO_CREATE_HEADERS) == {
        "dpirr_months", "dpirr_entries", "dpirr_products", "dpirr_models",
        "dpirr_variants"}


def test_bootstrap_aliases_exclude_dpirr():
    # Reading 13 sheets in one request risks the Vercel function timeout.
    assert sheets.BOOTSTRAP_ALIASES == [
        "fi_master", "dealer_master", "added_dealers", "onboarding",
        "fi_policy", "fi_policy_geo", "dealer_health", "snapshots"]
    assert all(not a.startswith("dpirr_") for a in sheets.BOOTSTRAP_ALIASES)


# ── plan_variant_bulk: the pure bulk-upload planner ──────────────────────────
def test_plan_variant_bulk_skips_blank_rows():
    rows = [{"model": "", "variant": "V", "esp": "1"},
            {"model": "M", "variant": "", "esp": "1"},
            {"model": "M", "variant": "V", "esp": ""},
            {"model": "  ", "variant": "  ", "esp": "  "}]
    new_models, variants, stats = sheets.plan_variant_bulk("P", rows, [], [])
    assert stats == {"modelsAdded": 0, "variantsAdded": 0,
                     "variantsUpdated": 0, "skipped": 4}
    assert new_models == [] and variants == []


def test_plan_variant_bulk_creates_model_and_variant():
    new_models, variants, stats = sheets.plan_variant_bulk(
        "P", [{"model": "M1", "variant": "V1", "esp": "100"}], [], [])
    assert new_models == [{"product": "P", "name": "M1"}]
    assert variants == [{"product": "P", "model": "M1", "variant": "V1",
                         "esp": "100"}]
    assert stats == {"modelsAdded": 1, "variantsAdded": 1,
                     "variantsUpdated": 0, "skipped": 0}


def test_plan_variant_bulk_updates_esp_in_place():
    existing_variants = [{"product": "P", "model": "M1", "variant": "V1",
                          "esp": "100"}]
    new_models, variants, stats = sheets.plan_variant_bulk(
        "P", [{"model": "M1", "variant": "V1", "esp": "250"}],
        [{"product": "P", "name": "M1"}], existing_variants)
    assert new_models == []
    assert variants == [{"product": "P", "model": "M1", "variant": "V1",
                         "esp": "250"}]
    assert stats["variantsUpdated"] == 1 and stats["variantsAdded"] == 0
    # The caller's input is never mutated.
    assert existing_variants[0]["esp"] == "100"


def test_plan_variant_bulk_matching_is_case_insensitive():
    _, variants, stats = sheets.plan_variant_bulk(
        "P", [{"model": "m1", "variant": "v1", "esp": "9"}],
        [{"product": "P", "name": "M1"}],
        [{"product": "P", "model": "M1", "variant": "V1", "esp": "1"}])
    assert stats == {"modelsAdded": 0, "variantsAdded": 0,
                     "variantsUpdated": 1, "skipped": 0}
    # Updated in place: the original casing of model/variant survives.
    assert variants == [{"product": "P", "model": "M1", "variant": "V1",
                         "esp": "9"}]


def test_plan_variant_bulk_preserves_other_products():
    other = {"product": "OTHER", "model": "M1", "variant": "V1", "esp": "5"}
    _, variants, stats = sheets.plan_variant_bulk(
        "P", [{"model": "M1", "variant": "V1", "esp": "7"}],
        [{"product": "OTHER", "name": "M1"}], [other])
    # OTHER's row is carried through untouched; P gets its own new rows.
    assert variants[0] == other
    assert {"product": "P", "model": "M1", "variant": "V1", "esp": "7"} in variants
    assert stats == {"modelsAdded": 1, "variantsAdded": 1,
                     "variantsUpdated": 0, "skipped": 0}


# ── GoogleSheetsAdapter against a stub spreadsheet ───────────────────────────
# These pin the adapter's own behaviour: which worksheets it is allowed to
# create, and — critically — that a cascade's Sheets call count does NOT grow
# with the number of matching rows. gspread is never imported: the adapter only
# ever touches the handle cached in _sh.
class StubWorksheet:
    """Records every call so tests can count writes and inspect payloads."""

    _next_id = 100

    def __init__(self, title, rows):
        self.title = title
        self.rows = [list(r) for r in rows]
        self.ops = []
        StubWorksheet._next_id += 1
        self.id = StubWorksheet._next_id

    # -- reads --
    def row_values(self, n):
        return list(self.rows[n - 1]) if len(self.rows) >= n else []

    def get_all_values(self):
        return [list(r) for r in self.rows]

    def get_all_records(self, expected_headers=None, default_blank=""):
        headers = self.rows[0] if self.rows else []
        out = []
        for row in self.rows[1:]:
            padded = list(row) + [default_blank] * (len(headers) - len(row))
            out.append(dict(zip(headers, padded)))
        return out

    # -- writes (every one records exactly one op = one HTTP call) --
    def _apply(self, range_name, values):
        first = int(range_name.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")) - 1
        for offset, new_row in enumerate(values):
            i = first + offset
            while len(self.rows) <= i:
                self.rows.append([])
            self.rows[i] = list(new_row)

    def update(self, range_name=None, values=None):
        self.ops.append(("update", range_name, values))
        self._apply(range_name, values)

    def batch_update(self, data, **kwargs):
        self.ops.append(("batch_update", data))
        for block in data:
            self._apply(block["range"], block["values"])

    def append_row(self, values):
        self.ops.append(("append_row", values))
        self.rows.append(list(values))

    def append_rows(self, values):
        self.ops.append(("append_rows", values))
        self.rows.extend(list(r) for r in values)

    def delete_rows(self, i):
        self.ops.append(("delete_rows", i))
        del self.rows[i - 1]

    def clear(self):
        self.ops.append(("clear",))
        self.rows = []

    def write_calls(self):
        """Only writes are recorded, so this is the sheet's HTTP write count."""
        return list(self.ops)


class StubSpreadsheet:
    def __init__(self, present):
        self.wss = {t: StubWorksheet(t, r) for t, r in present.items()}
        self.created = []
        self.batch_requests = []

    def worksheet(self, title):
        if title not in self.wss:
            raise Exception(f"WorksheetNotFound: {title}")
        return self.wss[title]

    def add_worksheet(self, title=None, rows=None, cols=None):
        self.created.append({"title": title, "rows": rows, "cols": cols})
        ws = StubWorksheet(title, [])
        self.wss[title] = ws
        return ws

    def batch_update(self, body):
        self.batch_requests.append(body)
        # Apply deleteDimension requests in order, as Google does.
        for req in body["requests"]:
            rng = req["deleteDimension"]["range"]
            ws = next(w for w in self.wss.values() if w.id == rng["sheetId"])
            del ws.rows[rng["startIndex"]:rng["endIndex"]]


def stub_adapter(present):
    adapter = sheets.GoogleSheetsAdapter(make_config())
    adapter._sh = StubSpreadsheet(present)
    return adapter


# -- which worksheets may be auto-created ------------------------------------
@pytest.mark.parametrize("alias", [a for a in sheets.READ_ALIASES
                                   if a not in sheets.AUTO_CREATE_HEADERS])
def test_original_aliases_are_never_auto_created(alias):
    """A missing original worksheet is a real fault, not something to conjure up
    with a guessed header row. It must raise and create nothing."""
    adapter = stub_adapter({})
    with pytest.raises(UpstreamError):
        adapter.read(alias)
    assert adapter._sh.created == []


@pytest.mark.parametrize("alias", sorted(sheets.AUTO_CREATE_HEADERS))
def test_dpirr_aliases_auto_create_with_frozen_headers(alias):
    adapter = stub_adapter({})
    assert adapter.read(alias) == []
    title = sheets.WORKSHEETS[alias]
    headers = sheets.AUTO_CREATE_HEADERS[alias]
    assert [c["title"] for c in adapter._sh.created] == [title]
    created = adapter._sh.created[0]
    assert created["rows"] == 500
    assert created["cols"] == max(10, len(headers) + 2)
    assert adapter._sh.wss[title].rows == [list(headers)]


def test_existing_dpirr_worksheet_is_not_recreated():
    adapter = stub_adapter({"DPIRR_Products": [["name"], ["P"]]})
    assert adapter.read("dpirr_products") == [{"name": "P"}]
    assert adapter._sh.created == []


# -- bulk_update --------------------------------------------------------------
def test_bulk_update_preserves_untouched_columns():
    adapter = stub_adapter({"DPIRR_Variants": [
        ["product", "model", "variant", "esp"],
        ["OLD", "M1", "V1", "500"],
        ["KEEP", "M2", "V2", "9"],
        ["OLD", "M1", "V2", "700"],
    ]})
    assert adapter.bulk_update("dpirr_variants", {"product": "OLD"},
                               {"product": "NEW"}) == 2
    assert adapter._sh.wss["DPIRR_Variants"].rows == [
        ["product", "model", "variant", "esp"],
        ["NEW", "M1", "V1", "500"],
        ["KEEP", "M2", "V2", "9"],
        ["NEW", "M1", "V2", "700"],
    ]


def test_bulk_update_writes_exactly_len_headers_columns():
    # The sheet is provisioned wider than its headers and rows are ragged.
    adapter = stub_adapter({"DPIRR_Models": [
        ["product", "name"], ["OLD"], ["OLD", "M2", "stray", "extra"]]})
    assert adapter.bulk_update("dpirr_models", {"product": "OLD"},
                               {"product": "NEW"}) == 2
    ws = adapter._sh.wss["DPIRR_Models"]
    batches = [o[1] for o in ws.ops if o[0] == "batch_update"]
    assert len(batches) == 1
    for block in batches[0]:
        for row in block["values"]:
            assert len(row) == 2
    assert ws.rows[1] == ["NEW", ""]


def test_bulk_update_call_count_is_bounded():
    """100 matching rows must not mean 100 writes — this is the Vercel-timeout
    regression guard, not a style preference."""
    rows = [["product", "model", "variant", "esp"]]
    rows += [["OLD", "M1", f"V{i}", str(i)] for i in range(100)]
    adapter = stub_adapter({"DPIRR_Variants": rows})
    assert adapter.bulk_update("dpirr_variants", {"product": "OLD"},
                               {"product": "NEW"}) == 100
    writes = adapter._sh.wss["DPIRR_Variants"].write_calls()
    assert len(writes) == 1, f"expected 1 batched write, got {len(writes)}"
    assert all(r[0] == "NEW" for r in adapter._sh.wss["DPIRR_Variants"].rows[1:])


def test_bulk_update_no_match_writes_nothing():
    adapter = stub_adapter({"DPIRR_Models": [["product", "name"], ["P", "M1"]]})
    assert adapter.bulk_update("dpirr_models", {"product": "NOPE"},
                               {"product": "X"}) == 0
    assert adapter._sh.wss["DPIRR_Models"].write_calls() == []


# -- bulk_delete --------------------------------------------------------------
def test_bulk_delete_removes_only_matching_rows():
    adapter = stub_adapter({"DPIRR_Models": [
        ["product", "name"], ["P", "M1"], ["K", "M3"], ["P", "M2"]]})
    assert adapter.bulk_delete("dpirr_models", {"product": "P"}) == 2
    assert adapter._sh.wss["DPIRR_Models"].rows == [["product", "name"],
                                                   ["K", "M3"]]


def test_bulk_delete_call_count_is_bounded():
    """The cascade that motivated this: deleting 120 variants must be ONE
    spreadsheet request, not 120 sequential delete_rows calls."""
    rows = [["product", "model", "variant", "esp"]]
    rows += [["GO", "M1", f"V{i}", "1"] for i in range(60)]
    rows += [["KEEP", "M2", "V", "1"]]
    rows += [["GO", "M1", f"W{i}", "1"] for i in range(60)]
    adapter = stub_adapter({"DPIRR_Variants": rows})
    assert adapter.bulk_delete("dpirr_variants", {"product": "GO"}) == 120
    assert len(adapter._sh.batch_requests) == 1
    assert adapter._sh.wss["DPIRR_Variants"].write_calls() == []
    assert adapter._sh.wss["DPIRR_Variants"].rows == [
        ["product", "model", "variant", "esp"], ["KEEP", "M2", "V", "1"]]


def test_bulk_delete_no_match_issues_no_request():
    adapter = stub_adapter({"DPIRR_Models": [["product", "name"], ["P", "M1"]]})
    assert adapter.bulk_delete("dpirr_models", {"product": "NOPE"}) == 0
    assert adapter._sh.batch_requests == []


# -- append_rows / replace_all ------------------------------------------------
def test_append_rows_is_one_call_and_uses_frozen_headers():
    adapter = stub_adapter({"DPIRR_Entries": [list(sheets.DPIRR_ENTRY_HEADERS)]})
    entries = [{"id": f"e{i}", "customerName": "C"} for i in range(50)]
    assert adapter.append_rows("dpirr_entries", entries) == 50
    ops = adapter._sh.wss["DPIRR_Entries"].write_calls()
    assert len(ops) == 1 and ops[0][0] == "append_rows"
    assert all(len(r) == len(sheets.DPIRR_ENTRY_HEADERS) for r in ops[0][1])


def test_replace_all_writes_header_row_then_all_rows():
    adapter = stub_adapter({"DPIRR_Variants": [
        ["product", "model", "variant", "esp"], ["OTHER", "M9", "V9", "1"]]})
    rows = [{"product": "OTHER", "model": "M9", "variant": "V9", "esp": "1"},
            {"product": "P", "model": "M1", "variant": "V1", "esp": "100"}]
    assert adapter.replace_all("dpirr_variants", rows) == 2
    ops = [o[0] for o in adapter._sh.wss["DPIRR_Variants"].ops]
    assert ops == ["clear", "update"]
    assert adapter._sh.wss["DPIRR_Variants"].rows == [
        ["product", "model", "variant", "esp"],
        ["OTHER", "M9", "V9", "1"],
        ["P", "M1", "V1", "100"]]


# -- run-coalescing helper ----------------------------------------------------
def test_contiguous_runs():
    assert sheets._contiguous_runs([]) == []
    assert sheets._contiguous_runs([2]) == [(2, 2)]
    assert sheets._contiguous_runs([2, 3, 4, 7, 9, 10]) == [
        (2, 4), (7, 7), (9, 10)]
