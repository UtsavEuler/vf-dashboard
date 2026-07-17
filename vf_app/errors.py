"""Stable outward error mapping.

Maps internal failure modes to stable HTTP status codes with generic messages.
Raw exception text and secrets are never returned to the client.
"""

import logging

from flask import jsonify
from werkzeug.exceptions import HTTPException

log = logging.getLogger("vf_app")


class ApiError(Exception):
    """Base for errors that map to a stable HTTP status + generic message."""

    status = 500
    message = "Internal server error"

    def __init__(self, message=None):
        super().__init__(message or self.message)
        if message:
            self.message = message


class ValidationError(ApiError):
    status = 400
    message = "Invalid request"


class NotFoundError(ApiError):
    status = 404
    message = "Not found"


class PayloadTooLargeError(ApiError):
    status = 413
    message = "Request entity too large"


class AuthError(ApiError):
    status = 403
    message = "Forbidden"


class UpstreamError(ApiError):
    """A Google Sheets failure that is not a timeout."""

    status = 502
    message = "Upstream data store error"


class UpstreamTimeoutError(ApiError):
    status = 504
    message = "Upstream data store timeout"


def _json_error(status, message):
    resp = jsonify({"error": message})
    resp.status_code = status
    return resp


def register_error_handlers(app):
    """Attach handlers that translate exceptions to stable JSON responses."""

    @app.errorhandler(ApiError)
    def _handle_api_error(exc):
        # Log server-side faults with detail; never leak detail to the client.
        if exc.status >= 500:
            log.warning("api_error status=%s type=%s", exc.status,
                        type(exc).__name__)
        return _json_error(exc.status, exc.message)

    @app.errorhandler(HTTPException)
    def _handle_http_exception(exc):
        # Werkzeug raises 413 (RequestEntityTooLarge) and 400 (BadRequest) etc.
        # Return a generic message rather than Werkzeug's default HTML/body.
        return _json_error(exc.code or 500, exc.name or "Error")

    @app.errorhandler(Exception)
    def _handle_unexpected(exc):
        # Catch-all: never surface raw exception text or tracebacks.
        log.exception("unhandled_exception type=%s", type(exc).__name__)
        return _json_error(500, "Internal server error")
