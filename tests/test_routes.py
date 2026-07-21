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
    assert set(body.keys()) == set(sheets.READ_ALIASES)
    for alias in sheets.READ_ALIASES:
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
