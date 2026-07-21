"""Validated environment configuration for the VF dashboard.

Loads and validates settings at startup and fails fast with a clear error when a
required setting is missing. Required: GOOGLE_SHEET_ID, GOOGLE_CREDENTIALS.
Optional (with defaults): MAX_REQUEST_BYTES,
GOOGLE_REQUEST_TIMEOUT_SECONDS.

GOOGLE_CREDENTIALS is the complete service-account JSON string. As a local-dev
convenience only, if GOOGLE_CREDENTIALS is not set we fall back to a
`credentials.json` file if one is present; production must always set
GOOGLE_CREDENTIALS and never depends on the file being present.
"""

import json
import os

# Repo root = parent of this package directory.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CREDS_FILE = os.path.join(BASE_DIR, "credentials.json")

DEFAULT_MAX_REQUEST_BYTES = 1048576
DEFAULT_GOOGLE_TIMEOUT_SECONDS = 10


class ConfigError(RuntimeError):
    """Raised when configuration is missing or invalid."""


def _fix_private_key(creds_dict):
    # Some environments double-escape newlines in the private key. Undo that so
    # the PEM parses. (Mirrors the behaviour of the original server.py.)
    if isinstance(creds_dict, dict) and "private_key" in creds_dict:
        creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
    return creds_dict


def _load_credentials(env):
    """Resolve service-account credentials to a dict.

    Prefers the GOOGLE_CREDENTIALS env var (full JSON string). Falls back to a
    local credentials.json file only when the env var is absent.
    """
    raw = env.get("GOOGLE_CREDENTIALS")
    if raw:
        try:
            return _fix_private_key(json.loads(raw))
        except (ValueError, TypeError) as exc:
            raise ConfigError(
                "GOOGLE_CREDENTIALS is set but is not valid JSON."
            ) from exc
    if os.path.exists(CREDS_FILE):
        try:
            with open(CREDS_FILE, "r", encoding="utf-8") as fh:
                return _fix_private_key(json.load(fh))
        except (ValueError, OSError) as exc:
            raise ConfigError(
                "credentials.json is present but could not be read as JSON."
            ) from exc
    return None


def _parse_int(env, name, default):
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (ValueError, TypeError) as exc:
        raise ConfigError(f"{name} must be an integer.") from exc
    if value <= 0:
        raise ConfigError(f"{name} must be a positive integer.")
    return value


class Config:
    """Immutable resolved configuration."""

    def __init__(
        self, sheet_id, credentials, max_request_bytes, google_timeout_seconds
    ):
        self.sheet_id = sheet_id
        self.credentials = credentials
        self.max_request_bytes = max_request_bytes
        self.google_timeout_seconds = google_timeout_seconds


def load_config(env=None):
    """Build and validate a Config from the given environment mapping.

    Raises ConfigError listing every missing required setting.
    """
    env = os.environ if env is None else env

    missing = []
    sheet_id = env.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        missing.append("GOOGLE_SHEET_ID")

    credentials = _load_credentials(env)
    if credentials is None:
        # Required: either the env var or, locally, credentials.json.
        missing.append("GOOGLE_CREDENTIALS")

    if missing:
        raise ConfigError(
            "Missing required configuration: "
            + ", ".join(sorted(missing))
            + ". Set these environment variables (see .env.example)."
        )

    return Config(
        sheet_id=sheet_id,
        credentials=credentials,
        max_request_bytes=_parse_int(
            env, "MAX_REQUEST_BYTES", DEFAULT_MAX_REQUEST_BYTES
        ),
        google_timeout_seconds=_parse_int(
            env, "GOOGLE_REQUEST_TIMEOUT_SECONDS", DEFAULT_GOOGLE_TIMEOUT_SECONDS
        ),
    )


_config = None


def get_config(env=None):
    """Return a process-cached Config, loading it on first use."""
    global _config
    if _config is None:
        _config = load_config(env)
    return _config


def reset_config():
    """Clear the cached config (used by tests)."""
    global _config
    _config = None
