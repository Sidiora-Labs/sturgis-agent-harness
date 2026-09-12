"""IPC boundary units use a real application; process/isolation proof is separate."""
import asyncio
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import time

import pytest

from agent.framing import read_frame, write_frame
from agent.protocol import CoreError, canonical_json, strict_loads
from consumer.application import AgentApplication
from consumer.config import Settings
from consumer.worker import WorkerServer
from consumer.workers import WorkerClient, WorkerConfig, WorkerManager, _atomic_private, _directory_fd, _read_private, signature


@pytest.fixture
async def private(tmp_path):
    manager = WorkerManager(Settings(root=tmp_path / "installation", worker_timeout=3))
    config = await manager.create("agent_a")
    app = AgentApplication(config)
    await app.start()
    client = manager.client("agent_a")
    server = await WorkerServer(config, app, client._key).start()
    yield manager, config, app, server, client
    await server.close(); await app.close(); await manager.close()


def frame(client, method="overview", params=None, **changes):
    value = {"version": "consumer/1", "request_id": "request", "method": method,
        "params": {} if params is None else params, "deadline": time.time() + 2, "nonce": "nonce"}
    value.update(changes)
    return {**value, "signature": signature(client._key, value)}


async def wire(config, value):
    fd = _directory_fd(config.private_root)
    try: reader, writer = await asyncio.open_unix_connection(f"/proc/self/fd/{fd}/worker.sock")
    finally: os.close(fd)
    try:
        await write_frame(writer, canonical_json(value).encode(), 8388608)
        return strict_loads(await read_frame(reader, 8388608))
    finally:
        writer.close(); await writer.wait_closed()


async def test_config_private_files_ownership_and_operator_paths(private, tmp_path):
    manager, config, _, _, _ = private
    assert WorkerConfig.from_dict(config.as_dict()) == config
    legacy = config.as_dict(); legacy.pop("browser_binary")
    assert WorkerConfig.from_dict(legacy).browser_binary is None
    assert (await manager.create("agent_a")) == config
    with pytest.raises(CoreError, match="Another manager"):
        WorkerManager(manager.settings)
    assert (config.private_root / "ipc.key").stat().st_mode & 0o777 == 0o600
    marker = tmp_path / "private"
    _atomic_private(marker, b"value")
    assert _read_private(marker, 5) == b"value"
    with pytest.raises(CoreError, match="limit"):
        _read_private(marker, 2)
    marker.chmod(0o644)
    with pytest.raises(CoreError, match="permissions"):
        _read_private(marker, 5)
    for changes in ({"scope": "other"}, {"mode": "other"}, {"root": "relative"},
                    {"root": config.workspace}, {"source_root": config.root.parent},
                    {"browser_binary": Path("relative")}, {"private_root": tmp_path / "elsewhere"}):
        with pytest.raises(CoreError): replace(config, **changes)
    with pytest.raises(CoreError): WorkerConfig.from_dict({**config.as_dict(), "env": {}})
    (tmp_path / "alias").symlink_to(config.root)
    with pytest.raises(CoreError): replace(config, root=tmp_path / "alias")
    with pytest.raises(CoreError): WorkerClient(config, b"short")
    with pytest.raises(CoreError): manager.client("unknown")


async def test_authenticated_requests_responses_dedup_and_read_only_receipt_lookup(private):
    _, config, app, server, client = private
    first = await client.request("conversations.create", {"title": "Only once"}, request_id="create")
    again = await client.request("conversations.create", {"title": "Only once"}, request_id="create")
    assert first == again and len(app.journal.list("consumer.conversations", config.scope)) == 1
    with pytest.raises(CoreError) as error:
        await client.request("conversations.create", {"title": "Different"}, request_id="create")
    assert error.value.code == "conflict" and error.value.details["worker_response"]
    digest = hashlib.sha256(canonical_json({"method": "conversations.create", "params": {"title": "Only once"}}).encode()).hexdigest()
    receipt = await client.request("_worker.receipt", {"request_id": "create", "digest": digest})
    assert receipt["response"]["data"] == first
    with pytest.raises(CoreError) as error:
        await client.request("_worker.receipt", {"request_id": "create", "digest": "wrong"})
    assert error.value.code == "conflict"
    with pytest.raises(CoreError) as error:
        await client.request("_worker.receipt", {"request_id": "absent", "digest": digest})
    assert error.value.code == "result_unavailable"
    original = frame(client, request_id="raw")
    reply = await wire(config, original)
    assert reply["signature"] == signature(client._key, {"nonce": original["nonce"], "response": reply["response"]}, response=True)
    assert reply["response"]["data"]["agent_id"] == config.agent_id
    replay = await wire(config, original)
    assert replay["response"]["error"]["code"] == "replayed_request"
    assert all("params" not in row.value for row in server.journal.list("ipc.requests", config.scope))


@pytest.mark.parametrize("changes,code", [
    ({"deadline": time.time() - 1}, "expired_request"),
    ({"deadline": time.time() + 10000}, "expired_request"),
    ({"version": "other"}, "invalid_request"),
    ({"params": []}, "invalid_request"),
    ({"method": "bad/path"}, "invalid_request"),
    ({"request_id": "bad/id"}, "invalid_identifier"),
])
async def test_authenticated_malformed_fields_are_rejected(private, changes, code):
    _, config, _, _, client = private
    reply = await wire(config, frame(client, **changes))
    assert reply["response"]["error"]["code"] == code


async def test_tampering_wrong_keys_unknown_methods_and_fixed_scope(private):
    _, config, _, _, client = private
    tampered = frame(client); tampered["params"] = {"scope": "other"}
    assert (await wire(config, tampered))["response"]["error"]["code"] == "unauthenticated"
    wrong = WorkerClient(config, b"0" * 32)
    with pytest.raises(CoreError) as error: await wrong.request("overview", {})
    assert error.value.code == "worker_protocol"
    for method, params, code in (("unknown.method", {}, "unknown_command"),
        ("overview", {"scope": "other"}, "invalid_request"), ("_worker.missing", {}, "unknown_command"),
        ("_worker.status", {"scope": "other"}, "invalid_request")):
        with pytest.raises(CoreError) as error: await client.request(method, params)
        assert error.value.code == code


async def test_secret_parameters_and_transient_responses_never_enter_receipts(private):
    _, config, _, server, client = private
    secret = "private-provider-value-not-a-fixture-replacement"
    response = await client.request("providers.credentials.put", {"name": "provider", "secret": secret}, request_id="put_secret")
    assert secret not in canonical_json(response)
    for row in server.journal.list("ipc.requests", config.scope):
        assert secret not in canonical_json(row.value)
    with pytest.raises(CoreError) as error:
        await client.request("providers.credentials.put", {"name": "provider", "secret": secret}, request_id="put_secret")
    assert error.value.code == "result_unavailable"
    assert secret.encode() not in (config.private_root / "transport.sqlite").read_bytes()
    device = await client.request("connections.configure", {"id": "desk", "kind": "device", "endpoint": "https://sturgis.example/device",
        "options": {"device_id": "desk", "operations": {"ping": {"type": "object", "additionalProperties": False}}}})
    pair_params = {"connection_id": "desk", "expected_revision": device["revision"]}
    offered = await client.request("devices.pair", pair_params, request_id="pair")
    claim_params = {"connection_id": "desk", "pairing_token": offered["pairing_token"], "device_id": "desk"}
    claimed = await client.request("ingress.device.claim", claim_params, request_id="claim")
    for method, params, request_id in (("devices.pair", pair_params, "pair"), ("ingress.device.claim", claim_params, "claim")):
        with pytest.raises(CoreError) as error: await client.request(method, params, request_id=request_id)
        assert error.value.code == "result_unavailable"
        receipt = server.journal.get("ipc.requests", request_id, config.scope)
        assert receipt.value["response"] is None and receipt.value["state"] == "completed"
    bodies = canonical_json([row.value for row in server.journal.list("ipc.requests", config.scope)])
    for value in (offered["pairing_token"], claimed["secret"]):
        assert value not in bodies
        assert value.encode() not in (config.private_root / "transport.sqlite").read_bytes()


async def test_quiesce_resume_deadlines_and_unfinished_receipt_recovery(private):
    _, config, app, server, client = private
    assert (await client.request("_worker.quiesce", {}))["draining"]
    with pytest.raises(CoreError) as error: await client.request("overview", {})
    assert error.value.code == "worker_draining"
    assert not (await client.request("_worker.resume", {}))["draining"]
    release = asyncio.Event()
    async def delayed(): await release.wait(); return {"done": True}
    # Explicit protocol-unit seam: this does not stand in for the real-process journeys.
    app.bind_command("test.delayed", delayed)
    with pytest.raises(CoreError) as error:
        await client.request("test.delayed", {}, request_id="timed", timeout=.05)
    assert error.value.code == "worker_indeterminate"
    for _ in range(100):
        row = server.journal.get("ipc.requests", "timed", config.scope)
        if row and row.value["state"] == "indeterminate": break
        await asyncio.sleep(.01)
    assert row.value["state"] == "indeterminate"
    assert (await client.request("overview", {}))["accepting"]
    server.journal.put("ipc.requests", "orphan", config.scope,
        {"digest": hashlib.sha256(canonical_json({"method": "overview", "params": {}}).encode()).hexdigest(),
         "method": "overview", "state": "running", "response": None})
    await server.close()
    replacement = await WorkerServer(config, app, client._key).start()
    try:
        with pytest.raises(CoreError) as error: await client.request("overview", {}, request_id="orphan")
        assert error.value.code == "worker_indeterminate"
    finally: await replacement.close()


async def test_client_validation_and_no_dispatch_unavailable(private, tmp_path):
    _, config, _, _, client = private
    for method, params, timeout in (("", {}, 1), (None, {}, 1), ("overview", [], 1),
                                   ("overview", {}, 0), ("overview", {}, float("inf"))):
        with pytest.raises(CoreError): await client.request(method, params, timeout=timeout)
    with pytest.raises(CoreError):
        await client.request("overview", {"large": "x" * 8385000})
    absent = replace(config, root=tmp_path / "absent/state", workspace=tmp_path / "absent/workspace", private_root=tmp_path / "absent/private")
    with pytest.raises(CoreError) as error: await WorkerClient(absent, client._key).request("overview", {})
    assert error.value.code == "worker_unavailable" and error.value.details["dispatched"] is False


async def test_configured_limits_optional_mounts_and_fail_closed_launcher(tmp_path, monkeypatch):
    import shutil
    manager = WorkerManager(Settings(root=tmp_path / "installation", mode="hosted", worker_timeout=.1))
    try:
        config = await manager.create("agent")
        browser = tmp_path / "browser/chrome"; browser.parent.mkdir(); browser.write_text("fixture-path-only")
        custom = replace(config, browser_binary=browser, native_binary=Path("/usr/bin/true"), worker_memory_bytes=274877906944)
        assert WorkerConfig.from_dict(custom.as_dict()) == custom
        command, environment = manager._command(custom)
        assert "--as=274877906944" in command and str(browser.parent) in command
        assert "--unshare-pid" in command and "--clearenv" in command and "--unshare-net" not in command
        for value in (True, 134217727, 274877906945):
            with pytest.raises(CoreError): replace(config, worker_memory_bytes=value)
        monkeypatch.setattr(shutil, "which", lambda _: None)
        with pytest.raises(CoreError) as error: manager._command(config)
        assert error.value.code == "isolation_unavailable"
        # Deliberately failing executable is a launch-fault boundary, not a worker substitute.
        monkeypatch.setattr(manager, "_command", lambda cfg: (["/usr/bin/false"], {"PATH": "/usr/bin:/bin"}))
        with pytest.raises(CoreError): await manager.start("agent")
        assert (await manager.status("agent"))["state"] == "stopped"
        await manager.delete("absent")
    finally: await manager.close()
    await manager.close()
    with pytest.raises(CoreError): await manager.start("agent")
    with pytest.raises(CoreError): await manager.create("agent")
