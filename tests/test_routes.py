"""Route contract tests against the fake Sheets adapter."""

import logging
from urllib.parse import urlencode

import pytest

from vf_app import sheets

PROTECTED_PATHS = ["/", "/eligibility", "/api/bootstrap", "/api/fi_master"]


def test_public_paths_are_available(client):
    for path in PROTECTED_PATHS:
        assert client.get(path).status_code != 403


def test_health_hides_secrets(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "fake-sheet-id" not in body
    assert resp.get_json().get("status") == "ok"


def test_frame_ancestors_limited_to_jarvis_origins(client):
    resp = client.get("/")
    csp = resp.headers["Content-Security-Policy"]
    assert "frame-ancestors https://jarvis.eulerlogistics.com" in csp
    assert "https://staging-jarvis.eulerlogistics.com" in csp


# ── Pages ────────────────────────────────────────────────────────────────────
def test_index_serves_dashboard(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.content_type
    assert "Euler Motors" in resp.get_data(as_text=True)


def test_eligibility_serves_page(client):
    resp = client.get("/eligibility")
    assert resp.status_code == 200
    assert "text/html" in resp.content_type


def test_unknown_path_is_404_not_repo_file(client):
    # Must never expose arbitrary repo files.
    assert client.get("/server.py").status_code == 404
    assert client.get("/credentials.json").status_code == 404
    assert client.get("/setup_sheets.py").status_code == 404


def test_unknown_api_alias_404(client):
    assert client.get("/api/nonsense").status_code == 404


# ── Reads ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("alias", sheets.READ_ALIASES)
def test_each_get_returns_list_shape(client, adapter, alias):
    adapter.data[alias] = [{"a": "1"}, {"a": "2"}]
    resp = client.get(f"/api/{alias}")
    assert resp.status_code == 200
    assert resp.get_json() == [{"a": "1"}, {"a": "2"}]


def test_bootstrap_returns_complete_contract(client, adapter):
    for alias in sheets.READ_ALIASES:
        adapter.data[alias] = [{"k": alias}]
    resp = client.get("/api/bootstrap")
    assert resp.status_code == 200
    body = resp.get_json()
    # Bootstrap is deliberately narrower than READ_ALIASES: the DP/IRR sheets are
    # read individually so one request never fans out to 13 Google round-trips.
    assert set(body.keys()) == set(sheets.BOOTSTRAP_ALIASES)
    for alias in sheets.BOOTSTRAP_ALIASES:
        assert body[alias] == [{"k": alias}]


# ── Writes: match-key validation + intended keys ─────────────────────────────
def _full_body(alias):
    return {k: f"v_{k}" for k in sheets.MATCH_KEYS[alias]}


@pytest.mark.parametrize("alias", sheets.UPSERT_ALIASES)
def test_post_uses_intended_match_keys(client, adapter, alias):
    body = _full_body(alias)
    body["extra"] = "payload"
    resp = client.post(f"/api/{alias}", json=body)
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    op, called_alias, keys = adapter.calls[-1]
    assert op == "upsert"
    assert called_alias == alias
    assert keys == {k: body[k] for k in sheets.MATCH_KEYS[alias]}


@pytest.mark.parametrize("alias", sheets.UPSERT_ALIASES)
def test_post_missing_match_key_400(client, adapter, alias):
    body = _full_body(alias)
    # Drop the first required key.
    body.pop(sheets.MATCH_KEYS[alias][0])
    resp = client.post(f"/api/{alias}", json=body)
    assert resp.status_code == 400
    # No write should have happened.
    assert all(c[0] != "upsert" for c in adapter.calls)


def test_snapshot_post_appends_once(client, adapter):
    resp = client.post("/api/snapshots", json={"snapshot_month": "Jul"})
    assert resp.status_code == 200
    appends = [c for c in adapter.calls if c[0] == "append_snapshot"]
    assert len(appends) == 1
    assert len(adapter.data["snapshots"]) == 1


def test_post_unknown_alias_404(client):
    assert client.post("/api/nonsense", json={}).status_code == 404


# ── Deletes: full composite key required ─────────────────────────────────────
@pytest.mark.parametrize("alias", sheets.DELETE_ALIASES)
def test_delete_requires_full_composite_key(client, adapter, alias):
    keys = {k: f"v_{k}" for k in sheets.MATCH_KEYS[alias]}
    resp = client.delete(f"/api/{alias}?{urlencode(keys)}")
    assert resp.status_code == 200
    op, called_alias, used = adapter.calls[-1]
    assert op == "delete" and called_alias == alias
    assert used == keys


@pytest.mark.parametrize("alias", sheets.DELETE_ALIASES)
def test_delete_missing_key_400(client, adapter, alias):
    keys = {k: f"v_{k}" for k in sheets.MATCH_KEYS[alias]}
    keys.pop(sheets.MATCH_KEYS[alias][0])
    resp = client.delete(f"/api/{alias}?{urlencode(keys)}")
    assert resp.status_code == 400
    assert all(c[0] != "delete" for c in adapter.calls)


def test_delete_unknown_alias_404(client):
    # fi_policy and dealer_health have no delete route.
    assert client.delete("/api/fi_policy?financier=x&productKey=y").status_code == 404


# ── Body limits ──────────────────────────────────────────────────────────────
def test_malformed_json_400(client):
    resp = client.post("/api/fi_master", data="{not json",
                       content_type="application/json")
    assert resp.status_code == 400


def test_oversized_json_413(app_factory):
    app = app_factory(max_request_bytes=50)
    client = app.test_client()
    big = {"name": "x" * 500}
    resp = client.post("/api/fi_master", json=big)
    assert resp.status_code == 413


# ── DP/IRR: bulk endpoints ───────────────────────────────────────────────────
def test_dpirr_entries_bulk_appends_all(client, adapter):
    entries = [{"id": "e1", "customerName": "A"}, {"id": "e2", "customerName": "B"}]
    resp = client.post("/api/dpirr_entries_bulk", json={"entries": entries})
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "result": {"added": 2}}
    assert ("append_rows", "dpirr_entries", None) in adapter.calls
    assert len(adapter.data["dpirr_entries"]) == 2


@pytest.mark.parametrize("body", [{}, {"entries": []}, {"entries": "x"},
                                  {"entries": ["not-an-object"]}])
def test_dpirr_entries_bulk_rejects_bad_payload(client, adapter, body):
    assert client.post("/api/dpirr_entries_bulk", json=body).status_code == 400
    assert all(c[0] != "append_rows" for c in adapter.calls)


def test_dpirr_variants_bulk_writes_models_and_variants(client, adapter):
    adapter.data["dpirr_variants"] = [
        {"product": "OTHER", "model": "M9", "variant": "V9", "esp": "1"}]
    resp = client.post("/api/dpirr_variants_bulk", json={
        "product": "P",
        "rows": [{"model": "M1", "variant": "V1", "esp": "100"},
                 {"model": "M1", "variant": "", "esp": "100"}],
    })
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "result": {
        "modelsAdded": 1, "variantsAdded": 1, "variantsUpdated": 0,
        "skipped": 1}}
    assert adapter.data["dpirr_models"] == [{"product": "P", "name": "M1"}]
    # The whole worksheet is rewritten, and the other product survives.
    assert ("replace_all", "dpirr_variants", None) in adapter.calls
    assert adapter.data["dpirr_variants"] == [
        {"product": "OTHER", "model": "M9", "variant": "V9", "esp": "1"},
        {"product": "P", "model": "M1", "variant": "V1", "esp": "100"}]


def test_dpirr_variants_bulk_requires_product(client, adapter):
    assert client.post("/api/dpirr_variants_bulk",
                       json={"product": "  ", "rows": []}).status_code == 400
    assert client.post("/api/dpirr_variants_bulk",
                       json={"product": "P", "rows": "nope"}).status_code == 400
    assert all(c[0] != "replace_all" for c in adapter.calls)


def test_dpirr_variants_bulk_with_no_new_models_skips_append(client, adapter):
    adapter.data["dpirr_models"] = [{"product": "P", "name": "M1"}]
    adapter.data["dpirr_variants"] = [
        {"product": "P", "model": "M1", "variant": "V1", "esp": "1"}]
    resp = client.post("/api/dpirr_variants_bulk", json={
        "product": "P", "rows": [{"model": "M1", "variant": "V1", "esp": "2"}]})
    assert resp.status_code == 200
    assert resp.get_json()["result"]["variantsUpdated"] == 1
    assert all(c[0] != "append_rows" for c in adapter.calls)
    assert adapter.data["dpirr_variants"][0]["esp"] == "2"


# ── DP/IRR: renames cascade ──────────────────────────────────────────────────
def test_dpirr_product_rename_cascades(client, adapter):
    adapter.data["dpirr_products"] = [{"name": "OLD"}]
    adapter.data["dpirr_models"] = [{"product": "OLD", "name": "M1"},
                                    {"product": "KEEP", "name": "M2"}]
    adapter.data["dpirr_variants"] = [
        {"product": "OLD", "model": "M1", "variant": "V1", "esp": "1"},
        {"product": "OLD", "model": "M1", "variant": "V2", "esp": "2"}]
    resp = client.post("/api/dpirr_products_rename",
                       json={"oldName": "OLD", "newName": "NEW"})
    assert resp.status_code == 200
    assert adapter.data["dpirr_products"] == [{"name": "NEW"}]
    assert adapter.data["dpirr_models"] == [{"product": "NEW", "name": "M1"},
                                            {"product": "KEEP", "name": "M2"}]
    # ALL matching variant rows are rewritten, proving the cascade ran (and so
    # that this static route won over the generic /api/<alias> upsert).
    assert [v["product"] for v in adapter.data["dpirr_variants"]] == ["NEW", "NEW"]
    ops = [(c[0], c[1]) for c in adapter.calls]
    assert ("bulk_update", "dpirr_models") in ops
    assert ("bulk_update", "dpirr_variants") in ops


@pytest.mark.parametrize("body", [{"newName": "N"}, {"oldName": "O"},
                                  {"oldName": " ", "newName": "N"},
                                  {"oldName": "O", "newName": ""}])
def test_dpirr_product_rename_requires_both_names(client, adapter, body):
    assert client.post("/api/dpirr_products_rename", json=body).status_code == 400
    assert adapter.calls == []


def test_dpirr_model_rename_cascades(client, adapter):
    adapter.data["dpirr_models"] = [{"product": "P", "name": "OLD"}]
    adapter.data["dpirr_variants"] = [
        {"product": "P", "model": "OLD", "variant": "V1", "esp": "1"},
        {"product": "P", "model": "OTHER", "variant": "V2", "esp": "2"}]
    resp = client.post("/api/dpirr_models_rename",
                       json={"product": "P", "oldName": "OLD", "newName": "NEW"})
    assert resp.status_code == 200
    assert adapter.data["dpirr_models"] == [{"product": "P", "name": "NEW"}]
    assert adapter.data["dpirr_variants"][0]["model"] == "NEW"
    assert adapter.data["dpirr_variants"][1]["model"] == "OTHER"
    op, alias, keys = [c for c in adapter.calls if c[0] == "bulk_update"][-1]
    assert alias == "dpirr_variants"
    assert keys == {"product": "P", "model": "OLD"}


@pytest.mark.parametrize("body", [{"oldName": "O", "newName": "N"},
                                  {"product": "P", "newName": "N"},
                                  {"product": "P", "oldName": "O"},
                                  {"product": " ", "oldName": "O",
                                   "newName": "N"}])
def test_dpirr_model_rename_requires_all_fields(client, adapter, body):
    assert client.post("/api/dpirr_models_rename", json=body).status_code == 400
    assert adapter.calls == []


# ── DP/IRR: variant save renames in place via oldVariant ─────────────────────
def test_dpirr_variant_post_renames_in_place(client, adapter):
    adapter.data["dpirr_variants"] = [
        {"product": "P", "model": "M", "variant": "V1", "esp": "1"}]
    resp = client.post("/api/dpirr_variants", json={
        "product": "P", "model": "M", "variant": "V2", "esp": "9",
        "oldVariant": "V1"})
    assert resp.status_code == 200
    op, alias, keys = adapter.calls[-1]
    assert (op, alias) == ("upsert", "dpirr_variants")
    assert keys == {"product": "P", "model": "M", "variant": "V1"}
    # Renamed in place — not duplicated.
    assert adapter.data["dpirr_variants"] == [
        {"product": "P", "model": "M", "variant": "V2", "esp": "9"}]


def test_dpirr_variant_post_without_old_variant_matches_itself(client, adapter):
    resp = client.post("/api/dpirr_variants", json={
        "product": "P", "model": "M", "variant": "V1", "esp": "5"})
    assert resp.status_code == 200
    _, _, keys = adapter.calls[-1]
    assert keys == {"product": "P", "model": "M", "variant": "V1"}
    assert adapter.data["dpirr_variants"] == [
        {"product": "P", "model": "M", "variant": "V1", "esp": "5"}]


@pytest.mark.parametrize("body", [
    {"model": "M", "variant": "V", "esp": "1"},
    {"product": "P", "variant": "V", "esp": "1"},
    {"product": "P", "model": "M", "esp": "1"},
    {"product": "P", "model": "M", "variant": "  ", "esp": "1"},
    # esp is required too: upsert rewrites the whole row, so accepting a missing
    # esp would blank an existing variant's price on a rename.
    {"product": "P", "model": "M", "variant": "V"},
    {"product": "P", "model": "M", "variant": "V", "esp": ""},
    {"product": "P", "model": "M", "variant": "V", "esp": None},
])
def test_dpirr_variant_post_requires_identity_and_esp(client, adapter, body):
    assert client.post("/api/dpirr_variants", json=body).status_code == 400
    assert adapter.calls == []


def test_dpirr_variant_post_accepts_numeric_zero_esp(client, adapter):
    """JSON 0 is a legal ESP and must not be coerced to blank."""
    resp = client.post("/api/dpirr_variants", json={
        "product": "P", "model": "M", "variant": "V", "esp": 0})
    assert resp.status_code == 200
    assert adapter.data["dpirr_variants"] == [
        {"product": "P", "model": "M", "variant": "V", "esp": "0"}]


def test_dpirr_variant_post_never_blanks_esp_on_rename(client, adapter):
    """Regression: an omitted esp used to write "" over the real price."""
    adapter.data["dpirr_variants"] = [
        {"product": "P", "model": "M", "variant": "V1", "esp": "500"}]
    resp = client.post("/api/dpirr_variants", json={
        "product": "P", "model": "M", "variant": "V2", "oldVariant": "V1"})
    assert resp.status_code == 400
    assert adapter.data["dpirr_variants"][0]["esp"] == "500"


# ── DP/IRR: cascading deletes ────────────────────────────────────────────────
def test_dpirr_product_delete_cascades(client, adapter):
    adapter.data["dpirr_products"] = [{"name": "P"}, {"name": "KEEP"}]
    adapter.data["dpirr_models"] = [{"product": "P", "name": "M1"},
                                    {"product": "KEEP", "name": "M2"}]
    adapter.data["dpirr_variants"] = [
        {"product": "P", "model": "M1", "variant": "V1", "esp": "1"},
        {"product": "P", "model": "M1", "variant": "V2", "esp": "2"},
        {"product": "KEEP", "model": "M2", "variant": "V3", "esp": "3"}]
    resp = client.delete("/api/dpirr_products?name=P")
    assert resp.status_code == 200
    assert adapter.data["dpirr_products"] == [{"name": "KEEP"}]
    assert adapter.data["dpirr_models"] == [{"product": "KEEP", "name": "M2"}]
    assert adapter.data["dpirr_variants"] == [
        {"product": "KEEP", "model": "M2", "variant": "V3", "esp": "3"}]
    ops = [(c[0], c[1], c[2]) for c in adapter.calls]
    assert ("bulk_delete", "dpirr_models", {"product": "P"}) in ops
    assert ("bulk_delete", "dpirr_variants", {"product": "P"}) in ops


def test_dpirr_product_delete_requires_name(client, adapter):
    assert client.delete("/api/dpirr_products").status_code == 400
    assert client.delete("/api/dpirr_products?name=").status_code == 400
    assert adapter.calls == []


def test_dpirr_model_delete_cascades(client, adapter):
    adapter.data["dpirr_models"] = [{"product": "P", "name": "M1"},
                                    {"product": "P", "name": "M2"}]
    adapter.data["dpirr_variants"] = [
        {"product": "P", "model": "M1", "variant": "V1", "esp": "1"},
        {"product": "P", "model": "M2", "variant": "V2", "esp": "2"}]
    resp = client.delete("/api/dpirr_models?product=P&name=M1")
    assert resp.status_code == 200
    assert adapter.data["dpirr_models"] == [{"product": "P", "name": "M2"}]
    assert adapter.data["dpirr_variants"] == [
        {"product": "P", "model": "M2", "variant": "V2", "esp": "2"}]
    assert ("bulk_delete", "dpirr_variants",
            {"product": "P", "model": "M1"}) in [tuple(c) for c in adapter.calls]


@pytest.mark.parametrize("qs", ["", "?product=P", "?name=M1", "?product=&name=M1"])
def test_dpirr_model_delete_requires_full_key(client, adapter, qs):
    assert client.delete(f"/api/dpirr_models{qs}").status_code == 400
    assert adapter.calls == []


# ── Logging redaction ────────────────────────────────────────────────────────
def test_logs_never_contain_rows(client, caplog):
    caplog.set_level(logging.INFO, logger="vf_app")
    marker = "SUPER_SECRET_ROW_VALUE_XYZ"
    client.post("/api/fi_master", json={"name": marker, "extra": marker})
    client.get("/api/fi_master")
    text = caplog.text
    assert marker not in text
    # But structured fields ARE present.
    assert "worksheet=fi_master" in text
