from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.protocol import CoreError
from consumer.config import Settings
from consumer.contracts import MAX_BODY, VERSION, failure, identifier, object_body, public_record, success


@pytest.mark.parametrize("value", ["", "a/b", ".", "a b", "é", "x" * 129, 1, None, "a\n"])
def test_identifier_rejects_untrusted_paths(value):
    with pytest.raises(CoreError):
        identifier(value)


def test_contracts_reject_unknown_missing_oversized_and_non_json():
    assert identifier("agent_01-X") == "agent_01-X"
    assert object_body({"text": "hi"}, {"text"}, {"text"}) == {"text": "hi"}
    for body in ([], {1: 2}, {"extra": 1}, {}):
        with pytest.raises(CoreError):
            object_body(body, {"text"}, {"text"})
    with pytest.raises(CoreError, match="byte limit"):
        object_body({"text": "x" * MAX_BODY}, {"text"})
    with pytest.raises((CoreError, ValueError)):
        success(float("nan"))


def test_envelopes_protect_metadata_and_unexpected_errors():
    record = SimpleNamespace(id="true", revision=2, value={"id": "forged", "revision": 99, "name": "ok"})
    assert public_record(record) == {"id": "true", "revision": 2, "name": "ok"}
    with pytest.raises(CoreError):
        public_record(SimpleNamespace(value=[]))
    assert success(None, "req1") == {"version": VERSION, "request_id": "req1", "data": {}, "error": None}
    assert "SECRET" not in str(failure(RuntimeError("SECRET"), "req1"))
    assert failure(CoreError("denied", "Denied", body="x" * 5000))["error"]["details"] == {"truncated": True}
    assert success([1])["data"] == [1]


def test_operator_settings_are_canonical_immutable_and_secret_repr(tmp_path):
    settings = Settings(root=tmp_path / "../data", bootstrap_token="SECRET", private_network_origins=("http://localhost:9000/", "http://localhost:9000"))
    assert settings.root == (tmp_path.parent / "data").resolve()
    assert settings.native_binary.is_absolute()
    assert settings.private_network_origins == ("http://localhost:9000",)
    assert "SECRET" not in repr(settings)
    with pytest.raises(AttributeError):
        settings.mode = "hosted"
    assert Settings(native_binary="worker").native_binary == Path("worker").resolve()


@pytest.mark.parametrize("options", [
    {"mode": "anything"}, {"host": "a/b"}, {"host": 1}, {"port": True}, {"port": -1}, {"port": 65536},
    {"worker_timeout": float("nan")}, {"worker_timeout": True}, {"worker_timeout": "3"}, {"worker_timeout": 0},
    {"allow_signup": 1}, {"bootstrap_token": 1}, {"private_network_origins": ("http://x/path",)},
    {"private_network_origins": ("http://u:p@host",)}, {"private_network_origins": ("http://host:bad",)},
    {"private_network_origins": ("file:///tmp",)},
])
def test_settings_fail_closed(options):
    with pytest.raises(CoreError):
        Settings(**options)


def test_environment_loading(tmp_path):
    settings = Settings.from_env({"STURGIS_ROOT": str(tmp_path), "STURGIS_PORT": "9000", "STURGIS_WORKER_TIMEOUT": "5", "STURGIS_ALLOW_SIGNUP": "true", "STURGIS_PRIVATE_NETWORK_ORIGINS": "http://localhost:80"})
    assert settings.root == tmp_path and settings.port == 9000 and settings.worker_timeout == 5
    assert settings.allow_signup
    assert not Settings.from_env({}).allow_signup
    for env in ({"STURGIS_PORT": "bad"}, {"STURGIS_WORKER_TIMEOUT": "bad"}, {"STURGIS_ALLOW_SIGNUP": "yes"}):
        with pytest.raises(CoreError):
            Settings.from_env(env)
