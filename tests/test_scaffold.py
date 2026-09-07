"""Offline Phase 0 tests: crypto round-trip, DB migrations, policy merge.

No network, no Google. Run with: uv run pytest
"""

from __future__ import annotations

import pytest

from hermes_ops.auth import crypto
from hermes_ops.policy.loader import DEFAULT_POLICY, load_policy
from hermes_ops.store import db

KEY = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"  # 64 hex chars


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("OPS_ENCRYPTION_KEY", KEY)


def test_generate_key_is_64_hex_chars():
    k = crypto.generate_key()
    assert len(k) == 64
    bytes.fromhex(k)  # no exception


def test_encrypt_decrypt_roundtrip(key):
    payload = {"token": "abc", "refresh_token": "xyz", "scopes": ["a", "b"]}
    blob = crypto.encrypt_json(payload)
    assert crypto.decrypt_json(blob) == payload


def test_decrypt_with_wrong_key_fails(monkeypatch):
    monkeypatch.setenv("OPS_ENCRYPTION_KEY", KEY)
    blob = crypto.encrypt_json({"token": "abc"})
    monkeypatch.setenv("OPS_ENCRYPTION_KEY", "f" * 64)
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt_json(blob)


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("OPS_ENCRYPTION_KEY", raising=False)
    with pytest.raises(crypto.CryptoError):
        crypto.encrypt_json({"x": 1})


def test_write_read_encrypted(key, tmp_path):
    path = tmp_path / "google_token.json"
    crypto.write_encrypted(path, {"token": "t"})
    assert path.exists()
    assert crypto.read_encrypted(path) == {"token": "t"}


def test_migrations_reach_latest_and_create_tables(tmp_path):
    dbfile = tmp_path / "state.db"
    version = db.init(dbfile)
    assert version == db.LATEST

    conn = db.connect(dbfile)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"messages", "events", "links", "audit", "drafts", "meta"} <= tables
        cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
        assert "last_inbound_at" in cols  # migration 2 applied
    finally:
        conn.close()


def test_migrations_are_idempotent(tmp_path):
    dbfile = tmp_path / "state.db"
    assert db.init(dbfile) == db.LATEST
    assert db.init(dbfile) == db.LATEST  # second run is a no-op


def test_policy_defaults_when_no_file(tmp_path):
    pol = load_policy(tmp_path / "nope.yaml")
    assert pol["timezone"] == DEFAULT_POLICY["timezone"]
    assert pol["ingestion"]["extracted_requires_confirmation"] is True


def test_policy_deep_merge(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("timezone: UTC\nsenders:\n  collaborators: [alice]\n", encoding="utf-8")
    pol = load_policy(p)
    assert pol["timezone"] == "UTC"
    assert pol["senders"]["collaborators"] == ["alice"]
    # untouched keys keep their defaults
    assert pol["senders"]["important_domains"] == []
    assert pol["calendars"]["default"] == "hermes"
