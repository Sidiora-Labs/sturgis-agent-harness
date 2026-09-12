"""OS faults at real native-memory and persistent runtime-owner boundaries."""
import asyncio
import json
from pathlib import Path
import signal
import sys

import pytest

from agent.protocol import CoreError, canonical_json
from agent.runtime import CoreRuntime
from agent.tool_runner import WorkspaceFs, file_executor
from test_continuity import eventually, harness, proposal, send


async def test_single_slot_sleep_preempts_dispatched_native_publish(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    cycles = []

    async def consolidate(scope, records, cycle):
        cycles.append(cycle)
        entered.set()
        await release.wait()
        return []

    async with harness(tmp_path, [["foreground during native sleep"]], max_running=1) as (r, binding, model, received):
        r.memory.timeout = 10
        r.memory_sleep.consolidator = consolidate
        r.journal.append("scope", "awareness.observed", {"fact": "before sleep"}, source="sensor")
        await r.start()
        await asyncio.wait_for(entered.wait(), 5)
        sleep_work = r._sleep_work
        native = r.memory.process
        assert native is not None and native.returncode is None
        assert r.scheduler.get("scope", sleep_work).value["state"] == "running"
        cycle_id = cycles[0]["cycle_id"]
        assert cycles[0]["generation"] > 0
        native.send_signal(signal.SIGSTOP)
        try:
            release.set()
            await eventually(lambda: r.journal.get("memory.sleep", cycle_id, "scope").value["phase"] == "publishing"
                             and any(sent for _, sent in r.memory._pending.values()))
            event, work = await asyncio.wait_for(send(r, binding, "respond while memory is stopped"), .8)
            assert (await r.wait(work, timeout=2)).value["state"] == "completed"
            await eventually(lambda: any(x["terminal"] == "completed" for x in received), timeout=2)
            assert native.returncode is None
            assert len(model.requests) == 1
            assert r.scheduler.get("scope", sleep_work).value["state"] == "cancelled"
            persisted = r.journal.get("memory.sleep", cycle_id, "scope").value
            assert persisted["phase"] == "publishing" and persisted["owner"] is None
            assert r.journal.get("input_dispositions", event.id, "scope").value["state"] == "incorporated"
        finally:
            native.send_signal(signal.SIGCONT)
        # The dispatched publish may still execute. Its durable receipt must let the
        # next admitted sleep finish the same cycle without re-running consolidation.
        await eventually(lambda: r.journal.get("memory.sleep", cycle_id, "scope").value["phase"] == "complete", timeout=5)
        assert len(cycles) == 1
        assert len(r.journal.list("memory.sleep", "scope")) == 1
        result = await r.memory.query("scope")
        assert result["status"] == "ok" and result["generation"] == cycles[0]["generation"]


async def test_sigstop_native_query_keeps_admission_and_wire_output_responsive(tmp_path):
    async with harness(tmp_path, [["still connected"]], max_running=1) as (r, binding, model, received):
        await r.start()
        await eventually(lambda: any(row.value["phase"] == "complete" for row in r.journal.list("memory.sleep", "scope")))
        cached = r.journal.get("runtime_memory", "latest", "scope")
        native = r.memory.process
        native.send_signal(signal.SIGSTOP)
        pending = asyncio.create_task(r.memory.query("scope", timeout=.8))
        try:
            await eventually(lambda: any(sent for _, sent in r.memory._pending.values()))
            _, work = await asyncio.wait_for(send(r, binding), .5)
            assert (await r.wait(work, timeout=2)).value["state"] == "completed"
            await eventually(lambda: any(x["content"].get("text") == "still connected" for x in received), timeout=2)
            unavailable = await pending
            assert unavailable["status"] == "unavailable"
            assert unavailable["error"]["code"] == "deadline"
            assert unavailable["error"]["dispatched"] is True
            assert unavailable["error"]["effect_state"] == "unknown"
            assert r.journal.get("runtime_memory", "latest", "scope").revision == cached.revision
            assert len(model.requests) == 1
        finally:
            native.send_signal(signal.SIGCONT)
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        assert (await r.memory.query("scope", timeout=2))["status"] == "ok"


# The child owns the actual runtime lock and operational database. The only fault
# injection in effect mode delays an executor's acknowledgement after WorkspaceFs
# has completed its real atomic write. No journal or recovery records are invented.
OWNER_PROCESS = r'''
import asyncio
import json
import sys
import threading
from pathlib import Path
from agent.channel_adapters import StreamChannelAdapter
from agent.channel_core import InboundMessage
from agent.runtime import CoreRuntime
from agent.tool_runner import WorkspaceFs, file_executor

async def main():
    config = json.loads(sys.argv[1])
    r = CoreRuntime(config["root"], scope="scope", workspace=config["workspace"],
                    capabilities=("workspace.write",), max_running=1)
    message = InboundMessage("scope", config["binding"], "user", "crash-input", "survive owner crash")
    if config["mode"] == "ingress":
        event = r.channels.admit(message)
        output = r.delivery.enqueue("scope", config["binding"], "crash-stream", 0,
                                    {"type": "text", "text": "durable before owner crash"})
        print(json.dumps({"event_id": event.id, "outbox_id": output.id}), flush=True)
    elif config["mode"] == "legacy_admission":
        # The exact production operations used before admission and turn-state
        # publication became one transaction, retained as restart compatibility.
        event = r.channels.admit(message)
        routed = r._route(event)
        admitted = r.scheduler.admit("scope", routed.value["work_id"])
        print(json.dumps({"work_id": admitted.id}), flush=True)
    elif config["mode"] == "background":
        async def consolidating(scope, records, cycle):
            print(json.dumps({"work_id": r._sleep_work, "generation": cycle["generation"]}), flush=True)
            await asyncio.Event().wait()
        r.memory_sleep.consolidator = consolidating
        await r.start()
    else:
        reader, writer = await asyncio.open_connection("127.0.0.1", config["channel_port"])
        r.adapters.register("wire", StreamChannelAdapter(reader, writer))
        if config["mode"] == "admission":
            admit = r.scheduler.admit
            def admission_gap(*args, **kwargs):
                row = admit(*args, **kwargs)
                if row is not None and row.value["kind"] == "model":
                    print(json.dumps({"work_id": row.id}), flush=True)
                    threading.Event().wait()
                return row
            r.scheduler.admit = admission_gap
        else:
            execute = file_executor(WorkspaceFs(config["workspace"]))
            async def acknowledgement_gap(arguments, context):
                await execute(arguments, context)
                print(json.dumps({"work_id": context.work_id, "attempt_id": context.attempt_id}), flush=True)
                await asyncio.Event().wait()
            acknowledgement_gap.supports_constraints = True
            r.tools.register("file.write", acknowledgement_gap, input_schema={"type": "object"},
                output_schema={"type": "object"}, required_capabilities=["workspace.write"], timeout=60)
        await r.start()
        await r.accept(message)
    await asyncio.Event().wait()

asyncio.run(main())
'''


async def start_owner(runtime, binding, mode):
    adapter = runtime.adapters.get("wire")
    config = {"root": str(runtime.root), "workspace": runtime.profiles.resources("scope")["workspace"],
              "binding": binding, "mode": mode, "channel_port": adapter.writer.get_extra_info("peername")[1]}
    await runtime.close()
    child = await asyncio.create_subprocess_exec(sys.executable, "-u", "-c", OWNER_PROCESS, canonical_json(config),
        cwd=str(Path(__file__).resolve().parents[2]), stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        line = await asyncio.wait_for(child.stdout.readline(), 10)
        if not line:
            raise AssertionError((await child.stderr.read()).decode())
        return child, json.loads(line), config, adapter
    except BaseException:
        if child.returncode is None:
            child.kill()
        await child.wait()
        raise


async def kill_owner(child):
    child.kill()
    assert await asyncio.wait_for(child.wait(), 5) == -signal.SIGKILL


def reopen(config, adapter):
    runtime = CoreRuntime(config["root"], scope="scope", workspace=config["workspace"],
                          capabilities=("workspace.write",), max_running=1, memory_interval=.05)
    runtime.adapters.register("wire", adapter)
    return runtime


async def test_killed_owner_replays_ingress_gap_and_existing_outbox_once(tmp_path):
    async with harness(tmp_path, [["input recovered"]]) as (old, binding, model, received):
        child, saved, config, adapter = await start_owner(old, binding, "ingress")
        try:
            with pytest.raises(CoreError, match="Another runtime owns") as duplicate:
                reopen(config, adapter)
            assert duplicate.value.code == "runtime_owned"
        finally:
            await kill_owner(child)
        r = reopen(config, adapter)
        try:
            assert r.journal.get("runtime_inputs", saved["event_id"], "scope") is None
            await r.start()
            row = await eventually(lambda: r.journal.get("runtime_inputs", saved["event_id"], "scope"))
            assert (await r.wait(row.value["work_id"], timeout=5)).value["state"] == "completed"
            await eventually(lambda: any(x["content"].get("text") == "durable before owner crash" for x in received))
            assert r.journal.get("outbox", saved["outbox_id"], "scope").value["state"] == "sent_unconfirmed"
            assert len(model.requests) == 1
            assert len(r.journal.list("runtime_turns", "scope")) == 1
            assert len([x for x in received if x["content"].get("text") == "durable before owner crash"]) == 1
        finally:
            await r.close()


async def test_killed_owner_preserves_unknown_effect_and_does_not_reinvoke(tmp_path):
    scripts = [["Before the effect.", proposal("file.write", {"operation": "write", "path": "effect.txt", "content": "actual effect"})],
               ["fresh input after crash"]]
    async with harness(tmp_path, scripts) as (old, binding, model, received):
        child, saved, config, adapter = await start_owner(old, binding, "effect")
        effect = tmp_path / "workspace/effect.txt"
        try:
            assert effect.read_text() == "actual effect"
            before = effect.stat()
        finally:
            await kill_owner(child)
        r = reopen(config, adapter)
        r.tools.register("file.write", file_executor(WorkspaceFs(config["workspace"])),
            input_schema={"type": "object"}, output_schema={"type": "object"}, required_capabilities=["workspace.write"])
        try:
            prior = r.journal.list("tool_requests", "scope")
            assert len(prior) == 1 and prior[0].value["result"]["state"] == "running"
            await r.start()
            interrupted = await r.wait(saved["work_id"], timeout=5)
            assert interrupted.value["state"] == "interrupted"
            assert interrupted.value["error"]["code"] == "process_restart"
            result = r.journal.get("tool_requests", prior[0].id, "scope").value["result"]
            assert result["attempt_id"] == saved["attempt_id"]
            assert result["state"] == "indeterminate" and result["outcome"] == "unknown"
            assert result["effects"]["certainty"] == "unknown"
            assert result["effects"]["evidence_event_ids"] == []
            assert r.scheduler.get("scope", saved["work_id"]).value["state"] == "cancelled"
            _, work = await send(r, binding, "new work after the crash", delivery_id="post-crash")
            assert (await r.wait(work, timeout=5)).value["state"] == "completed"
            await eventually(lambda: any(x["content"].get("text") == "fresh input after crash" for x in received))
            assert len(model.requests) == 2
            assert len(r.journal.list("tool_requests", "scope")) == 1
            after = effect.stat()
            assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
            assert not r.journal.events("scope", kinds=["tool.effect"])
        finally:
            await r.close()


@pytest.mark.parametrize("mode", ["admission", "legacy_admission"])
async def test_killed_owner_between_admission_and_turn_update_releases_single_slot(tmp_path, mode):
    async with harness(tmp_path, [["recovered work"], ["next work"]], max_running=1) as (old, binding, model, received):
        child, saved, config, adapter = await start_owner(old, binding, mode)
        await kill_owner(child)
        r = reopen(config, adapter)
        try:
            assert r.scheduler.get("scope", saved["work_id"]).value["state"] == ("queued" if mode == "admission" else "running")
            assert r.journal.get("runtime_turns", saved["work_id"], "scope").value["state"] == "queued"
            assert not model.requests
            await r.start()
            # The new exclusive owner must fence the abandoned grant. Either resume
            # the unstarted input or expose interruption; it cannot retain its slot.
            assert (await r.wait(saved["work_id"], timeout=2)).value["state"] in {"completed", "interrupted"}
            _, following = await send(r, binding, "next input", delivery_id="after-admission-crash")
            assert (await r.wait(following, timeout=2)).value["state"] == "completed"
        finally:
            await r.close()


async def test_killed_owner_background_sleep_cannot_hold_foreground_capacity(tmp_path):
    async with harness(tmp_path, [["foreground after sleep owner crash"]], max_running=1) as (old, binding, model, received):
        child, saved, config, adapter = await start_owner(old, binding, "background")
        assert saved["generation"] > 0
        await kill_owner(child)
        r = reopen(config, adapter)
        try:
            assert r.scheduler.get("scope", saved["work_id"]).value["state"] == "running"
            await r.start()
            assert r.scheduler.get("scope", saved["work_id"]).value["state"] == "cancelled"
            _, work = await send(r, binding, "foreground after background crash")
            assert (await r.wait(work, timeout=2)).value["state"] == "completed"
            await eventually(lambda: any(x["content"].get("text") == "foreground after sleep owner crash" for x in received))
        finally:
            await r.close()
