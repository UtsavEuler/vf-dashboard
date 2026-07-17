"""Route contract tests against the fake Sheets adapter."""

import logging
from urllib.parse import urlencode

import pytest

from tests.conftest import PROXY_SECRET
from vf_app import sheets

PROTECTED_PATHS = ["/", "/eligibility", "/api/bootstrap", "/api/fi_master"]


# ── Auth ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", PROTECTED_PATHS)
def test_missing_secret_forbidden(client, path):
    assert client.get(path).status_code == 403


@pytest.mark.parametrize("path", PROTECTED_PATHS)
def test_wrong_secret_forbidden(client, path):
    resp = client.get(path, headers={"X-Jarvis-Proxy-Token": "nope"})
    assert resp.status_code == 403


def test_health_needs_no_secret_and_hides_secrets(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert PROXY_SECRET not in body
    assert "fake-sheet-id" not in body
    assert resp.get_json().get("status") == "ok"


# ── Pages ────────────────────────────────────────────────────────────────────
def test_index_serves_dashboard(client, auth):
    resp = client.get("/", headers=auth)
    assert resp.status_code == 200
    assert "text/html" in resp.content_type
    assert "Euler Motors" in resp.get_data(as_text=True)


def test_eligibility_serves_page(client, auth):
    resp = client.get("/eligibility", headers=auth)
    assert resp.status_code == 200
    assert "text/html" in resp.content_type


def test_unknown_path_is_404_not_repo_file(client, auth):
    # Must never expose arbitrary repo files.
    assert client.get("/server.py", headers=auth).status_code == 404
    assert client.get("/credentials.json", headers=auth).status_code == 404
    assert client.get("/setup_sheets.py", headers=auth).status_code == 404


def test_unknown_api_alias_404(client, auth):
    assert client.get("/api/nonsense", headers=auth).status_code == 404


# ── Reads ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("alias", sheets.READ_ALIASES)
def test_each_get_returns_list_shape(client, auth, adapter, alias):
    adapter.data[alias] = [{"a": "1"}, {"a": "2"}]
    resp = client.get(f"/api/{alias}", headers=auth)
    assert resp.status_code == 200
    assert resp.get_json() == [{"a": "1"}, {"a": "2"}]


def test_bootstrap_returns_complete_contract(client, auth, adapter):
    for alias in sheets.READ_ALIASES:
        adapter.data[alias] = [{"k": alias}]
    resp = client.get("/api/bootstrap", headers=auth)
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body.keys()) == set(sheets.READ_ALIASES)
    for alias in sheets.READ_ALIASES:
        assert body[alias] == [{"k": alias}]


# ── Writes: match-key validation + intended keys ─────────────────────────────
def _full_body(alias):
    return {k: f"v_{k}" for k in sheets.MATCH_KEYS[alias]}


@pytest.mark.parametrize("alias", sheets.UPSERT_ALIASES)
def test_post_uses_intended_match_keys(client, auth, adapter, alias):
    body = _full_body(alias)
    body["extra"] = "payload"
    resp = client.post(f"/api/{alias}", json=body, headers=auth)
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    op, called_alias, keys = adapter.calls[-1]
    assert op == "upsert"
    assert called_alias == alias
    assert keys == {k: body[k] for k in sheets.MATCH_KEYS[alias]}


@pytest.mark.parametrize("alias", sheets.UPSERT_ALIASES)
def test_post_missing_match_key_400(client, auth, adapter, alias):
    body = _full_body(alias)
    # Drop the first required key.
    body.pop(sheets.MATCH_KEYS[alias][0])
    resp = client.post(f"/api/{alias}", json=body, headers=auth)
    assert resp.status_code == 400
    # No write should have happened.
    assert all(c[0] != "upsert" for c in adapter.calls)


def test_snapshot_post_appends_once(client, auth, adapter):
    resp = client.post("/api/snapshots", json={"snapshot_month": "Jul"}, headers=auth)
    assert resp.status_code == 200
    appends = [c for c in adapter.calls if c[0] == "append_snapshot"]
    assert len(appends) == 1
    assert len(adapter.data["snapshots"]) == 1


def test_post_unknown_alias_404(client, auth):
    assert client.post("/api/nonsense", json={}, headers=auth).status_code == 404


# ── Deletes: full composite key required ─────────────────────────────────────
@pytest.mark.parametrize("alias", sheets.DELETE_ALIASES)
def test_delete_requires_full_composite_key(client, auth, adapter, alias):
    keys = {k: f"v_{k}" for k in sheets.MATCH_KEYS[alias]}
    resp = client.delete(f"/api/{alias}?{urlencode(keys)}", headers=auth)
    assert resp.status_code == 200
    op, called_alias, used = adapter.calls[-1]
    assert op == "delete" and called_alias == alias
    assert used == keys


@pytest.mark.parametrize("alias", sheets.DELETE_ALIASES)
def test_delete_missing_key_400(client, auth, adapter, alias):
    keys = {k: f"v_{k}" for k in sheets.MATCH_KEYS[alias]}
    keys.pop(sheets.MATCH_KEYS[alias][0])
    resp = client.delete(f"/api/{alias}?{urlencode(keys)}", headers=auth)
    assert resp.status_code == 400
    assert all(c[0] != "delete" for c in adapter.calls)


def test_delete_unknown_alias_404(client, auth):
    # fi_policy and dealer_health have no delete route.
    assert client.delete("/api/fi_policy?financier=x&productKey=y",
                         headers=auth).status_code == 404


# ── Body limits ──────────────────────────────────────────────────────────────
def test_malformed_json_400(client, auth):
    resp = client.post("/api/fi_master", data="{not json",
                       content_type="application/json", headers=auth)
    assert resp.status_code == 400


def test_oversized_json_413(app_factory, auth):
    app = app_factory(max_request_bytes=50)
    client = app.test_client()
    big = {"name": "x" * 500}
    resp = client.post("/api/fi_master", json=big, headers=auth)
    assert resp.status_code == 413


# ── Logging redaction ────────────────────────────────────────────────────────
def test_logs_never_contain_secrets_or_rows(client, auth, caplog):
    caplog.set_level(logging.INFO, logger="vf_app")
    marker = "SUPER_SECRET_ROW_VALUE_XYZ"
    client.post("/api/fi_master", json={"name": marker, "extra": marker},
                headers={"X-Jarvis-Proxy-Token": PROXY_SECRET})
    client.get("/api/fi_master", headers=auth)
    text = caplog.text
    assert marker not in text
    assert PROXY_SECRET not in text
    # But structured fields ARE present.
    assert "worksheet=fi_master" in text
