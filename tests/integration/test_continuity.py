"""Real HTTP, channel sockets, SQLite, native memory and restricted tool journeys."""
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys
import time

import pytest

from agent.channel_adapters import StreamChannelAdapter
from agent.channel_core import InboundMessage
from agent.framing import read_frame, write_frame
from agent.protocol import canonical_json, CoreError, ToolCall
from agent.runtime import CoreRuntime
from agent.tool_runner import WorkspaceFs, file_executor, ProcessRunner, process_executor, ExecutorResult


class WireModel:
    def __init__(self, scripts):
        self.scripts = list(scripts); self.requests = []; self.tasks = set()

    async def handle(self, reader, writer):
        task = asyncio.current_task(); self.tasks.add(task)
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            length = next(int(line.split(b":", 1)[1]) for line in header.split(b"\r\n")
                          if line.lower().startswith(b"content-length:"))
            request = json.loads(await reader.readexactly(length))
            self.requests.append(request)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n")
            await writer.drain()
            script = self.scripts.pop(0) if self.scripts else ["continued"]
            for item in script:
                if isinstance(item, asyncio.Event): await item.wait(); continue
                if isinstance(item, float): await asyncio.sleep(item); continue
                if isinstance(item, str): item = {"choices": [{"delta": {"content": item}}]}
                writer.write(("data:" + canonical_json(item) + "\n\n").encode()); await writer.drain()
            writer.write(b"data:[DONE]\n\n"); await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError): pass
        finally:
            writer.close(); await writer.wait_closed(); self.tasks.discard(task)


def proposal(tool, arguments, *, identity="call-1"):
    return {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": identity,
        "function": {"name": "invoke", "arguments": canonical_json(ToolCall(tool, arguments).as_dict())}}]}}]}


async def eventually(predicate, *, timeout=5):
    async def check():
        while True:
            value = predicate()
            if value: return value
            await asyncio.sleep(.01)
    return await asyncio.wait_for(check(), timeout)


@asynccontextmanager
async def harness(tmp_path, scripts, **options):
    workspace = tmp_path / "workspace"; workspace.mkdir(exist_ok=True)
    model = WireModel(scripts)
    server = await asyncio.start_server(model.handle, "127.0.0.1", 0)
    received = []; channel_tasks = set()
    async def collect(reader, writer):
        task = asyncio.current_task(); channel_tasks.add(task)
        model.channel_peer = writer
        try:
            while (frame := await read_frame(reader)) is not None: received.append(json.loads(frame))
        finally:
            writer.close(); await writer.wait_closed(); channel_tasks.discard(task)
    channel = await asyncio.start_server(collect, "127.0.0.1", 0)
    reader, writer = await asyncio.open_connection("127.0.0.1", channel.sockets[0].getsockname()[1])
    runtime = CoreRuntime(tmp_path / "state", scope="scope", workspace=workspace,
        capabilities=("workspace.write", "process.execute"), memory_interval=.05, **options)
    runtime.catalog.register("scope", "default", endpoint=f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/model")
    runtime.catalog.observe("scope", "default", model="wire-test", context_limit=40000, output_limit=1000)
    adapter = StreamChannelAdapter(reader, writer)
    runtime.adapters.register("wire", adapter)
    binding = runtime.channels.bind("scope", "wire", "account", "conversation", "user")
    try: yield runtime, binding.id, model, received
    finally:
        await runtime.close(); await adapter.close()
        server.close(); channel.close()
        peers = list(model.tasks | channel_tasks)
        for task in peers: task.cancel()
        await asyncio.gather(*peers, return_exceptions=True)
        await server.wait_closed(); await channel.wait_closed()


async def send(runtime, binding, text="hello", *, kind="prompt", delivery_id="input-1"):
    event = await runtime.accept(InboundMessage("scope", binding, "user", delivery_id, text, kind))
    work = runtime.journal.get("runtime_inputs", event.id, "scope").value["work_id"]
    return event, work


async def test_real_world_effect_fixed_context_native_memory_and_wire_delivery(tmp_path):
    scripts = [["I will write the file.", proposal("file.write", {"operation": "write", "path": "result.txt", "content": "actual"}),
                {"usage": {"total_tokens": 37}, "choices": []}], ["The recorded write succeeded."]]
    async with harness(tmp_path, scripts) as (r, binding, model, received):
        fs = WorkspaceFs(tmp_path / "workspace")
        r.tools.register("file.write", file_executor(fs), input_schema={"type": "object"},
            output_schema={"type": "object"}, required_capabilities=["workspace.write"])
        goal = r.goals.create("scope", "verify independently", criteria=[{"id": "check", "event_kind": "test.verified", "source": "test", "matches": {"ok": True}}])
        await r.start(); event, work = await send(r, binding, "write result.txt")
        result = await r.wait(work, timeout=10)
        assert result.value["state"] == "completed", result.value
        await eventually(lambda: received and received[-1]["terminal"] == "completed")
        assert (tmp_path / "workspace/result.txt").read_text() == "actual"
        assert r.goals.get("scope", goal.id).value["state"] == "proposed"
        assert len(model.requests) == 2 and model.requests[0]["tools"][0]["function"]["name"] == "invoke"
        second = model.requests[1]["messages"]
        assert any(m["role"] == "tool" and json.loads(json.loads(m["content"])["text"])["outcome"] == "success" for m in second)
        assert any(m["role"] == "user" and "write result.txt" in m["content"] for m in second)
        disposition = r.journal.get("input_dispositions", event.id, "scope")
        assert disposition.value["state"] == "incorporated"
        packet = r.journal.get("context_packets", disposition.value["context_id"], "scope").value
        assert packet["messages"] == second
        assert all(row.value["state"] == "sent_unconfirmed" for row in r.journal.list("outbox", "scope"))
        await eventually(lambda: r.journal.cursor("scope", "memory/native-v1") >= event.seq)
        memory = await r.memory.query("scope", text="result.txt")
        assert memory["status"] == "ok" and memory["data"]["records"]
        assert (await r.accept(InboundMessage("scope", binding, "user", "input-1", "write result.txt"))).id == event.id
        assert len(r.journal.list("runtime_turns", "scope")) == 1


async def test_steering_during_real_process_preserves_partial_output_and_revokes_effects(tmp_path):
    code = "import time; time.sleep(2); open('/workspace/late','w').write('unexpected')"
    scripts = [["Starting a slow operation.", proposal("process", {"argv": [sys.executable, "-c", code]})],
               ["I incorporated the new direction."]]
    async with harness(tmp_path, scripts) as (r, binding, model, received):
        r.tools.register("process", process_executor(ProcessRunner(tmp_path / "workspace", [sys.executable])),
            input_schema={"type": "object"}, output_schema={"type": "object"},
            required_capabilities=["process.execute"], timeout=10)
        await r.start(); _, first = await send(r, binding)
        await eventually(lambda: any(x["content"].get("text") == "Starting a slow operation." for x in received))
        await eventually(lambda: any(x.value["result"]["state"] == "running" for x in r.journal.list("tool_requests", "scope")))
        old_context = canonical_json(model.requests[0]["messages"])
        event, second = await asyncio.wait_for(send(r, binding, "change direction", kind="steer", delivery_id="steer-1"), .5)
        assert r.journal.get("runtime_inputs", event.id, "scope")
        assert (await r.wait(first, timeout=5)).value["state"] == "interrupted"
        assert (await r.wait(second, timeout=5)).value["state"] == "completed"
        assert canonical_json(model.requests[0]["messages"]) == old_context
        assert "change direction" in canonical_json(model.requests[1]["messages"])
        assert not (tmp_path / "workspace/late").exists()
        assert r.scheduler.get("scope", first).value["state"] == "cancelled"


async def test_pending_input_and_visible_text_remain_responsive_during_slow_provider(tmp_path):
    release = asyncio.Event()
    async with harness(tmp_path, [["partial", release], ["second response"]]) as (r, binding, model, received):
        await r.start(); _, first = await send(r, binding)
        await eventually(lambda: received)
        event, second = await asyncio.wait_for(send(r, binding, "next", delivery_id="input-2"), .5)
        assert r.journal.get("runtime_turns", second, "scope").value["state"] == "queued"
        assert len(model.requests) == 1
        release.set()
        assert (await r.wait(first, timeout=5)).value["state"] == "completed"
        assert (await r.wait(second, timeout=5)).value["state"] == "completed"
        assert r.journal.get("input_dispositions", event.id, "scope").value["state"] == "incorporated"
        assert "partial" in canonical_json(model.requests[1]["messages"])


async def test_restart_replays_durable_unrouted_input_and_existing_outbox(tmp_path):
    async with harness(tmp_path, [["after restart"]]) as (r, binding, model, received):
        event = r.channels.admit(InboundMessage("scope", binding, "user", "durable", "survive crash"))
        r.delivery.enqueue("scope", binding, "older-stream", 0, {"type": "text", "text": "already durable"})
        # Equivalent to the persisted crash point before start; no in-memory queue is required.
        await r.start()
        await eventually(lambda: r.journal.get("runtime_inputs", event.id, "scope"))
        work = r.journal.get("runtime_inputs", event.id, "scope").value["work_id"]
        assert (await r.wait(work, timeout=5)).value["state"] == "completed"
        await eventually(lambda: any(x["content"].get("text") == "already durable" for x in received))
        assert len(model.requests) == 1


async def test_memory_process_missing_does_not_block_foreground(tmp_path):
    async with harness(tmp_path, [["still responsive"]], memory_binary="/missing/sturgis-memory") as (r, binding, model, received):
        await r.start(); _, work = await send(r, binding)
        assert (await r.wait(work, timeout=5)).value["state"] == "completed"
        await eventually(lambda: received)
        packet = r.journal.list("context_packets", "scope")[0].value
        status = next(json.loads(m["content"])["text"] for m in packet["messages"] if json.loads(m["content"])["kind"] == "runtime_status")
        assert json.loads(status)["status"] == "unavailable"


async def test_acp_prompts_use_the_same_runtime_and_output_path(tmp_path):
    async with harness(tmp_path, [["ACP response"]]) as (r, _, model, received):
        updates = []
        async def emit(value): updates.append(value)
        await r.start(); acp = r.acp(emit=emit, connection_id="client")
        initial = await acp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": 1}})
        assert initial["result"]["protocolVersion"] == 1
        session = await acp.handle({"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {"cwd": str(tmp_path / "workspace"), "mcpServers": []}})
        session_id = session["result"]["sessionId"]
        result = await acp.handle({"jsonrpc": "2.0", "id": 3, "method": "session/prompt", "params": {"sessionId": session_id, "prompt": [{"type": "text", "text": "hello ACP"}]}})
        assert result["result"]["stopReason"] == "end_turn"
        await eventually(lambda: updates)
        assert any(update["params"]["update"].get("content", {}).get("text") == "ACP response" for update in updates)
        assert "hello ACP" in canonical_json(model.requests[0]["messages"])


async def test_real_channel_ingress_is_bound_and_can_be_reattached_after_invalid_input(tmp_path):
    async with harness(tmp_path, [["from wire"], ["reattached"]]) as (r, binding, model, received):
        r.attach_ingress(binding); r.attach_ingress(binding)
        await r.start()
        await write_frame(model.channel_peer, canonical_json({"delivery_id": "wire-1", "text": "wire input"}).encode())
        await eventually(lambda: received and received[-1]["terminal"] == "completed")
        assert "wire input" in canonical_json(model.requests[0]["messages"])
        await write_frame(model.channel_peer, canonical_json({"scope": "forged", "text": "bad"}).encode())
        await eventually(lambda: r.journal.events("scope", kinds=["runtime.ingress_failed"]))
        assert binding not in r._ingress_bindings
        r.attach_ingress(binding)
        await write_frame(model.channel_peer, canonical_json({"delivery_id": "wire-2", "text": "new wire input"}).encode())
        await eventually(lambda: len(model.requests) == 2)
        await eventually(lambda: len([x for x in received if x["terminal"] == "completed"]) == 2)


async def test_real_model_child_uses_its_workspace_and_evidence_admission(tmp_path):
    scripts = [[proposal("file.write", {"operation": "write", "path": "child.txt", "content": "child effect"})],
               ["finished"], ["I claim success without evidence"]]
    async with harness(tmp_path, scripts) as (r, binding, model, received):
        r.tools.register("file.write", file_executor(WorkspaceFs(tmp_path / "workspace")),
            input_schema={"type": "object"}, output_schema={"type": "object"}, required_capabilities=["workspace.write"])
        parent = r.scheduler.submit("scope", "controller", {}, capabilities=["workspace.write"],
            resources={"tool_calls": 20, "tokens": 500000, "elapsed_ms": 1000000, "iterations": 20})
        r.scheduler.admit("scope", parent.id)
        await r.start()
        criteria = [{"id": "effect", "event_kind": "tool.effect", "source": "tool_runner", "matches": {"data.path": "child.txt"}}]
        child = await r.delegate(parent.id, "write your own child.txt", capabilities=["workspace.write"],
            resources={"tool_calls": 4, "tokens": 100000, "elapsed_ms": 100000, "iterations": 4}, criteria=criteria)
        turn = await r.wait(child.value["work_id"], timeout=5)
        assert turn.value["state"] == "completed", turn.value
        assert r.subagents.get("scope", child.id).value["result"]["verdict"] == "accepted"
        assert (Path(child.value["workspace_root"]) / "child.txt").read_text() == "child effect"
        assert not (tmp_path / "workspace/child.txt").exists()
        assert not received
        next_child = await r.delegate(parent.id, "report with proof", capabilities=[],
            resources={"tokens": 100000, "elapsed_ms": 100000, "iterations": 2}, criteria=criteria)
        failure = await r.wait(next_child.value["work_id"], timeout=5)
        assert failure.value["error"]["code"] == "child_evidence_incomplete"
        assert r.subagents.get("scope", next_child.id).value["result"]["verdict"] == "rejected"


async def test_large_tool_body_is_durable_and_explicitly_deferred_from_context(tmp_path):
    async with harness(tmp_path, [[proposal("large", {})], ["The large result is stored."]]) as (r, binding, model, received):
        async def large(args, context): return ExecutorResult({"content": "世界" * 10000}, effects="none")
        r.tools.register("large", large, input_schema={"type": "object"}, output_schema={"type": "object"}, read_only=True)
        await r.start(); _, work = await send(r, binding)
        assert (await r.wait(work, timeout=5)).value["state"] == "completed"
        observation = next(json.loads(json.loads(m["content"])["text"]) for m in model.requests[1]["messages"] if m["role"] == "tool")
        assert observation["completeness"]["truncated"] and not observation["completeness"]["observation_complete"]
        reference = observation["data"]["reference"]
        body = json.loads(r.artifacts.get("scope", reference["id"]))
        assert body["data"]["content"] == "世界" * 10000
        assert body["outcome"] == observation["outcome"] == "success"
        assert len(canonical_json(model.requests[1]["messages"]).encode()) < 40000


async def test_due_schedule_runs_through_common_runtime_once(tmp_path):
    async with harness(tmp_path, [["scheduled response"]]) as (r, binding, model, received):
        resources = {"tokens": 100000, "elapsed_ms": 100000, "iterations": 2}
        with pytest.raises(CoreError): r.schedule_prompt(binding, "", {"kind": "once", "at": time.time()}, resources=resources)
        schedule = r.schedule_prompt(binding, "scheduled inspection", {"kind": "once", "at": time.time() - 1}, resources=resources)
        await r.start()
        await eventually(lambda: received and received[-1]["terminal"] == "completed")
        assert len(model.requests) == 1 and "scheduled inspection" in canonical_json(model.requests[0]["messages"])
        assert "scheduled_request" in canonical_json(model.requests[0]["messages"])
        r._tick(); r._tick()
        assert r.journal.get("schedules", schedule.id, "scope").value["occurrences"] == 1
        assert len(r.journal.list("runtime_turns", "scope")) == 1


@pytest.mark.parametrize("reason,code,stop", [("length", "provider_max_tokens", "max_tokens"), ("content_filter", "provider_refusal", "refusal")])
async def test_provider_limits_preserve_partial_text_and_do_not_execute_proposals(tmp_path, reason, code, stop):
    script = ["partial answer", proposal("file.write", {"operation": "write", "path": "must-not-exist", "content": "bad"}),
              {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}]
    async with harness(tmp_path, [script]) as (r, binding, model, received):
        r.tools.register("file.write", file_executor(WorkspaceFs(tmp_path / "workspace")),
            input_schema={"type": "object"}, output_schema={"type": "object"})
        await r.start(); event, work = await send(r, binding)
        result = await r.wait(work, timeout=5)
        assert result.value["state"] == "failed" and result.value["error"]["code"] == code
        assert await r.submit_event(event) == {"stopReason": stop}
        await r.wait_output(work)
        assert any(x["content"].get("text") == "partial answer" for x in received)
        assert not r.journal.list("tool_requests", "scope") and not (tmp_path / "workspace/must-not-exist").exists()


async def test_unreported_provider_tokens_remain_reserved_until_receipt(tmp_path):
    async with harness(tmp_path, [["response without usage"]]) as (r, binding, model, received):
        await r.start(); _, work = await send(r, binding)
        assert (await r.wait(work, timeout=5)).value["state"] == "completed"
        packet = r.journal.list("context_packets", "scope")[0]
        claim = r.journal.get("work_claims", packet.id + ":model", "scope")
        assert claim.value["state"] == "claimed" and claim.value["resources"]["tokens"] > 0
        assert r.scheduler.get("scope", work).value["usage"]["iterations"] == 1
        with pytest.raises(CoreError): r.record_provider_usage("missing", 1)
        settled = r.record_provider_usage(packet.id, 25)
        assert settled.value["usage"]["tokens"] == 25
        assert r.journal.get("work_claims", packet.id + ":model", "scope").value["state"] == "settled"
        assert r.record_provider_usage(packet.id, 25).value["usage"]["tokens"] == 25


async def test_harness_assertion_failure_closes_blocked_provider_before_waiting_for_servers(tmp_path):
    release = asyncio.Event()
    peers = []
    model = None

    async def fail_after_partial_output():
        nonlocal model
        async with harness(tmp_path, [["cleanup-visible-partial", release]]) as (runtime, binding, model, received):
            await runtime.start()
            await send(runtime, binding)
            await eventually(lambda: any(item["content"].get("text") == "cleanup-visible-partial" for item in received))
            peers.extend(model.tasks)
            assert peers and not release.is_set()
            raise AssertionError("induced failure while the real provider is blocked")

    try:
        with pytest.raises(AssertionError, match="induced failure while the real provider is blocked"):
            await asyncio.wait_for(fail_after_partial_output(), 5)
        assert not release.is_set(), "Fixture cleanup must not depend on releasing the model script"
        assert model is not None and not model.tasks
        assert all(task.done() for task in peers)
    finally:
        # Bound the negative regression too: the old teardown can time out before
        # reaching its own peer cancellation, which must not hang pytest itself.
        remaining = list(model.tasks) if model is not None else []
        for task in remaining: task.cancel()
        await asyncio.gather(*remaining, return_exceptions=True)
