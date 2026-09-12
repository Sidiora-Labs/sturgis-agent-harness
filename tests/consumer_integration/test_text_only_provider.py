"""Observed text-only providers remain usable without acquiring tool authority."""
import asyncio
import base64
from contextlib import asynccontextmanager

from agent.tool_runner import WorkspaceFs, file_executor
from consumer.application import AgentApplication
from tests.consumer.test_application import configuration
from tests.integration.test_continuity import WireModel, eventually, harness, proposal, send


@asynccontextmanager
async def text_application(tmp_path, scripts):
    model = WireModel([["OK"], *scripts])
    server = await asyncio.start_server(model.handle, "127.0.0.1", 0)
    origin = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    app = AgentApplication(configuration(tmp_path, origins=(origin,)))
    try:
        await app.start()
        # Omission is the real consumer default, including the initial UI form.
        configured = await app.command("providers.configure", {
            "provider_id": "default", "endpoint": origin + "/model", "model": "text-only",
            "context_limit": 60000, "output_limit": 512})
        assert configured["tools"] is False
        probe = await app.command("providers.probe", {
            "provider_id": "default", "expected_revision": configured["revision"]})
        assert probe["ready"], probe
        await app.command("providers.activate", {
            "provider_id": "default", "expected_revision": configured["revision"]})
        yield app, model
    finally:
        await app.close()
        server.close()
        for task in list(model.tasks):
            task.cancel()
        await asyncio.gather(*model.tasks, return_exceptions=True)
        await server.wait_closed()


async def test_default_text_provider_streams_before_completion_without_advertised_tools(tmp_path):
    release = asyncio.Event()
    async with text_application(tmp_path, [["First visible text", release, " and complete.",
            {"choices": [], "usage": {"total_tokens": 17}}]]) as (app, model):
        assert app.core.tools.descriptors()  # Consumer read/write tools exist.
        room = await app.command("conversations.create", {})
        receipt = await app.command("messages.send", {
            "conversation_id": room["id"], "message_id": "text-only", "text": "Hello"})
        await eventually(lambda: any(row.value["payload"]["content"].get("text") == "First visible text"
            for row in app.journal.list("outbox", app.scope)))
        assert not release.is_set()
        assert app.journal.get("runtime_turns", receipt["work_id"], app.scope).value["state"] == "running"
        assert len(model.requests) == 2
        assert all("tools" not in request for request in model.requests)
        instructions = next(message["content"] for message in model.requests[-1]["messages"]
            if message["role"] == "system")
        assert "text-only" in instructions and "workspace.write" not in instructions
        packet = app.journal.list("context_packets", app.scope)[0].value
        assert packet["metadata"]["protocol_overhead"] == 258  # Empty tool schema plus fixed envelope.
        release.set()
        turn = await app.core.wait(receipt["work_id"], timeout=5)
        assert turn.value["state"] == "completed", turn.value
        await app.core.wait_output(receipt["work_id"], timeout=5)
        outputs = [row.value["payload"] for row in app.journal.list("outbox", app.scope)]
        assert any(item["terminal"] == "completed" for item in outputs)
        assert (await app.command("usage.summary", {}))["actual"]["tokens"] == 17


async def test_text_provider_unsolicited_tool_is_rejected_before_any_file_effect(tmp_path):
    attack = proposal("workspace.write", {"path": "unsolicited.txt", "data_base64":
        base64.b64encode(b"must not execute").decode()})
    async with text_application(tmp_path, [["Visible prelude", attack]]) as (app, model):
        await app.command("permissions.set", {"tool": "workspace.write", "mode": "allow"})
        assert "workspace.write" in app.core.capabilities
        room = await app.command("conversations.create", {})
        receipt = await app.command("messages.send", {
            "conversation_id": room["id"], "message_id": "unexpected-tool", "text": "Just say hello"})
        turn = await app.core.wait(receipt["work_id"], timeout=5)
        assert turn.value["state"] == "failed"
        assert turn.value["error"]["code"] == "provider_protocol"
        assert not app.journal.list("tool_requests", app.scope)
        assert not (app.config.workspace / "unsolicited.txt").exists()
        assert not (await app.command("files.list", {}))["entries"]
        assert any(row.value["payload"]["content"].get("text") == "Visible prelude"
            for row in app.journal.list("outbox", app.scope))
        assert len(model.requests) == 2 and "tools" not in model.requests[-1]


async def test_programmatic_core_default_still_requires_registered_tool_support(tmp_path):
    async with harness(tmp_path, [["Should not be requested"]]) as (core, binding, model, _):
        core.tools.register("file.write", file_executor(WorkspaceFs(tmp_path / "workspace")),
            input_schema={"type": "object"}, output_schema={"type": "object"},
            required_capabilities=["workspace.write"])
        core.catalog.observe(core.scope, "default", model="text-only", context_limit=40000,
            output_limit=1000, tools=False)
        await core.start()
        _, work_id = await send(core, binding)
        turn = await core.wait(work_id, timeout=5)
        assert turn.value["state"] == "failed" and turn.value["error"]["code"] == "incompatible"
        assert not model.requests
