import asyncio
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import sys
import textwrap

import pytest

from agent.framing import FrameCodec, encode_frame
from agent.plugin_host import PluginHost
from agent.plugin_sdk import Capability, PluginManifest, serve, validate_schema
from agent.protocol import CoreError, ToolCall, ToolResult, canonical_json, strict_loads
from agent.skills import SkillRegistry
from agent.state import Journal
from agent.tool_runner import ExecutionContext, ExecutorResult, ProcessRunner, ToolDispatcher, WorkspaceFs, digest, file_executor, process_executor


EMPTY = {"type": "object", "properties": {}, "additionalProperties": False}
OBJECT = {"type": "object"}


@pytest.fixture
def journal(tmp_path):
    store = Journal(tmp_path / "journal.db")
    yield store
    store.close()


def authority(journal):
    def authorize(scope, grant, call):
        assert journal.events(scope)[-1].kind == "tool.request"
        assert journal.events(scope)[-1].payload["attempt_id"] == grant["claim_id"]
        row = journal.get("test_grants", grant["work_id"], scope)
        if row is None or row.value["generation"] != grant["generation"]:
            raise CoreError("revoked", "Grant is no longer current")
        journal.put("test_grants", row.id, scope, {**row.value, "claims": row.value["claims"] + 1}, expected_revision=row.revision)
        return {"claim_id": grant["claim_id"], "generation": grant["generation"]}
    return authorize


def dispatcher(journal):
    journal.put("test_grants", "work", "scope", {"generation": 1, "claims": 0})
    return ToolDispatcher(journal, authorize=authority(journal))


def context(cancel=None):
    return ExecutionContext("scope", "work", 1, "action", "attempt", cancel or asyncio.Event())


def descriptor(name="count", **extra):
    return {"name": name, "input_schema": EMPTY, "output_schema": OBJECT, **extra}


def manifest(version="1", **extra):
    return {"id": "example", "version": version, "entry": "main.py", "tools": [descriptor()],
            "commands": [descriptor("inspect", read_only=True)], **extra}


def package(tmp_path, version="1", *, schema=1, fault="", slow=0):
    root = tmp_path / ("package_" + version)
    root.mkdir()
    (root / "main.py").write_text(textwrap.dedent(f'''
        import os, socket, time
        from agent.plugin_sdk import serve
        SCHEMA = {schema}
        def handle(request):
            state = dict(request["state"])
            op = request["operation"]
            if {fault!r} == op:
                raise RuntimeError("intentional worker failure")
            if op == "migrate":
                state["counter"] = state.get("counter", 0) * 10
            if op == "tool":
                time.sleep({slow})
                state["counter"] = state.get("counter", 0) + 1
            data = {{"value": state.get("counter", 0)}}
            if request["target"] == "probe":
                data["private_visible"] = os.path.exists("/root/Sturgis")
                data["readable"] = os.path.exists("/workspace/visible")
                try:
                    open("/workspace/written", "w").write("effect")
                    data["writable"] = True
                except OSError:
                    data["writable"] = False
                sock = socket.socket()
                data["host_reachable"] = sock.connect_ex(("127.0.0.1", request["arguments"].get("port", 9))) == 0
                sock.close()
            return {{"data": data, "state": state, "state_schema": SCHEMA}}
        serve(handle)
    '''))
    return root


@pytest.mark.parametrize("value", [None, {}, {"name": "../bad", "input_schema": EMPTY, "output_schema": OBJECT},
    descriptor(timeout=0), descriptor(output_limit=0), descriptor(read_only="yes"), descriptor(input_schema=[]),
    descriptor(input_schema={"type": "nonexistent"}), descriptor(required_capabilities=[""]), descriptor(extra=True)])
def test_capability_contract_rejects_invalid_descriptors(value):
    with pytest.raises(CoreError):
        Capability.from_dict(value)


@pytest.mark.parametrize("value", [None, {}, manifest(entry="../main.py"), manifest(protocol_version="plugin/2"),
    manifest(state_schema=0), manifest(accepts_state_schemas=[0]), manifest(required_grants=["network"]),
    manifest(tools=[descriptor(), descriptor()]), manifest(tools=[descriptor(required_capabilities=["workspace.write"])]),
    manifest(unknown=3), manifest(tools=1)])
def test_plugin_manifest_validation(value):
    with pytest.raises(CoreError):
        PluginManifest.from_dict(value)


def test_sdk_worker_protocol_and_schemas():
    m = PluginManifest.from_dict(manifest())
    assert m.operation("command", "inspect").read_only
    with pytest.raises(CoreError, match="not declared"):
        m.operation("tool", "missing")
    with pytest.raises(CoreError):
        validate_schema({"type": "missing"}, {})
    with pytest.raises(CoreError):
        validate_schema(EMPTY, {"unexpected": True})
    with pytest.raises(CoreError, match="local schema"):
        validate_schema({"$ref": "https://example.com/schema"}, {})
    with pytest.raises(CoreError, match="structural bound"):
        validate_schema({"enum": list(range(10001))}, 1)
    with pytest.raises(CoreError, match="Recursive schema"):
        validate_schema({"$ref": "#"}, {})
    with pytest.raises(CoreError, match="Schema reference"):
        validate_schema({"$ref": "#/$defs/missing"}, {})
    request = {"protocol_version": "plugin/1", "request_id": "r", "operation": "health", "target": None,
               "arguments": {}, "state": {}, "state_schema": 1}
    payload = encode_frame(canonical_json(request).encode())
    result = io.BytesIO()
    serve(lambda r: {"data": {"ok": True}, "state": r["state"], "state_schema": 1}, reader=io.BytesIO(payload), writer=result)
    assert strict_loads(FrameCodec().feed(result.getvalue())[0])["data"] == {"ok": True}
    for wire in (b"", payload + payload, encode_frame(b"{}"), b"\0"):
        with pytest.raises(CoreError):
            serve(lambda r: {}, reader=io.BytesIO(wire), writer=io.BytesIO())
    with pytest.raises(CoreError):
        serve(lambda r: {}, reader=io.BytesIO(payload), writer=io.BytesIO())


def test_rooted_file_operations_preimages_and_symlinks(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    fs = WorkspaceFs(root)
    result = fs.write("directory/a.txt", b"hello world")
    assert fs.read("directory/a.txt") == b"hello world"
    with pytest.raises(CoreError, match="preimage"):
        fs.write("directory/a.txt", b"lost update")
    changed = fs.edit("directory/a.txt", "world", "there", expected_sha256=result["after_sha256"])
    fs.copy("directory/a.txt", "b.txt", expected_source=changed["after_sha256"])
    fs.rename("b.txt", "c.txt", expected_source=changed["after_sha256"])
    assert fs.search("there")[0]["line"] == 1
    assert len(fs.list()) == 2
    assert fs.list("directory")[0]["file"]
    fs.delete("c.txt", expected_sha256=changed["after_sha256"])
    outside = tmp_path / "outside"
    outside.write_text("private")
    (root / "link").symlink_to(outside)
    (root / "dirlink").symlink_to(tmp_path, target_is_directory=True)
    for path in ("../outside", str(outside), "link", "dirlink/outside", "", ".", "nul\x00"):
        with pytest.raises(CoreError):
            fs.read(path)
    with pytest.raises(CoreError):
        fs.write("link", b"attack", expected_sha256=digest(b"private"))
    assert outside.read_text() == "private"
    assert all("link" not in item["path"] for item in fs.search("private"))
    os.mkfifo(root / "pipe")
    with pytest.raises(CoreError):
        fs.read("pipe")


def test_file_limits_and_mutation_failures(tmp_path):
    fs = WorkspaceFs(tmp_path, max_bytes=5, max_entries=2)
    with pytest.raises(CoreError):
        WorkspaceFs(tmp_path, max_bytes=0)
    with pytest.raises(CoreError):
        fs.write("x", b"abcdef")
    (tmp_path / "large").write_bytes(b"abcdef")
    with pytest.raises(CoreError):
        fs.read("large")
    (tmp_path / "large").unlink()
    fs.write("x", b"aa")
    for fn in (lambda: fs.edit("x", "a", "b", expected_sha256=digest(b"aa")),
               lambda: fs.edit("x", "", "b", expected_sha256=digest(b"aa")),
               lambda: fs.copy("x", "y", expected_source="wrong"),
               lambda: fs.delete("absent", expected_sha256=None),
               lambda: fs.rename("absent", "y", expected_source=None),
               lambda: fs.search("")):
        with pytest.raises(CoreError):
            fn()
    fs.write("binary", b"\xff")
    with pytest.raises(CoreError):
        fs.edit("binary", "a", "b", expected_sha256=digest(b"\xff"))
    fs.write("third", b"a")
    with pytest.raises(CoreError):
        fs.list()
    (tmp_path / "third").unlink()
    (tmp_path / "binary").unlink()
    fs.write("y", b"aaaaa")
    with pytest.raises(CoreError):
        fs.search("a")


async def test_real_process_limits_environment_and_namespace(tmp_path):
    runner = ProcessRunner(tmp_path, [sys.executable], environment_keys=["ALLOWED"])
    result = await runner.run([sys.executable, "-c", "import os; print(os.environ.get('ALLOWED')); print(os.path.exists('/root/Sturgis')); print('err',file=__import__('sys').stderr)"], environment={"ALLOWED": "yes"})
    assert result["stdout"] == b"yes\nFalse\n"
    assert result["stderr"] == b"err\n" and result["exit_code"] == 0
    result = await runner.run([sys.executable, "-c", "import sys; open('/workspace/effect','w').write('real'); print('bwrap: fabricated error',file=sys.stderr); sys.exit(2)"])
    assert result["exit_code"] == 2 and (tmp_path / "effect").read_text() == "real"
    result = await runner.run([sys.executable, "-c", "import sys; print(sys.stdin.read())"], stdin=b"input")
    assert result["stdout"] == b"input\n"
    for kwargs in ({"argv": []}, {"argv": ["/missing"]}, {"argv": ["/usr/bin/true"]},
                   {"argv": [sys.executable, "-c", "pass"], "cwd": "../"},
                   {"argv": [sys.executable, "-c", "pass"], "cwd": "missing"},
                   {"argv": [sys.executable, "-c", "pass"], "environment": {"SECRET": "no"}},
                   {"argv": [sys.executable, "-c", "pass"], "timeout": 0}):
        with pytest.raises(CoreError):
            await runner.run(**kwargs)
    with pytest.raises(CoreError, match="output"):
        await runner.run([sys.executable, "-c", "print('x'*10000)"], output_limit=100)
    with pytest.raises(CoreError, match="deadline"):
        await runner.run([sys.executable, "-c", "import time; time.sleep(10)"], timeout=.05)
    cancel = asyncio.Event()
    cancel.set()
    with pytest.raises(CoreError) as exc:
        await runner.run([sys.executable, "-c", "pass"], cancel=cancel)
    assert exc.value.details["effects"] == "none"


async def test_process_tree_cancellation_stops_descendant_effect(tmp_path):
    runner = ProcessRunner(tmp_path, [sys.executable])
    cancel = asyncio.Event()
    code = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',\"import time; time.sleep(.5); open('/workspace/late','w').write('bad')\"]); print('ready',flush=True); time.sleep(10)"
    task = asyncio.create_task(runner.run([sys.executable, "-c", code], cancel=cancel))
    await asyncio.sleep(.15)
    cancel.set()
    with pytest.raises(CoreError, match="cancellation"):
        await task
    await asyncio.sleep(.55)
    assert not (tmp_path / "late").exists()


async def test_dispatch_transaction_schema_dedupe_and_real_file_effect(journal, tmp_path):
    dispatch = dispatcher(journal)
    dispatch.register("file.write", file_executor(WorkspaceFs(tmp_path)), input_schema={"type": "object", "required": ["operation", "path", "content"], "properties": {"operation": {"const": "write"}, "path": {"type": "string"}, "content": {"type": "string"}}, "additionalProperties": False}, output_schema=OBJECT, required_capabilities=["workspace.write"])
    call = ToolCall("file.write", {"operation": "write", "path": "created", "content": "observed"}, expectation={"observable": "created exists"})
    result = await dispatch.invoke(call, scope="scope", work_id="work", generation=1, request_id="request")
    assert result.outcome == "success" and result.effects["certainty"] == "observed"
    assert result.effects["evidence_event_ids"] and (tmp_path / "created").read_text() == "observed"
    assert (await dispatch.invoke(call, scope="scope", work_id="work", generation=1, request_id="request")) == result
    assert journal.get("test_grants", "work", "scope").value["claims"] == 1
    usage = [event for event in journal.events("scope") if event.kind == "tool.usage"]
    assert len(usage) == 1 and usage[0].payload["claim_id"] == result.attempt_id
    assert usage[0].payload["resources"]["tool_calls"] == 1
    with pytest.raises(CoreError, match="reused"):
        await dispatch.invoke(replace(call, arguments={**call.arguments, "content": "changed"}), scope="scope", work_id="work", generation=1, request_id="request")
    for bad in (ToolCall("absent", {}), ToolCall("file.write", {}), {"tool": "file.write", "arguments": {}, "scope": "forged"}):
        assert (await dispatch.invoke(bad, scope="scope", work_id="work", generation=1)).state == "rejected"
    result = await dispatch.invoke(call, scope="scope", work_id="work", generation=2)
    assert result.error["code"] == "revoked"
    assert journal.get("test_grants", "work", "scope").value["claims"] == 1
    assert dispatch.descriptors()[0]["required_capabilities"] == ("workspace.write",)
    with pytest.raises(CoreError):
        ToolDispatcher(journal, authorize=None)
    with pytest.raises(CoreError):
        dispatch.register("file.write", None, input_schema=EMPTY, output_schema=OBJECT)


async def test_dispatch_process_target_error_and_file_operations(journal, tmp_path):
    dispatch = dispatcher(journal)
    runner = ProcessRunner(tmp_path, [sys.executable])
    dispatch.register("terminal.execute", process_executor(runner), input_schema=OBJECT, output_schema=OBJECT)
    result = await dispatch.invoke(ToolCall("terminal.execute", {"argv": [sys.executable, "-c", "print('native'); raise SystemExit(2)"]}), scope="scope", work_id="work", generation=1)
    assert result.state == "completed" and result.outcome == "target_error" and result.data["exit_code"] == 2
    binary = await dispatch.invoke(ToolCall("terminal.execute", {"argv": [sys.executable, "-c", "import os; os.write(1,b'\\xff')"]}), scope="scope", work_id="work", generation=1)
    assert binary.data["stdout"] == "/w==" and binary.data["stdout_encoding"] == "base64"
    fs = WorkspaceFs(tmp_path)
    fs.write("readable", b"hello")
    execute = file_executor(fs)
    assert (await execute({"operation": "read", "path": "readable"}, context())).data["content"] == "hello"
    fs.write("binary", b"\xff")
    assert (await execute({"operation": "read", "path": "binary"}, context())).data["encoding"] == "base64"
    assert (await execute({"operation": "list"}, context())).effects == "none"
    with pytest.raises(CoreError):
        await execute({"operation": "unsupported"}, context())


async def test_dispatch_cancellation_failures_recovery_and_reconciliation(journal):
    dispatch = dispatcher(journal)
    started = asyncio.Event()
    async def slow(args, ctx):
        started.set()
        await asyncio.sleep(10)
    async def bad(args, ctx):
        return {"not": "executor result"}
    async def large(args, ctx):
        return ExecutorResult({"content": "x" * 100})
    async def error(args, ctx):
        raise ValueError("executor broke")
    for name, function, extra in (("slow", slow, {"timeout": .05}), ("bad", bad, {}), ("large", large, {"output_limit": 20}), ("error", error, {})):
        dispatch.register(name, function, input_schema=EMPTY, output_schema=OBJECT, **extra)
    for name in ("bad", "large", "error"):
        assert (await dispatch.invoke(ToolCall(name, {}), scope="scope", work_id="work", generation=1)).outcome == "execution_error"
    result = await dispatch.invoke(ToolCall("slow", {}), scope="scope", work_id="work", generation=1, request_id="timeout")
    assert result.state == "indeterminate"
    assert not any(event.kind == "tool.usage" and event.payload["claim_id"] == result.attempt_id for event in journal.events("scope"))
    complete = ToolResult(result.action_id, result.attempt_id, result.tool, "completed", "success", {"done": True})
    request_event = next(event for event in journal.events("scope") if event.kind == "tool.request" and event.payload["attempt_id"] == result.attempt_id)
    evidence = journal.append("scope", "tool.observation", {"schema_version": "tool-observation/1",
        "request_id": "timeout", "work_id": "work", "request_event_id": request_event.id,
        "result": complete.as_dict(), "usage": journal.get("tool_requests", "timeout", "scope").value["observed_resources"]}, source="tool_observer")
    with pytest.raises(CoreError):
        dispatch.reconcile("scope", "timeout", complete, evidence_event_ids=["missing"])
    admitted = dispatch.reconcile("scope", "timeout", complete, evidence_event_ids=[evidence.id])
    assert admitted == replace(complete, effects={**complete.effects, "evidence_event_ids": [evidence.id]})
    assert any(event.kind == "tool.usage" and event.payload["claim_id"] == result.attempt_id for event in journal.events("scope"))
    with pytest.raises(CoreError):
        dispatch.reconcile("scope", "timeout", complete, evidence_event_ids=[evidence.id])
    cancel = asyncio.Event()
    started.clear()
    pending = asyncio.create_task(dispatch.invoke(ToolCall("slow", {}), scope="scope", work_id="work", generation=1, cancel=cancel, request_id="cancel"))
    await started.wait()
    repeated = await dispatch.invoke(ToolCall("slow", {}), scope="scope", work_id="work", generation=1, request_id="cancel")
    assert repeated.state == "indeterminate"
    cancel.set()
    assert (await pending).state == "indeterminate"
    assert (await dispatch.invoke(ToolCall("slow", {}), scope="scope", work_id="work", generation=1, cancel=cancel)).state == "rejected"
    with pytest.raises(CoreError):
        await dispatch.invoke(ToolCall("slow", {}), scope="scope", work_id="work", generation=1, request_id="")
    assert (await dispatch.invoke(ToolCall("slow", {}), scope="scope", work_id="work", generation=0)).state == "rejected"
    row = journal.get("tool_requests", "cancel", "scope")
    interrupted = {**row.value, "result": {**row.value["result"], "state": "running", "outcome": None}}
    journal.put("tool_requests", "interrupted", "scope", interrupted)
    assert dispatch.recover("scope")[0].state == "indeterminate"
    assert dispatch.recover("scope") == []


async def test_plugin_worker_migration_commands_rollback_restart(journal, tmp_path):
    host = PluginHost(journal, tmp_path / "plugins")
    p1 = package(tmp_path, "1")
    host.install("scope", p1, manifest())
    with pytest.raises(CoreError):
        host.install("scope", p1, manifest())
    await host.activate("scope", "example")
    assert (await host.invoke("scope", "example", "count", {}))["value"] == 1
    assert (await host.invoke("scope", "example", "inspect", {}, kind="command"))["value"] == 1
    assert (await host.health("scope", "example"))["value"] == 1
    with pytest.raises(CoreError):
        await host.invoke("other", "example", "count", {})
    with pytest.raises(CoreError):
        await host.invoke("scope", "example", "count", {}, version="other")
    p2 = package(tmp_path, "2", schema=2)
    host.install("scope", p2, manifest("2", state_schema=2, accepts_state_schemas=[1]))
    await host.activate("scope", "example")
    assert (await host.invoke("scope", "example", "count", {}))["value"] == 11
    host = PluginHost(journal, tmp_path / "plugins")
    assert host.inspect("scope", "example").value["state"]["counter"] == 11
    await host.rollback("scope", "example")
    assert (await host.invoke("scope", "example", "inspect", {}, kind="command"))["value"] == 1
    await host.drain("scope", "example")
    with pytest.raises(CoreError):
        await host.invoke("scope", "example", "count", {})
    assert host.uninstall("scope", "example").value["status"] == "uninstalled"


async def test_plugin_filesystem_and_network_grants_are_real(journal, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "visible").write_text("permitted")
    plugin = package(tmp_path)
    probe = descriptor("probe", input_schema=OBJECT, required_capabilities=["workspace.read"])
    host = PluginHost(journal, tmp_path / "plugins", workspace_root=workspace)
    definition = manifest(tools=[probe], required_grants=["workspace.read"])
    with pytest.raises(CoreError):
        host.install("scope", plugin, definition)
    host.install("scope", plugin, definition, grants=["workspace.read"])
    await host.activate("scope", "example")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        result = await host.invoke("scope", "example", "probe", {"port": listener.getsockname()[1]})
    assert result["readable"] and not result["writable"] and not result["private_visible"] and not result["host_reachable"]
    p2 = package(tmp_path, "2")
    host.install("scope", p2, manifest("2", tools=[probe], required_grants=["workspace.read", "workspace.write"]), grants=["workspace.read", "workspace.write"])
    await host.activate("scope", "example")
    assert (await host.invoke("scope", "example", "probe", {}))["writable"]
    assert (workspace / "written").read_text() == "effect"


async def test_plugin_failure_quarantine_and_version_integrity(journal, tmp_path):
    host = PluginHost(journal, tmp_path / "plugins")
    with pytest.raises(CoreError):
        host.install("scope", package(tmp_path, "needs_root"), manifest(), grants=["workspace.read"])
    p1 = package(tmp_path, "1")
    host.install("scope", p1, manifest())
    with pytest.raises(CoreError):
        await host.health("scope", "example")
    with pytest.raises(CoreError):
        await host.rollback("scope", "example")
    await host.activate("scope", "example")
    with pytest.raises(CoreError):
        host.uninstall("scope", "example")
    with pytest.raises(CoreError):
        await host.activate("scope", "example", "missing")
    host.install("scope", package(tmp_path, "incompatible", schema=2), manifest("incompatible", state_schema=2, accepts_state_schemas=[]))
    with pytest.raises(CoreError):
        await host.activate("scope", "example")
    host.install("scope", package(tmp_path, "broken", schema=2, fault="migrate"), manifest("broken", state_schema=2))
    with pytest.raises(CoreError):
        await host.activate("scope", "example")
    assert host.inspect("scope", "example").value["active_version"] == "1"
    with pytest.raises(CoreError, match="Quarantined"):
        await host.activate("scope", "example", "broken")
    host.install("scope", package(tmp_path, "failure", fault="tool"), manifest("failure"))
    await host.activate("scope", "example")
    with pytest.raises(CoreError, match="worker"):
        await host.invoke("scope", "example", "count", {})
    assert host.inspect("scope", "example").value["status"] == "quarantined"
    host._update("scope", "example", {"status": "draining", "pending": {"operation": "tool"}})
    assert host.recover("scope")[0].value["status"] == "quarantined"
    assert host.recover("scope") == []
    await host.activate("scope", "example", "1")
    row = host.inspect("scope", "example")
    Path(row.value["versions"]["1"]["path"], "main.py").write_text("tampered")
    with pytest.raises(CoreError, match="bytes changed"):
        await host.health("scope", "example")


async def test_plugin_dispatch_draining_cancellation_and_malformed_response(journal, tmp_path):
    host = PluginHost(journal, tmp_path / "plugins")
    host.install("scope", package(tmp_path), manifest())
    with pytest.raises(CoreError):
        host.register_tools(dispatcher(journal), "scope", "example")
    dispatch = ToolDispatcher(journal, authorize=authority(journal))
    await host.activate("scope", "example")
    host.register_tools(dispatch, "scope", "example")
    result = await dispatch.invoke(ToolCall("plugin.example.tool.count", {}), scope="scope", work_id="work", generation=1)
    assert result.data["value"] == 1
    result = await dispatch.invoke(ToolCall("plugin.example.command.inspect", {}), scope="scope", work_id="work", generation=1)
    assert result.effects["certainty"] == "none"
    execute = dispatch.registry["plugin.example.tool.count", "1"][1]
    with pytest.raises(CoreError):
        await execute({}, replace(context(), scope="another"))
    host.install("scope", package(tmp_path, "slow", slow=.15), manifest("slow"))
    await host.activate("scope", "example")
    running = asyncio.create_task(host.invoke("scope", "example", "count", {}))
    await asyncio.sleep(.04)
    disabled = asyncio.create_task(host.disable("scope", "example"))
    assert (await running)["value"] >= 1
    assert (await disabled).value["status"] == "disabled"
    bad = package(tmp_path, "malformed")
    (bad / "main.py").write_text("print('not a frame')")
    host.install("scope", bad, manifest("malformed"))
    with pytest.raises(CoreError):
        await host.activate("scope", "example")


def skill(id="read", version="1", **extra):
    return {"id": id, "version": version, "description": "Read useful files", "triggers": ["read"],
            "steps": ["Read requested file", "Check result"], "required_tools": ["file.read"], **extra}


def test_skills_scoped_discovery_selection_revisions_and_rollback(journal, tmp_path):
    roots = {}
    for kind in ("built_in", "global", "workspace"):
        root = tmp_path / kind
        root.mkdir()
        (root / "read.skill.json").write_text(json.dumps(skill(version=kind)))
        roots[kind] = root
    registry = SkillRegistry(journal, roots)
    assert registry.discover("scope")[0]["manifest"]["version"] == "workspace"
    registry.install("scope", skill(version="profile"))
    assert registry.discover("scope")[0]["origin"] == "profile"
    assert registry.discover("other")[0]["origin"] == "workspace"
    assert registry.select("scope", "read this", ["file.read"])["selected"]
    assert registry.select("scope", "read", [])["excluded"][0]["reason"] == "unavailable_tools"
    assert registry.select("scope", "different", ["file.read"])["excluded"][0]["reason"] == "irrelevant"
    assert registry.select("scope", "read", ["file.read"], max_bytes=1)["excluded"][0]["reason"] == "context_budget"
    registry.install("scope", skill(version="next", platforms=["linux"]))
    assert registry.select("scope", "read", ["file.read"], platform="windows")["excluded"][0]["reason"] == "platform"
    registry.set_enabled("scope", "read", False)
    assert registry.select("scope", "read", ["file.read"])["excluded"][0]["reason"] == "inactive"
    registry.set_enabled("scope", "read", True)
    assert registry.rollback("scope", "read").value["manifest"]["version"] == "profile"
    with pytest.raises(CoreError):
        registry.install("scope", skill(version="profile"))
    with pytest.raises(CoreError):
        registry.set_enabled("scope", "missing", True)
    with pytest.raises(CoreError):
        registry.rollback("missing", "read")
    with pytest.raises(CoreError):
        registry.select("scope", "", [])
    with pytest.raises(CoreError):
        SkillRegistry(journal, {"unknown": tmp_path})


async def test_skill_synthesis_uses_actual_scoped_tool_outcomes(journal, tmp_path):
    dispatch = dispatcher(journal)
    fs = WorkspaceFs(tmp_path)
    fs.write("evidence", b"real content")
    dispatch.register("file.read", file_executor(fs), input_schema=OBJECT, output_schema=OBJECT, read_only=True)
    for _ in range(2):
        await dispatch.invoke(ToolCall("file.read", {"operation": "read", "path": "evidence"}), scope="scope", work_id="work", generation=1)
    events = [event.id for event in journal.events("scope") if event.kind == "tool.result"]
    registry = SkillRegistry(journal, require_review=True)
    candidate = registry.synthesize("scope", skill(), events)
    assert candidate.value["state"] == "awaiting_review"
    with pytest.raises(CoreError):
        registry.activate("scope", candidate.id)
    registry.review("scope", candidate.id, approved=True, reviewer="owner")
    assert registry.activate("scope", candidate.id).value["state"] == "activated"
    with pytest.raises(CoreError):
        registry.review("scope", candidate.id, approved=True, reviewer="owner")
    assert registry.rollback("scope", "read").value["state"] == "withdrawn"
    with pytest.raises(CoreError):
        registry.synthesize("scope", skill(), events)
    with pytest.raises(CoreError):
        registry.synthesize("other", skill(), events)
    with pytest.raises(CoreError):
        registry.synthesize("scope", skill(), [events[0], events[0]])
    wrong = journal.append("scope", "user.message", {"outcome": "success"})
    with pytest.raises(CoreError):
        registry.synthesize("scope", skill(id="other"), [events[0], wrong.id])
    rejected = registry.synthesize("scope", skill(id="rejected"), events)
    registry.review("scope", rejected.id, approved=False, reviewer="owner")
    with pytest.raises(CoreError):
        registry.activate("scope", rejected.id)
    automatic = SkillRegistry(journal).synthesize("scope", skill(id="automatic"), events)
    assert automatic.value["state"] == "ready"


@pytest.mark.parametrize("value", [{}, skill(id="../escape"), skill(description=""), skill(steps=[]),
                                  skill(triggers=[]), skill(steps="not a list"), skill(extra=True)])
def test_invalid_skill_packages(journal, value):
    with pytest.raises(CoreError):
        SkillRegistry(journal).install("scope", value)
