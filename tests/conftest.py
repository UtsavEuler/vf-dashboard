"""Shared test fixtures. No real Google Sheet is ever touched: routes run
against an in-memory FakeAdapter injected into create_app."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vf_app import sheets  # noqa: E402
from vf_app.config import Config  # noqa: E402
from vf_app.routes import create_app  # noqa: E402


class FakeAdapter:
    """In-memory stand-in for GoogleSheetsAdapter that records every call so
    tests can assert the exact match keys used for writes/deletes."""

    def __init__(self):
        self.data = {alias: [] for alias in sheets.READ_ALIASES}
        self.calls = []  # list of (op, alias, match_keys)

    def read(self, alias):
        self.calls.append(("read", alias, None))
        return [dict(r) for r in self.data[alias]]

    def upsert(self, alias, match_keys, data_dict):
        self.calls.append(("upsert", alias, dict(match_keys)))
        for row in self.data[alias]:
            if all(str(row.get(k, "")) == str(v) for k, v in match_keys.items()):
                row.update(data_dict)
                return
        self.data[alias].append(dict(data_dict))

    def delete(self, alias, match_keys):
        self.calls.append(("delete", alias, dict(match_keys)))
        for i, row in enumerate(self.data[alias]):
            if all(str(row.get(k, "")) == str(v) for k, v in match_keys.items()):
                del self.data[alias][i]
                return True
        return False

    def append_snapshot(self, snap_dict):
        self.calls.append(("append_snapshot", "snapshots", None))
        self.data["snapshots"].append(dict(snap_dict))


def make_config(max_request_bytes=1048576):
    return Config(
        sheet_id="fake-sheet-id",
        credentials={"type": "service_account", "private_key": "x"},
        max_request_bytes=max_request_bytes,
        google_timeout_seconds=10,
    )


@pytest.fixture
def adapter():
    return FakeAdapter()


@pytest.fixture
def app_factory(adapter):
    def _factory(max_request_bytes=1048576):
        app = create_app(config=make_config(max_request_bytes), adapter=adapter)
        app.testing = True
        return app
    return _factory


@pytest.fixture
def app(app_factory):
    return app_factory()


@pytest.fixture
def client(app):
    return app.test_client()
