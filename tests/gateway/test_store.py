"""Persistence, encryption, tenant isolation and client-key lifecycle evidence."""
import os
from pathlib import Path
import sqlite3

import pytest

from model_gateway.errors import GatewayError
from model_gateway.store import GatewayStore, text
from model_gateway.wire import json_dumps, json_loads


def test_credentials_persist_encrypted_and_cas_rotation_fences_old_refs(tmp_path):
    store = GatewayStore(tmp_path)
    original = store.put("alice", "connection", "primary", {"name": "Main"}, secret="upstream-very-secret")
    assert store.resolve("alice", original["secret_ref"]) == "upstream-very-secret"
    assert b"upstream-very-secret" not in store.path.read_bytes()
    assert "secret_ref" not in store.public_connection(original)
    assert store.public_connection(original)["credential_configured"]
    reopened = GatewayStore(tmp_path)
    assert reopened.get("alice", "connection", "primary") == original
    for fn in (lambda: reopened.get("bob", "connection", "primary"), lambda: reopened.resolve("bob", original["secret_ref"]),
               lambda: reopened.put("alice", "connection", "primary", {}, 0, secret="replacement")):
        with pytest.raises(GatewayError): fn()
    updated = reopened.put("alice", "connection", "primary", {**original, "name": "Updated"}, 1, secret="rotated-secret")
    assert updated["revision"] == 2
    with pytest.raises(GatewayError): reopened.resolve("alice", original["secret_ref"])
    assert reopened.resolve("alice", updated["secret_ref"]) == "rotated-secret"
    cleared = reopened.put("alice", "connection", "primary", updated, 2, secret="")
    assert reopened.resolve("alice", cleared["secret_ref"]) is None


def test_disconnect_prunes_routes_and_destroys_only_own_secret(tmp_path):
    store = GatewayStore(tmp_path)
    a = store.put("alice", "connection", "a", {}, secret="secret-a")
    store.put("bob", "connection", "a", {}, secret="secret-b")
    store.put("alice", "route", "both", {"models": ["a/m", "b/m"]})
    store.put("alice", "route", "single", {"models": ["a/m"]})
    store.put("alice", "route", "other", {"models": ["b/m"]})
    with pytest.raises(GatewayError): store.delete_connection("alice", "a", 0)
    assert store.delete_connection("alice", "a", 1) == {"disconnected": True}
    assert store.get("alice", "route", "both")["models"] == ["b/m"]
    assert store.get("alice", "route", "other")["revision"] == 1
    with pytest.raises(GatewayError): store.get("alice", "route", "single")
    with pytest.raises(GatewayError): store.resolve("alice", a["secret_ref"])
    assert len(store.list("bob", "connection")) == 1
    with pytest.raises(GatewayError): store.delete_connection("alice", "a", 1)


def test_api_keys_only_display_once_hashed_revocable_and_scoped(tmp_path):
    store = GatewayStore(tmp_path)
    key = store.create_key("alice", "Application", ["main", "main"])
    assert key["models"] == ["main"]
    assert key["key"].encode() not in store.path.read_bytes()
    assert store.authenticate(key["key"])["tenant"] == "alice"
    assert not store.keys("bob") and "key" not in store.keys("alice")[0]
    with pytest.raises(GatewayError): store.revoke_key("bob", key["id"])
    store.revoke_key("alice", key["id"])
    assert store.keys("alice")[0]["revoked"]
    for token in (key["key"], None, "short", "stg_" + "x" * 40):
        with pytest.raises(GatewayError): store.authenticate(token)


def test_usage_keeps_unknown_distinct_from_zero_and_failed_consumption(tmp_path):
    store = GatewayStore(tmp_path)
    store.record_usage("a", "key", "main", "provider", "failed", {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10})
    store.record_usage("a", "key", "main", "provider", "completed")
    summary = store.usage("a")
    assert (summary["requests"], summary["total_tokens"], summary["failures"], summary["unreported"]) == (2, 10, 1, 1)
    assert summary["recent"][0]["tokens"] is None
    assert store.usage("b")["requests"] == 0
    with pytest.raises(GatewayError): store.record_usage("a", "k", "m", "c", "failed", {"total_tokens": -1})


@pytest.mark.parametrize("field,value", [("revision", -1), ("revision", True), ("secret", "line\nbreak"), ("secret", "x"*65537)])
def test_reject_unsafe_store_input(tmp_path, field, value):
    store = GatewayStore(tmp_path)
    with pytest.raises(GatewayError):
        store.put("a", "connection", "c", {}, **({"expected_revision": value} if field == "revision" else {"secret": value}))


@pytest.mark.parametrize("value", [None, "", "\x00", "x"*257])
def test_bounded_labels(value):
    with pytest.raises(GatewayError): text(value)


@pytest.mark.parametrize("models", ["all", [None], [""], ["x"*513], ["m"]*257])
def test_key_model_bounds(tmp_path, models):
    with pytest.raises(GatewayError): GatewayStore(tmp_path).create_key("a", "name", models)


def test_private_paths_and_corrupt_credentials_fail_closed(tmp_path):
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(GatewayError): GatewayStore(linked)
    real.chmod(0o755)
    with pytest.raises(GatewayError): GatewayStore(real)
    real.chmod(0o700)
    store = GatewayStore(real)
    row = store.put("a", "connection", "c", {}, secret="safe-key")
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE secrets SET ciphertext=?", (b"corrupt",))
    with pytest.raises(GatewayError): store.resolve("a", row["secret_ref"])
    (real / "master.key").write_bytes(b"short")
    with pytest.raises(GatewayError): GatewayStore(real)


@pytest.mark.parametrize("value", ['{"a":1,"a":2}', '{"a":NaN}', b"\xff", "[", None])
def test_strict_json(value):
    with pytest.raises(GatewayError): json_loads(value)


def test_json_serializer_rejects_nonfinite_and_error_public_shape():
    assert json_loads(json_dumps({"ok": [1, 2]})) == {"ok": [1, 2]}
    with pytest.raises(GatewayError): json_dumps({"bad": float("inf")})
    error = GatewayError("safe", "Safe explanation", status=503, retryable=True, upstream_secret="hidden")
    assert error.as_dict() == {"code": "safe", "message": "Safe explanation", "retryable": True}
