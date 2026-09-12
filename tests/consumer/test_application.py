"""Application commands exercise actual service composition and durable core records."""
import asyncio
import base64
from pathlib import Path

import pytest

from agent.protocol import CoreError, ToolCall, canonical_json
from agent.tool_runner import ExecutionContext
from consumer.application import AgentApplication, _bytes
from consumer.workers import WorkerConfig

ROOT = Path(__file__).resolve().parents[2]


def configuration(tmp_path, *, origins=()):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return WorkerConfig(agent_id="test_agent", scope="test_agent", root=tmp_path / "runtime",
        workspace=workspace, private_root=tmp_path / "private", mode="self_hosted",
        native_binary=ROOT / ".build/native/sturgis-memory", source_root=ROOT,
        private_network_origins=origins)


@pytest.fixture
async def app(tmp_path):
    app = AgentApplication(configuration(tmp_path))
    await app.start()
    yield app
    await app.close()


async def test_strict_commands_and_conversation_cas(app):
    with pytest.raises(CoreError, match="Unsupported"):
        await app.command("__dict__", {})
    with pytest.raises(CoreError):
        await app.command("conversations.create", {"scope": "other"})
    with pytest.raises(CoreError):
        await app.command("messages.send", {})
    with pytest.raises(CoreError):
        app.bind_command("overview", app.overview)
    room = await app.command("conversations.create", {"title": "Daily work"})
    assert (await app.command("conversations.list", {}))["items"] == [room]
    assert not (await app.command("conversations.list", {"query": "absent"}))["items"]
    room = await app.command("conversations.update", {"id": room["id"], "title": "Renamed", "expected_revision": 1})
    with pytest.raises(CoreError):
        await app.command("conversations.update", {"id": room["id"], "title": "Stale", "expected_revision": 1})
    for params in ({"id": room["id"], "expected_revision": 2}, {"id": room["id"], "expected_revision": 2, "archived": 1}):
        with pytest.raises(CoreError):
            await app.command("conversations.update", params)
    await app.command("conversations.update", {"id": room["id"], "archived": True, "expected_revision": 2})
    assert not (await app.command("conversations.list", {}))["items"]
    assert (await app.command("conversations.list", {"include_archived": True}))["items"][0]["archived"]
    with pytest.raises(CoreError):
        await app.command("messages.send", {"conversation_id": room["id"], "message_id": "archived", "text": "hello"})
    with pytest.raises(CoreError):
        await app.command("conversations.list", {"include_archived": "yes"})
    with pytest.raises(CoreError):
        await app.command("conversations.get", {"id": "missing"})
    with pytest.raises(CoreError):
        await app.command("conversations.create", {"title": ""})
    assert (await app.command("overview", {}))["agent_id"] == "test_agent"


async def test_scoped_content_credentials_and_skill_procedure(app):
    data = base64.b64encode(b"real file").decode()
    file = await app.command("files.write", {"path": "notes.txt", "data_base64": data})
    assert base64.b64decode((await app.command("files.read", {"path": "notes.txt"}))["data_base64"]) == b"real file"
    with pytest.raises(CoreError):
        await app.command("files.write", {"path": "notes.txt", "data_base64": data})
    await app.command("files.delete", {"path": "notes.txt", "expected_sha256": file["after_sha256"]})
    for encoded in (1, "$invalid", "a" * 6_000_001):
        with pytest.raises(CoreError):
            _bytes(encoded)
    secret = await app.command("providers.credentials.put", {"name": "model", "secret": "private-secret"})
    assert (await app.command("providers.credentials.list", {}))[0]["reference"] == secret["reference"]
    assert "private-secret" not in canonical_json([e.payload for e in app.journal.events(app.scope, limit=1000)])
    await app.command("providers.credentials.revoke", {"name": "model", "expected_revision": secret["revision"]})
    manifest = {"id": "research", "version": "1", "description": "Use sourced notes", "triggers": ["research"], "steps": ["Read the supplied sources"], "required_tools": ["content.read"]}
    installed = await app.command("skills.install", {"manifest": manifest})
    assert (await app.command("skills.list", {}))[0]["manifest"] == manifest
    changed = await app.command("skills.enable", {"id": "research", "enabled": False, "expected_revision": installed["revision"]})
    with pytest.raises(CoreError):
        await app.command("skills.enable", {"id": "research", "enabled": True, "expected_revision": 1})
    with pytest.raises(CoreError):
        await app.command("skills.enable", {"id": "missing", "enabled": True, "expected_revision": 1})
    rolled = await app.command("skills.rollback", {"id": "research", "expected_revision": changed["revision"]})
    assert rolled["state"] == "withdrawn"
    assert isinstance(await app.command("usage.summary", {}), dict)


async def test_accepted_input_replay_cancel_and_safe_events_without_provider(app):
    room = await app.command("conversations.create", {})
    payload = {"conversation_id": room["id"], "message_id": "stable", "text": "remember this unique input"}
    receipt = await app.command("messages.send", payload, "request_1")
    assert receipt["status"] == "admitted"
    assert await app.command("messages.send", payload) == receipt
    with pytest.raises(CoreError, match="reused"):
        await app.command("messages.send", {**payload, "text": "different"})
    await app.core.wait(receipt["work_id"], timeout=5)
    turn = (await app.command("conversations.get", {"id": room["id"]}))["turns"][0]
    assert turn["state"] == "failed"  # Missing provider is explicit, not a fake assistant reply.
    assert (await app.command("conversations.list", {"query": "unique"}))["items"][0]["id"] == room["id"]
    cancelled = await app.command("messages.cancel", {"conversation_id": room["id"], "message_id": "cancel"})
    assert cancelled["work_id"] is None
    events = await app.command("messages.events", {"conversation_id": room["id"], "limit": 1000})
    assert any(e["kind"] == "user.prompt" for e in events["items"])
    assert any(e["kind"] == "output.persisted" for e in events["items"])
    assert not (await app.command("messages.events", {"after": events["cursor"]}))["items"]
    other = await app.command("conversations.create", {"title": "Other"})
    assert not (await app.command("messages.events", {"conversation_id": other["id"], "limit": 1000}))["items"]
    for values in ({"after": -1}, {"limit": 0}, {"limit": True}):
        with pytest.raises(CoreError):
            await app.command("messages.events", values)
    for change in ({"kind": "system"}, {"text": ""}, {"attachments": ["missing"]}):
        with pytest.raises(CoreError):
            await app.command("messages.send", {**payload, "message_id": "bad", **change})


async def test_lifecycle_fence_prevents_model_and_timer_admission(app):
    room = await app.command("conversations.create", {})
    await app.prepare_suspend()
    with pytest.raises(CoreError, match="paused"):
        await app.command("overview", {})
    due = app.core.schedule_prompt(room["binding_id"], "later", {"kind": "once", "at": 2_000_000_000}, resources={"tokens": 1000})
    snapshot = await app.lifecycle_snapshot()
    assert snapshot["next_wake_at"] == due.value["next_due"]
    assert app.core._admission_paused
    await app.resume_admission()
    assert (await app.command("overview", {}))["accepting"]


async def test_registered_content_tools_use_scheduler_and_exact_file_effects(app):
    upload = await app.command("attachments.upload", {"name": "source.txt", "media_type": "text/plain", "data_base64": base64.b64encode(b"source text").decode(), "operation_id": "up"})
    doc = await app.command("knowledge.add", {"attachment_id": upload["id"], "title": "Source", "operation_id": "knowledge"})
    await app.command("permissions.set", {"tool": "workspace.write", "mode": "allow"})
    work = app.core.scheduler.submit(app.scope, "test_tool", {}, capabilities=app.core.capabilities, resources={"tool_calls": 20, "elapsed_ms": 600000})
    work = app.core.scheduler.admit(app.scope, work.id, eligible_ids=[work.id])
    async def invoke(name, arguments):
        return await app.core.tools.invoke(ToolCall(name, arguments), scope=app.scope, work_id=work.id, generation=work.value["generation"])
    read = await invoke("content.read", {"kind": "attachment", "id": upload["id"]})
    assert read.outcome == "success"
    assert base64.b64decode(read.data["data_base64"]) == b"source text"
    read = await invoke("content.read", {"kind": "knowledge", "id": doc["document"]["id"], "limit": 3})
    assert base64.b64decode(read.data["data_base64"]) == b"sou"
    bad = await invoke("content.read", {"kind": "knowledge", "id": doc["document"]["id"], "offset": 100})
    assert bad.outcome == "execution_error"
    written = await invoke("workspace.write", {"operation": "write", "path": "result.txt", "data_base64": base64.b64encode(b"effect").decode()})
    assert written.effects["certainty"] == "observed" and written.effects["evidence_event_ids"]
    assert (app.config.workspace / "result.txt").read_bytes() == b"effect"
    assert (await invoke("workspace.read", {"operation": "read", "path": "result.txt"})).outcome == "success"
    assert (await invoke("workspace.read", {"operation": "list", "path": "."})).outcome == "success"
    assert (await invoke("workspace.read", {"operation": "read", "path": ".sturgis/private"})).outcome == "execution_error"
    assert (await invoke("workspace.write", {"operation": "delete", "path": "result.txt", "expected_sha256": written.data["after_sha256"]})).outcome == "success"
    context = ExecutionContext(app.scope, work.id, work.value["generation"], "a", "t", asyncio.Event(), {"delegated": True})
    for name in ("content.read", "workspace.read", "workspace.write"):
        executor = app.core.tools.registry[name, "1"][1]
        with pytest.raises(CoreError, match="delegated"):
            await executor({}, context)
    app.core.scheduler.finish(app.scope, work.id, work.value["generation"])


async def test_connection_configuration_is_versioned_through_application(app):
    credential = await app.command("providers.credentials.put", {"name": "hook", "secret": "signed-hook-secret"})
    configured = await app.command("connections.configure", {"id": "hook", "kind": "webhook", "endpoint": "https://hooks.example/receive", "credential_ref": credential["reference"]})
    assert (await app.command("connections.get", {"id": "hook"}))["status"] == "ungranted"
    granted = await app.command("connections.grant", {"id": "hook", "operations": ["receive"], "expected_revision": configured["revision"]})
    ready = await app.command("connections.probe", {"id": "hook", "expected_revision": granted["revision"]})
    assert ready["status"] == "ready"
    assert len(await app.command("connections.list", {})) == 1
    revoked = await app.command("connections.revoke", {"id": "hook", "expected_revision": ready["revision"]})
    assert revoked["status"] == "revoked"
    assert set(app.core.capabilities) == {"content.read", "workspace.read", "workspace.write"}
