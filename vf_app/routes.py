"""HTTP interface: explicit HTML and JSON routes.

No generic filesystem serving. Only `/` and `/eligibility` expose HTML; every
other path is a 404. Access is gated by Jarvis navigation during the temporary
public-embed deployment.
"""

import logging
import os
import time

from flask import Flask, Response, jsonify, request
from werkzeug.exceptions import HTTPException

from . import sheets
from .config import get_config
from .errors import (
    NotFoundError,
    PayloadTooLargeError,
    ValidationError,
    register_error_handlers,
)

log = logging.getLogger("vf_app")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASHBOARD_HTML = os.path.join(BASE_DIR, "euler_vf.html")
ELIGIBILITY_HTML = os.path.join(BASE_DIR, "euler_loan_eligibility.html")

def _serve_html(path):
    with open(path, "r", encoding="utf-8") as fh:
        return Response(fh.read(), mimetype="text/html")


def _structured_log(route, op, alias, outcome, started):
    """Redacted structured log line: route, op, worksheet alias, outcome,
    latency. Never logs request/response bodies, secrets or Sheet rows."""
    latency_ms = int((time.monotonic() - started) * 1000)
    log.info("route=%s op=%s worksheet=%s outcome=%s latency_ms=%s",
             route, op, alias or "-", outcome, latency_ms)


def create_app(config=None, adapter=None):
    """Application factory. Optionally inject a Config and a Sheets adapter
    (used by tests); otherwise the process-cached defaults are used."""
    cfg = config or get_config()

    app = Flask(__name__)
    # Reject oversized bodies before parsing (Werkzeug raises 413).
    app.config["MAX_CONTENT_LENGTH"] = cfg.max_request_bytes

    register_error_handlers(app)

    def _adapter():
        return adapter if adapter is not None else sheets.get_adapter(cfg)

    # ── Security headers ──────────────────────────────────────────────────
    @app.after_request
    def _security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault(
            "Referrer-Policy", "strict-origin-when-cross-origin"
        )
        resp.headers.setdefault(
            "Content-Security-Policy",
            "frame-ancestors https://jarvis.eulerlogistics.com "
            "https://staging-jarvis.eulerlogistics.com",
        )
        return resp

    # ── Body helpers ──────────────────────────────────────────────────────
    def _json_body():
        # MAX_CONTENT_LENGTH already guards size; double-check Content-Length so
        # an oversized body maps to 413 before we ever parse it.
        clen = request.content_length
        if clen is not None and clen > cfg.max_request_bytes:
            raise PayloadTooLargeError()
        try:
            data = request.get_json(force=True, silent=False)
        except HTTPException:
            # Werkzeug raises 413 (too large) or 400 (bad request); let the
            # registered handlers map these to stable statuses.
            raise
        except Exception as exc:  # malformed JSON
            raise ValidationError("Malformed JSON body") from exc
        if not isinstance(data, dict):
            raise ValidationError("Request body must be a JSON object")
        return data

    def _validate_keys(alias, source):
        """Ensure every composite match key is present and non-empty."""
        missing = [k for k in sheets.MATCH_KEYS[alias]
                   if not str(source.get(k, "")).strip()]
        if missing:
            raise ValidationError("Missing required field(s)")

    def _require(source, *names):
        """Return the named fields, rejecting any that are absent or blank.

        Values are stringified without an `or ""` fallback so a JSON `0` (a legal
        ESP) survives as "0" rather than collapsing to blank. Returned values are
        whitespace-stripped.
        """
        values = []
        for name in names:
            raw = source.get(name, "")
            value = "" if raw is None else str(raw).strip()
            if not value:
                raise ValidationError("Missing required field(s)")
            values.append(value)
        return values

    # ── Pages ─────────────────────────────────────────────────────────────
    @app.route("/")
    def index():
        return _serve_html(DASHBOARD_HTML)

    @app.route("/eligibility")
    def eligibility():
        return _serve_html(ELIGIBILITY_HTML)

    @app.route("/health")
    def health():
        # Readiness only: confirms the process is up and configuration loaded.
        # Never touches Sheets and never discloses secret values.
        return jsonify({"status": "ok", "config_loaded": True})

    # ── Bootstrap: whole initial read model in one response ───────────────
    @app.route("/api/bootstrap", methods=["GET"])
    def bootstrap():
        started = time.monotonic()
        try:
            ad = _adapter()
            model = {alias: ad.read(alias) for alias in sheets.BOOTSTRAP_ALIASES}
            _structured_log("/api/bootstrap", "read", "*", "ok", started)
            return jsonify(model)
        except Exception:
            _structured_log("/api/bootstrap", "read", "*", "error", started)
            raise

    # ── Per-resource reads ────────────────────────────────────────────────
    @app.route("/api/<alias>", methods=["GET"])
    def api_get(alias):
        if alias not in sheets.READ_ALIASES:
            raise NotFoundError()
        started = time.monotonic()
        try:
            data = _adapter().read(alias)
            _structured_log(f"/api/{alias}", "read", alias, "ok", started)
            return jsonify(data)
        except Exception:
            _structured_log(f"/api/{alias}", "read", alias, "error", started)
            raise

    # ── Per-resource writes (upsert / snapshot append) ────────────────────
    @app.route("/api/<alias>", methods=["POST"])
    def api_post(alias):
        started = time.monotonic()
        body = _json_body()
        ad = _adapter()
        try:
            if alias == "snapshots":
                ad.append_snapshot(body)
            elif alias in sheets.UPSERT_ALIASES:
                _validate_keys(alias, body)
                keys = {k: body[k] for k in sheets.MATCH_KEYS[alias]}
                ad.upsert(alias, keys, body)
            else:
                raise NotFoundError()
            _structured_log(f"/api/{alias}", "write", alias, "ok", started)
            return jsonify({"ok": True})
        except Exception:
            _structured_log(f"/api/{alias}", "write", alias, "error", started)
            raise

    # ── Per-resource deletes (full composite key from query string) ───────
    @app.route("/api/<alias>", methods=["DELETE"])
    def api_delete(alias):
        if alias not in sheets.DELETE_ALIASES:
            raise NotFoundError()
        started = time.monotonic()
        args = request.args
        _validate_keys(alias, args)
        keys = {k: args[k] for k in sheets.MATCH_KEYS[alias]}
        try:
            _adapter().delete(alias, keys)
            _structured_log(f"/api/{alias}", "delete", alias, "ok", started)
            return jsonify({"ok": True})
        except Exception:
            _structured_log(f"/api/{alias}", "delete", alias, "error", started)
            raise

    # ── DP/IRR: the shapes the generic alias routes cannot express ────────
    # Werkzeug prefers static rules over converter rules, so every route below
    # wins over /api/<alias> for its exact path.

    @app.route("/api/dpirr_entries_bulk", methods=["POST"])
    def dpirr_entries_bulk():
        """Append many entries in one Sheets call (bulk order creation)."""
        started = time.monotonic()
        body = _json_body()
        entries = body.get("entries")
        if (not isinstance(entries, list) or not entries
                or not all(isinstance(e, dict) for e in entries)):
            raise ValidationError("entries must be a non-empty list of objects")
        try:
            added = _adapter().append_rows("dpirr_entries", entries)
            _structured_log("/api/dpirr_entries_bulk", "write", "dpirr_entries",
                            "ok", started)
            return jsonify({"ok": True, "result": {"added": added}})
        except Exception:
            _structured_log("/api/dpirr_entries_bulk", "write", "dpirr_entries",
                            "error", started)
            raise

    @app.route("/api/dpirr_variants_bulk", methods=["POST"])
    def dpirr_variants_bulk():
        """Bulk upsert a product's models + variants in a fixed number of Sheets
        calls, so an uploaded spreadsheet of any size cannot trip write limits."""
        started = time.monotonic()
        body = _json_body()
        (product,) = _require(body, "product")
        rows = body.get("rows")
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise ValidationError("rows must be a list of objects")
        try:
            ad = _adapter()
            new_models, all_variants, stats = sheets.plan_variant_bulk(
                product, rows, ad.read("dpirr_models"), ad.read("dpirr_variants"))
            if new_models:
                ad.append_rows("dpirr_models", new_models)
            ad.replace_all("dpirr_variants", all_variants)
            _structured_log("/api/dpirr_variants_bulk", "write",
                            "dpirr_variants", "ok", started)
            return jsonify({"ok": True, "result": stats})
        except Exception:
            _structured_log("/api/dpirr_variants_bulk", "write",
                            "dpirr_variants", "error", started)
            raise

    @app.route("/api/dpirr_products_rename", methods=["POST"])
    def dpirr_products_rename():
        """Rename a product and cascade the rename to its models and variants."""
        started = time.monotonic()
        body = _json_body()
        old_name, new_name = _require(body, "oldName", "newName")
        try:
            ad = _adapter()
            ad.upsert("dpirr_products", {"name": old_name}, {"name": new_name})
            ad.bulk_update("dpirr_models", {"product": old_name},
                           {"product": new_name})
            ad.bulk_update("dpirr_variants", {"product": old_name},
                           {"product": new_name})
            _structured_log("/api/dpirr_products_rename", "write",
                            "dpirr_products", "ok", started)
            return jsonify({"ok": True})
        except Exception:
            _structured_log("/api/dpirr_products_rename", "write",
                            "dpirr_products", "error", started)
            raise

    @app.route("/api/dpirr_models_rename", methods=["POST"])
    def dpirr_models_rename():
        """Rename a model and cascade the rename to its variants."""
        started = time.monotonic()
        body = _json_body()
        product, old_name, new_name = _require(body, "product", "oldName",
                                               "newName")
        try:
            ad = _adapter()
            ad.upsert("dpirr_models", {"product": product, "name": old_name},
                      {"product": product, "name": new_name})
            ad.bulk_update("dpirr_variants",
                           {"product": product, "model": old_name},
                           {"model": new_name})
            _structured_log("/api/dpirr_models_rename", "write", "dpirr_models",
                            "ok", started)
            return jsonify({"ok": True})
        except Exception:
            _structured_log("/api/dpirr_models_rename", "write", "dpirr_models",
                            "error", started)
            raise

    @app.route("/api/dpirr_variants", methods=["POST"])
    def dpirr_variants_save():
        """Add or update a variant. Matching on oldVariant (when supplied) is what
        makes a rename land on the existing row instead of creating a new one.

        esp is REQUIRED: upsert rewrites the whole row, so accepting a missing esp
        would blank an existing variant's price on a rename.
        """
        started = time.monotonic()
        body = _json_body()
        product, model, variant, esp = _require(body, "product", "model",
                                                "variant", "esp")
        raw_old = body.get("oldVariant", "")
        old_variant = str(raw_old).strip() if raw_old is not None else ""
        old_variant = old_variant or variant
        try:
            _adapter().upsert(
                "dpirr_variants",
                {"product": product, "model": model, "variant": old_variant},
                {"product": product, "model": model, "variant": variant,
                 "esp": esp},
            )
            _structured_log("/api/dpirr_variants", "write", "dpirr_variants",
                            "ok", started)
            return jsonify({"ok": True})
        except Exception:
            _structured_log("/api/dpirr_variants", "write", "dpirr_variants",
                            "error", started)
            raise

    @app.route("/api/dpirr_products", methods=["DELETE"])
    def dpirr_products_delete():
        """Delete a product and cascade-delete all its models and variants."""
        started = time.monotonic()
        (name,) = _require(request.args, "name")
        try:
            ad = _adapter()
            ad.delete("dpirr_products", {"name": name})
            ad.bulk_delete("dpirr_models", {"product": name})
            ad.bulk_delete("dpirr_variants", {"product": name})
            _structured_log("/api/dpirr_products", "delete", "dpirr_products",
                            "ok", started)
            return jsonify({"ok": True})
        except Exception:
            _structured_log("/api/dpirr_products", "delete", "dpirr_products",
                            "error", started)
            raise

    @app.route("/api/dpirr_models", methods=["DELETE"])
    def dpirr_models_delete():
        """Delete a model and cascade-delete all its variants."""
        started = time.monotonic()
        product, name = _require(request.args, "product", "name")
        try:
            ad = _adapter()
            ad.delete("dpirr_models", {"product": product, "name": name})
            ad.bulk_delete("dpirr_variants", {"product": product, "model": name})
            _structured_log("/api/dpirr_models", "delete", "dpirr_models", "ok",
                            started)
            return jsonify({"ok": True})
        except Exception:
            _structured_log("/api/dpirr_models", "delete", "dpirr_models",
                            "error", started)
            raise

    return app
