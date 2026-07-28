"""The on-disk secret store: hashing round-trip, config shape, and permissions.

Proves the ported hashing scheme behaves like the macOS ``SecretStore`` — a set
sequence verifies, a wrong one or a wrong length does not, and the stored config is
owner-only base64 with the same field names the Swift build writes.
"""

from __future__ import annotations

import base64
import json
import os
import stat


def test_set_then_matches(store):
    store.set_secret("hunter2!")
    assert store.has_secret()
    assert store.secret_length() == 8
    assert store.matches("hunter2!")


def test_wrong_sequence_and_length_rejected(store):
    store.set_secret("correct-horse")
    assert not store.matches("correct-hors")   # one short
    assert not store.matches("correct-horsee")  # one long
    assert not store.matches("wrong-sequence!")
    assert not store.matches("")


def test_no_secret_matches_nothing(store):
    assert not store.has_secret()
    assert store.secret_length() == 0
    assert not store.matches("anything")


def test_config_shape_and_permissions(store):
    store.set_secret("passphrase")
    config_path = store._config_url
    raw = json.loads(config_path.read_bytes())
    # Same field names / encoding the macOS JSONEncoder produces.
    assert set(raw.keys()) == {"salt", "hash", "length"}
    assert raw["length"] == 10
    assert len(base64.b64decode(raw["salt"])) == 32
    assert len(base64.b64decode(raw["hash"])) == 32
    mode = stat.S_IMODE(os.stat(config_path).st_mode)
    assert mode == 0o600


def test_persistence_across_reload(store, tmp_path):
    store.set_secret("remember-me")
    # Drop the in-process cache and re-read from disk, as a fresh launch would.
    store._cached = None
    assert store.has_secret()
    assert store.matches("remember-me")


def test_salt_is_random_per_secret(store, tmp_path):
    store.set_secret("same-text")
    first = store._config_url.read_bytes()
    store.set_secret("same-text")
    second = store._config_url.read_bytes()
    assert first != second  # a fresh 32-byte salt each time


def test_event_log_appends(store):
    store.log("ARMED")
    store.log("TRIPPED (wrong sequence)")
    text = store._log_url.read_text()
    assert "ARMED" in text
    assert "TRIPPED (wrong sequence)" in text
    assert text.count("\n") == 2
