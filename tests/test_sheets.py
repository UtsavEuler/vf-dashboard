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
    }
    assert sheets.MATCH_KEYS["fi_master"] == ["name"]
    assert sheets.MATCH_KEYS["dealer_master"] == ["dealerName", "location"]
    assert sheets.MATCH_KEYS["added_dealers"] == ["dealer", "location"]
    assert sheets.MATCH_KEYS["onboarding"] == ["dealer", "location", "financier"]
    assert sheets.MATCH_KEYS["fi_policy"] == ["financier", "productKey"]
    assert sheets.MATCH_KEYS["dealer_health"] == ["dealer", "location"]
    assert sheets.MATCH_KEYS["fi_policy_geo"] == [
        "financier", "productKey", "seg", "state", "city"]
