"""Configuration validation tests."""

import pytest

from vf_app.config import ConfigError, load_config

MINIMAL = {
    "GOOGLE_SHEET_ID": "sid",
    "GOOGLE_CREDENTIALS": '{"type":"service_account","private_key":"a\\\\nb"}',
    "JARVIS_PROXY_SECRET": "secret",
}


def test_valid_config_loads_with_defaults():
    cfg = load_config(dict(MINIMAL))
    assert cfg.sheet_id == "sid"
    assert cfg.proxy_secret == "secret"
    assert cfg.max_request_bytes == 1048576
    assert cfg.google_timeout_seconds == 10
    # The \\n in the private key is normalised to a real newline.
    assert "\n" in cfg.credentials["private_key"]


@pytest.mark.parametrize("missing", ["GOOGLE_SHEET_ID", "JARVIS_PROXY_SECRET"])
def test_missing_required_raises(missing):
    env = dict(MINIMAL)
    del env[missing]
    with pytest.raises(ConfigError) as exc:
        load_config(env)
    assert missing in str(exc.value)


def test_missing_credentials_raises(monkeypatch):
    # Point the file fallback at a nonexistent path so only the env var counts.
    import vf_app.config as config_mod
    monkeypatch.setattr(config_mod, "CREDS_FILE", "/nonexistent/credentials.json")
    env = dict(MINIMAL)
    del env["GOOGLE_CREDENTIALS"]
    with pytest.raises(ConfigError) as exc:
        load_config(env)
    assert "GOOGLE_CREDENTIALS" in str(exc.value)


def test_invalid_credentials_json_raises():
    env = dict(MINIMAL)
    env["GOOGLE_CREDENTIALS"] = "{not valid json"
    with pytest.raises(ConfigError):
        load_config(env)


def test_invalid_int_limit_raises():
    env = dict(MINIMAL)
    env["MAX_REQUEST_BYTES"] = "not-a-number"
    with pytest.raises(ConfigError):
        load_config(env)
