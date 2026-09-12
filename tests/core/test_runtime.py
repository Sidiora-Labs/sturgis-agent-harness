import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys

import pytest

from agent.channel_core import InboundMessage
from agent.protocol import CoreError, ToolCall, canonical_json
from agent.provider_adapters import ProviderEvent
from agent.runtime import CoreRuntime
from agent.tool_runner import WorkspaceFs, file_executor, ProcessRunner, process_executor, ExecutorResult
from agent.meta_harness import CheckSpec


class ControlledProvider:
    """A provider boundary probe; actual HTTP and native journeys live in integration."""
    def __init__(self, events=None):
        self.events = events or [ProviderEvent("text", {"text": "visible"}), ProviderEvent("terminal", {"status": "completed"})]
        self.requests = []

    async def stream(self, descriptor, messages, **options):
        self.requests.append(messages)
        for event in self.events:
            if isinstance(event, Exception): raise event
            yield event


class Capture:
    def __init__(self): self.items = []
    async def send(self, item): self.items.append(item); return {"status": "sent_unconfirmed"}


def runtime(tmp_path, provider=None, **options):
    workspace = tmp_path / "workspace"; workspace.mkdir(parents=True, exist_ok=True)
    r = CoreRuntime(tmp_path / "root", scope="s", workspace=workspace,
        provider=provider or ControlledProvider(), memory_binary="/missing/native", memory_interval=.05, **options)
    if r.journal.get("providers", "default", "s") is None:
        r.catalog.register("s", "default", endpoint="http://127.0.0.1:1/model")
    r.catalog.observe("s", "default", model="probe", context_limit=20000, output_limit=1000)
    r.adapters.register("capture", Capture())
    binding = r.channels.bind("s", "capture", "a", "c", "u")
    return r, binding.id


async def accept(r, binding, text="hi", kind="prompt", identity="one"):
    event = await r.accept(InboundMessage("s", binding, "u", identity, text, kind))
    return event, r.journal.get("runtime_inputs", event.id, "s").value["work_id"]


async def test_lifecycle_identity_lock_and_invalid_input(tmp_path):
    r, b = runtime(tmp_path)
    try:
        with pytest.raises(CoreError, match="Start"): await accept(r, b)
        with pytest.raises(CoreError, match="no input"): r.attach_ingress(b)
        with pytest.raises(CoreError, match="owns"): runtime(tmp_path)
        async with r:
            assert await r.start() is r
            with pytest.raises(CoreError, match="another"): await r.accept(InboundMessage("other", b, "u", "d", "hi"))
            event, work = await accept(r, b)
            with pytest.raises(CoreError, match="durably"): await r.submit_event(replace(event, payload={"text": "forged"}))
            with pytest.raises(CoreError, match="only admitted"): await r.submit_event(replace(event, kind="memory.generated"))
            assert (await r.wait(work, timeout=3)).value["state"] == "completed"
            with pytest.raises(CoreError, match="Unknown"): await r.wait("missing")
            with r.journal.transaction():
                prior = r.journal.get("runtime_turns", work, "s")
                assert r._finish(work, "failed") == prior
                r._emit(work, {"text": "too late"})
        await r.close()
        with pytest.raises(CoreError, match="closed"): await r.start()
        with pytest.raises(CoreError, match="closed"): r.attach_ingress(b)
        with pytest.raises(CoreError, match="Start"): await r.submit_event(event)
    finally: await r.close()


@pytest.mark.parametrize("events,code", [
    ([ProviderEvent("unexpected", {})], "provider_protocol"),
    ([ProviderEvent("text", {"text": "partial"})], "provider_incomplete"),
    ([ProviderEvent("usage", {"total_tokens": -1})], "provider_usage"),
    ([RuntimeError("do not expose secrets")], "internal_error"),
    ([CoreError("provider_unavailable", "offline")], "provider_unavailable"),
    ([ProviderEvent("tool", {"id": None, "call": ToolCall("x", {}).as_dict()}), ProviderEvent("terminal", {})], "provider_protocol"),
])
async def test_failed_inference_keeps_visible_output_and_truthful_terminal(tmp_path, events, code):
    r, b = runtime(tmp_path, ControlledProvider(events))
    try:
        await r.start(); event, work = await accept(r, b)
        result = await r.wait(work, timeout=3)
        assert result.value["state"] == "failed" and result.value["error"]["code"] == code
        with pytest.raises(CoreError, match="Model work failed"): await r.submit_event(event)
        outbox = r.journal.list("outbox", "s")
        assert any(row.value["payload"]["terminal"] == "failed" for row in outbox)
        assert "do not expose secrets" not in canonical_json([row.value for row in outbox])
    finally: await r.close()


async def test_cancellation_fences_queued_work_and_waiting(tmp_path):
    r, b = runtime(tmp_path)
    try:
        # Construct the atomic persisted queue before admission gets a chance to run.
        event = r.channels.admit(InboundMessage("s", b, "u", "one", "hi"))
        row = r._route(event); work = row.value["work_id"]
        cancellation = r.channels.admit(InboundMessage("s", b, "u", "cancel", "", "cancel"))
        r._route(cancellation)
        await r.start()
        assert (await r.wait(work, timeout=3)).value["state"] == "interrupted"
        assert r.journal.get("input_dispositions", cancellation.id, "s").value["state"] == "cancelled"
        assert not r.provider.requests
    finally: await r.close()


async def test_large_input_has_exact_deferred_ranges_and_budget_failure(tmp_path):
    r, b = runtime(tmp_path, max_model_calls=1)
    try:
        r.catalog.observe("s", "default", model="probe", context_limit=5000, output_limit=1000)
        await r.start(); event, work = await accept(r, b, "界" * 10000)
        result = await r.wait(work, timeout=3)
        assert result.value["error"]["code"] == "iteration_budget"
        packet = r.journal.list("context_packets", "s")[0].value
        item = next(item for item in packet["manifest"] if item["source_id"] == event.id)
        assert item["representation"] == "excerpt" and item["pending_ranges"] == [[item["end"], 30000]]
        assert item["end"] % 3 == 0
        assert r.journal.get("input_dispositions", event.id, "s") is None
        assert packet["metadata"]["input_tokens"] + packet["metadata"]["reserved_output"] + packet["metadata"]["protocol_overhead"] <= 5000
    finally: await r.close()


async def test_recovery_interrupts_prior_running_work_without_repeating_effect(tmp_path):
    r, b = runtime(tmp_path)
    event = r.channels.admit(InboundMessage("s", b, "u", "one", "recover"))
    work = r._route(event).value["work_id"]
    admitted = r.scheduler.admit("s", work)
    r._update(work, state="running", generation=admitted.value["generation"])
    r._emit(work, {"type": "text", "text": "durable before crash"})
    # Release resources without runtime recovery; the next owner sees only durable state.
    await r.close()
    replacement, same_binding = runtime(tmp_path)
    try:
        await replacement.start()
        result = await replacement.wait(work, timeout=3)
        assert result.value["state"] == "interrupted" and result.value["error"]["code"] == "process_restart"
        assert not replacement.provider.requests
        assert len(replacement.journal.list("outbox", "s")) == 2
        assert same_binding == b
    finally: await replacement.close()


async def test_supervised_worker_failure_is_recorded_and_restarted(tmp_path):
    r, b = runtime(tmp_path)
    calls = 0
    async def fail_once():
        nonlocal calls
        calls += 1
        if calls == 1: raise CoreError("transient", "test failure")
        await asyncio.sleep(10)
    try:
        r._running = True
        task = asyncio.create_task(r._supervise("probe", fail_once))
        for _ in range(100):
            if calls == 2: break
            await asyncio.sleep(.01)
        assert calls == 2 and r.journal.events("s", kinds=["runtime.worker_failed"])[0].payload["worker"] == "probe"
        task.cancel(); await asyncio.gather(task, return_exceptions=True)
    finally: await r.close()


async def test_invalid_limits_workspace_and_insufficient_context(tmp_path):
    with pytest.raises(CoreError, match="positive"): runtime(tmp_path, max_model_calls=0)
    r, b = runtime(tmp_path, instructions="mandatory " * 10000)
    try:
        await r.start(); _, work = await accept(r, b)
        assert (await r.wait(work, timeout=3)).value["error"]["code"] == "context_budget"
    finally: await r.close()
    other = tmp_path / "other"; other.mkdir()
    with pytest.raises(CoreError, match="differs"):
        CoreRuntime(tmp_path / "root", scope="s", workspace=other)


async def test_explicit_evolution_configuration_has_no_implicit_source_authority(tmp_path):
    r, _ = runtime(tmp_path)
    try:
        checks = [CheckSpec("build", "build", (sys.executable, "-c", "pass"))]
        with pytest.raises(CoreError, match="Authority"):
            r.configure_evolution(checks=checks, authority={"forged": True})
        evolution = r.configure_evolution(checks=checks)
        with pytest.raises(CoreError, match="authority"):
            evolution.stage("s", [], rationale="not authorized", evidence_event_ids=[])
        with pytest.raises(CoreError, match="already installed"): r.configure_evolution(checks=checks)
        await r.start()
        with pytest.raises(CoreError, match="Start"):
            other, _ = runtime(tmp_path / "other")
            try: await other.delegate("parent", "work", capabilities=[], resources={})
            finally: await other.close()
    finally: await r.close()


async def test_scheduler_constraints_control_real_child_file_and_process_effects(tmp_path):
    r, _ = runtime(tmp_path)
    child_root = tmp_path / "workspace/child"; child_root.mkdir()
    parent_root = tmp_path / "workspace"
    (parent_root / "private").write_text("parent only")
    try:
        allocation = {"tool_calls": 20, "elapsed_ms": 1000000}
        parent = r.scheduler.submit("s", "control", {}, capabilities=["workspace.write"], resources=allocation)
        r.scheduler.admit("s", parent.id)
        child = r.scheduler.submit("s", "child", {}, parent_id=parent.id,
            capabilities=["workspace.write"], resources={"tool_calls": 10, "elapsed_ms": 500000},
            constraints={"workspace_root": str(child_root), "workspace_writable": True, "delegated": True})
        admitted = r.scheduler.admit("s", child.id)
        r.tools.register("file", file_executor(WorkspaceFs(parent_root)), input_schema={"type": "object"}, output_schema={"type": "object"})
        r.tools.register("process", process_executor(ProcessRunner(parent_root, [sys.executable])), input_schema={"type": "object"}, output_schema={"type": "object"})
        async def unsafe(arguments, context): return ExecutorResult({})
        r.tools.register("unsafe", unsafe, input_schema={"type": "object"}, output_schema={"type": "object"})
        async def call(tool, args):
            return await r.tools.invoke(ToolCall(tool, args), scope="s", work_id=child.id,
                                        generation=admitted.value["generation"])
        result = await call("file", {"operation": "write", "path": "own", "content": "child"})
        assert result.outcome == "success" and (child_root / "own").read_text() == "child"
        assert not (parent_root / "own").exists()
        result = await call("file", {"operation": "read", "path": "../private"})
        assert result.outcome == "execution_error"
        result = await call("unsafe", {})
        assert result.state == "rejected" and result.error["code"] == "workspace_incompatible"
        result = await call("process", {"argv": [sys.executable, "-c", "open('/workspace/process-own','w').write('child')"]})
        assert result.outcome == "success" and (child_root / "process-own").exists()
        assert not (parent_root / "process-own").exists()
        r.scheduler.reconcile_usage("s")
        r.scheduler.finish("s", child.id, admitted.value["generation"])
        read_only = r.scheduler.submit("s", "child", {}, parent_id=parent.id, resources={"tool_calls": 10, "elapsed_ms": 500000},
            constraints={"workspace_root": str(child_root), "workspace_writable": False, "delegated": True})
        admitted = r.scheduler.admit("s", read_only.id); child = read_only
        result = await call("file", {"operation": "write", "path": "denied", "content": "bad"})
        assert result.effects["certainty"] == "none" and result.error["code"] == "scope_denied"
        result = await call("process", {"argv": [sys.executable, "-c", "open('/workspace/denied','w').write('bad')"]})
        assert result.outcome == "target_error" and not (child_root / "denied").exists()
    finally: await r.close()
