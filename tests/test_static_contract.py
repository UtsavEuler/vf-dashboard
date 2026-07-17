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


def test_no_taif_write_route_in_frontend():
    # TA/IF persistence is disabled; the only remaining mention is a comment.
    text = _read(VF_HTML)
    assert "API.post('api/taif'" not in text
    assert 'API.post("api/taif"' not in text


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
