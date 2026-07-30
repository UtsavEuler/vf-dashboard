"""Static contract checks over the Python modules and the HTML pages."""

import os
import py_compile
import re
import shutil
import subprocess
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VF_HTML = os.path.join(ROOT, "euler_vf.html")
ELIG_HTML = os.path.join(ROOT, "euler_loan_eligibility.html")

PY_FILES = [
    "api/index.py",
    "server.py",
    "vf_app/__init__.py",
    "vf_app/config.py",
    "vf_app/routes.py",
    "vf_app/sheets.py",
    "vf_app/errors.py",
]

# Inline <script> blocks that have no src attribute.
_INLINE_SCRIPT = re.compile(
    r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE)


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


# ── Python compiles ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("rel", PY_FILES)
def test_python_compiles(rel):
    py_compile.compile(os.path.join(ROOT, rel), doraise=True)


# ── Inline JS syntax-checks ──────────────────────────────────────────────────
@pytest.mark.parametrize("html_path", [VF_HTML, ELIG_HTML])
def test_inline_js_syntax(html_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available; skipping JS syntax check")
    scripts = _INLINE_SCRIPT.findall(_read(html_path))
    assert scripts, "expected at least one inline script block"
    for i, body in enumerate(scripts):
        if not body.strip():
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as fh:
            fh.write(body)
            tmp = fh.name
        try:
            proc = subprocess.run([node, "--check", tmp],
                                  capture_output=True, text=True)
            assert proc.returncode == 0, (
                f"JS syntax error in {os.path.basename(html_path)} "
                f"block {i}:\n{proc.stderr}")
        finally:
            os.unlink(tmp)


# ── Forbidden strings ────────────────────────────────────────────────────────
def test_no_railway_url():
    for path in (VF_HTML, ELIG_HTML):
        text = _read(path).lower()
        assert "railway" not in text
        assert "up.railway.app" not in text


def test_no_hardcoded_deployment_hostname():
    for path in (VF_HTML, ELIG_HTML):
        text = _read(path).lower()
        assert "web-production-43ecd" not in text
        assert ".vercel.app" not in text


def test_no_login_logout_or_session_tokens():
    for path in (VF_HTML, ELIG_HTML):
        text = _read(path)
        assert "/api/login" not in text
        assert "/api/logout" not in text
        assert "vf_token" not in text
        assert "X-Session-Token" not in text


def test_no_service_worker():
    for path in (VF_HTML, ELIG_HTML):
        text = _read(path)
        assert "serviceWorker" not in text
        assert "navigator.serviceWorker" not in text


def test_taif_write_has_no_backend_and_is_the_only_such_gap():
    """TA/IF is a KNOWN, DELIBERATE gap, recorded here so it cannot be forgotten.

    euler_vf.html posts to /api/taif but no backend has ever implemented it — no
    worksheet, no handler, in server.py or vf_app. The intended persistence
    behaviour is an open product question, so the call is left as-is. The previous
    version of this test asserted `API.post('api/taif'` (no leading slash), which
    never appeared in the file and so passed vacuously. This asserts reality: the
    frontend call exists, and no Python module answers it.
    """
    text = _read(VF_HTML)
    assert "API.post('/api/taif'" in text, (
        "TA/IF call disappeared — if it was wired up or removed, update "
        "UNBACKED_FRONTEND_ROUTES and this test")
    for rel in PY_FILES:
        assert "taif" not in _read(os.path.join(ROOT, rel)).lower()


# ── server.py must stay a thin dev runner ────────────────────────────────────
def test_server_py_is_a_thin_runner_over_the_vercel_app():
    """server.py must run the SAME app Vercel serves, never its own HTTP stack.

    A previous partial revert turned server.py back into a standalone
    http.server monolith, so `make run` exercised code that production never
    ran and the DP/IRR endpoints existed only locally. This kills that whole
    drift class.
    """
    text = _read(os.path.join(ROOT, "server.py"))
    assert "from api.index import app" in text
    for forbidden in ("@app.route", "BaseHTTPRequestHandler",
                      "SimpleHTTPRequestHandler", "HTTPServer"):
        assert forbidden not in text, (
            f"server.py defines its own HTTP stack ({forbidden}); it must only "
            "run the app from api/index.py")


# ── Every /api path the frontend calls must exist in the Flask app ───────────
# (method, path) pairs the frontend calls that DELIBERATELY have no backend.
# Each entry is a known gap, not a licence to add more.
UNBACKED_FRONTEND_ROUTES = {
    # TA/IF has never been persisted anywhere: no worksheet, no handler, in
    # server.py or vf_app. Wiring it up is an open product question.
    ("POST", "/api/taif"),
}

_API_CALL = re.compile(
    r"""API\.(get|post|delete)\(\s*(['"`])((?:\\.|(?!\2)[^\\])*)\2""")

# The initial load reads a list of paths (Promise.allSettled over READ_PATHS)
# rather than writing each URL at its own API.get call site, so the literals live
# in the array. They are all GETs.
_READ_PATHS_BLOCK = re.compile(r"READ_PATHS\s*=\s*\[(.*?)\]", re.DOTALL)
_QUOTED_PATH = re.compile(r"""(['"`])(/api/[^'"`]*)\1""")

_METHOD_BY_VERB = {"get": "GET", "post": "POST", "delete": "DELETE"}


def _clean_path(raw):
    """Keep the leading literal path: strip query string, hash and any point at
    which the URL starts being built by concatenation/interpolation."""
    return raw.split("?")[0].split("#")[0].split("${")[0]


def _frontend_api_calls(text):
    """Extract (METHOD, path) for every /api URL the page requests.

    Call sites build URLs by concatenation (`'/api/x?id=' + encodeURIComponent(..)`)
    and with template literals, so only the leading literal matters.
    """
    found = set()
    for verb, _quote, raw in _API_CALL.findall(text):
        path = _clean_path(raw)
        if path.startswith("/api/"):
            found.add((_METHOD_BY_VERB[verb], path))
    for block in _READ_PATHS_BLOCK.findall(text):
        for _quote, raw in _QUOTED_PATH.findall(block):
            found.add(("GET", _clean_path(raw)))
    return found


def test_frontend_api_calls_all_resolve_in_the_flask_app():
    """The guard that would have caught the DP/IRR 404s on day one."""
    import sys

    sys.path.insert(0, ROOT)
    from tests.conftest import FakeAdapter, make_config  # noqa: PLC0415
    from vf_app.routes import create_app  # noqa: PLC0415

    app = create_app(config=make_config(), adapter=FakeAdapter())
    app.testing = True
    client = app.test_client()

    calls = _frontend_api_calls(_read(VF_HTML))
    assert calls, "expected to find API.* call sites in euler_vf.html"

    # A URL-map match is not enough: the generic /api/<alias> rule matches ANY
    # alias and then 404s inside the handler for aliases it does not serve (this
    # is exactly how /api/taif looks routable while being unimplemented). So make
    # the real request and treat 404/405 as "no backend". A 400 means the route
    # exists and merely rejected our deliberately empty payload.
    unresolved = set()
    for method, path in sorted(calls):
        resp = client.open(path, method=method, json={})
        if resp.status_code in (404, 405):
            unresolved.add((method, path))

    assert unresolved == UNBACKED_FRONTEND_ROUTES, (
        f"frontend calls with no matching Flask route: "
        f"{sorted(unresolved - UNBACKED_FRONTEND_ROUTES)}; "
        f"allowlisted but now backed: "
        f"{sorted(UNBACKED_FRONTEND_ROUTES - unresolved)}")


def test_frontend_api_calls_include_the_dpirr_surface():
    """Pin the extractor itself: if it silently stopped finding call sites the
    coverage test above would pass with nothing to check."""
    calls = _frontend_api_calls(_read(VF_HTML))
    for expected in [("GET", "/api/dpirr_months"),
                     ("GET", "/api/dpirr_entries"),
                     ("GET", "/api/dpirr_products"),
                     ("GET", "/api/dpirr_models"),
                     ("GET", "/api/dpirr_variants"),
                     ("POST", "/api/dpirr_months"),
                     ("POST", "/api/dpirr_entries"),
                     ("POST", "/api/dpirr_entries_bulk"),
                     ("POST", "/api/dpirr_products"),
                     ("POST", "/api/dpirr_products_rename"),
                     ("POST", "/api/dpirr_models"),
                     ("POST", "/api/dpirr_models_rename"),
                     ("POST", "/api/dpirr_variants"),
                     ("POST", "/api/dpirr_variants_bulk"),
                     ("DELETE", "/api/dpirr_entries"),
                     ("DELETE", "/api/dpirr_products"),
                     ("DELETE", "/api/dpirr_models"),
                     ("DELETE", "/api/dpirr_variants")]:
        assert expected in calls, f"{expected} no longer found in euler_vf.html"
    # READ_PATHS extraction is load-bearing for the whole initial read surface.
    for alias in ("fi_master", "dealer_master", "added_dealers", "onboarding",
                  "fi_policy", "dealer_health", "fi_policy_geo", "snapshots"):
        assert ("GET", f"/api/{alias}") in calls


# ── The frontend must surface failures, never swallow them ───────────────────
def test_api_handle_rejects_non_2xx():
    """A reverted _handle turned every 404 into an empty table. Never again."""
    text = _read(VF_HTML)
    assert "if (!r.ok) return Promise.reject" in text
    assert "catch(e) { console.error('JSON parse error:'" not in text


def test_initial_load_degrades_per_section():
    """Rejecting non-2xx must not make the dashboard all-or-nothing.

    With Promise.all, one read returning 502/504 blanked all 13 sections. The
    initial load uses allSettled so a single failure empties and flags ONE
    section. Writes are untouched and still fail loud.
    """
    text = _read(VF_HTML)
    assert "Promise.allSettled(READ_PATHS.map(" in text
    assert "READ_PATHS.map(function(p){ return API.get(p); })" in text
    # And still hard-fails when nothing at all loaded.
    assert "Every dashboard read failed" in text
    # No API.get list may be re-coupled with Promise.all.
    assert "Promise.all([" not in text


# ── No real secrets in tracked config ────────────────────────────────────────
def test_env_example_has_names_only():
    text = _read(os.path.join(ROOT, ".env.example"))
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        assert "=" in line
        key, _, value = line.partition("=")
        # Only the documented default limits may carry a value.
        if key in ("MAX_REQUEST_BYTES", "GOOGLE_REQUEST_TIMEOUT_SECONDS"):
            continue
        assert value == "", f"{key} must have no value in .env.example"


def test_gitignore_covers_secrets():
    text = _read(os.path.join(ROOT, ".gitignore"))
    for needed in ("credentials.json", ".env", ".vercel/"):
        assert needed in text


def test_no_hardcoded_sheet_id_or_password_in_python():
    for rel in PY_FILES:
        text = _read(os.path.join(ROOT, rel))
        assert "1jWmwJJZJzLX0oCSeRm24bCNNQ29pn0jAOl9Y9pUlU-4" not in text
        assert "euler@1234$" not in text
