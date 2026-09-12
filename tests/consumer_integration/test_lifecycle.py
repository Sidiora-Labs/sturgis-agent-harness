"""Actual private Python/native processes, namespace boundaries and durable wakes."""
import asyncio
import base64
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import signal
import sys
import time

import pytest

from agent.protocol import CoreError, ToolCall, canonical_json
from agent.scheduler import Scheduler
from agent.state import Journal
from consumer.config import Settings
from consumer.lifecycle import COMMANDS, CONTROL, RUNTIMES, Lifecycle
from consumer.workers import WorkerClient, WorkerManager


@pytest.mark.parametrize("mode,memory_limit", [("hosted", 2147483648), ("hosted", 274877906944), ("self_hosted", 274877906944)])
async def test_browser_resource_readiness_and_real_worker_model_tool_render(tmp_path, mode, memory_limit):
    from tests.consumer.test_connections import wire
    from tests.consumer.test_connectors import CHROME, reply
    from tests.integration.test_continuity import WireModel, proposal

    model = WireModel([[proposal("consumer.probe", {})]])
    server = await asyncio.start_server(model.handle, "127.0.0.1", 0)
    model_origin = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    try:
        async with wire() as (forbidden, unexpected):
            async def page(writer, request):
                if request["path"] == "/script.js":
                    await reply(writer, b'document.title="Hosted rendered"; document.body.append("JavaScript executed");',
                        headers={"Content-Type": "application/javascript"})
                elif request["path"] == "/start":
                    await reply(writer, b"", status=302, headers={"Location": "/page"})
                else:
                    await reply(writer, ('<html><title>Original</title><body>Actual hosted page<script src="/script.js"></script>'
                        '<img src="' + forbidden + '/private"></body></html>').encode(), headers={"Content-Type": "text/html"})
            async with wire(page) as (origin, requests):
                manager = WorkerManager(Settings(root=tmp_path / "installation", mode=mode, worker_timeout=15,
                    browser_binary=Path(CHROME), worker_memory_bytes=memory_limit,
                    private_network_origins=(origin, forbidden, model_origin)))
                try:
                    client = await manager.start("browser_agent")
                    row = await client.request("connections.configure", {"id": "web", "kind": "browser",
                        "endpoint": origin + "/start", "options": {"allowed_origins": [origin]}})
                    row = await client.request("connections.grant", {"id": "web", "operations": ["inspect"], "expected_revision": row["revision"]})
                    row = await client.request("connections.probe", {"id": "web", "expected_revision": row["revision"]})
                    if mode == "hosted":
                        # This host's zero-capability worker cannot configure a
                        # nested bwrap network namespace, even at the larger AS cap.
                        assert row["status"] == "unavailable"
                        assert row["probe"]["error"]["code"] == "browser_unavailable"
                        assert requests == [] and unexpected == []
                        return
                    assert row["status"] == "ready", canonical_json(row)
                    assert row["probe"]["kind"] == "isolated_chromium"
                    await client.request("permissions.set", {"tool": "connection.web.inspect", "mode": "allow"})
                    call = ToolCall("connection.web.inspect", {}, str(row["revision"]))
                    model.scripts.extend([[{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "browser_call",
                        "function": {"name": "invoke", "arguments": canonical_json(call.as_dict())}}]}}]}], ["The rendered document is available."]])
                    provider = await client.request("providers.configure", {"provider_id": "default", "endpoint": model_origin + "/model",
                        "model": "wire", "context_limit": 60000, "output_limit": 512, "tools": True})
                    assert (await client.request("providers.probe", {"provider_id": "default", "expected_revision": provider["revision"]}))["ready"]
                    await client.request("providers.activate", {"provider_id": "default", "expected_revision": provider["revision"]})
                    room = await client.request("conversations.create", {})
                    await client.request("messages.send", {"conversation_id": room["id"], "message_id": "render", "text": "Inspect the granted browser page."})
                    async def finished():
                        value = await client.request("conversations.get", {"id": room["id"]})
                        return value if value["turns"] and value["turns"][0]["state"] in {"completed", "failed", "interrupted"} else None
                    completed = await eventually(finished, timeout=40)
                    assert completed["turns"][0]["state"] == "completed", completed
                    evidence = Journal(client.config.root / "operations.sqlite")
                    try:
                        results = [event.payload for event in evidence.events(client.config.scope, limit=1000)
                            if event.kind == "tool.result" and event.payload["tool"] == "connection.web.inspect"]
                    finally:
                        evidence.close()
                    assert len(results) == 1 and results[0]["outcome"] == "success", results
                    result = results[0]["data"]
                    assert result["title"] == "Hosted rendered" and "JavaScript executed" in result["text"]
                    assert result["url"] == origin + "/page" and result["network"] == "namespace_isolated"
                    assert unexpected == [] and any(item["code"] == "network_origin_denied" for item in result["blocked"])
                    screenshot = await client.request("artifacts.read", {"id": result["screenshot_artifact_id"]})
                    assert base64.b64decode(screenshot["data_base64"]).startswith(b"\x89PNG")
                    assert {"/start", "/page", "/script.js"} <= {r["path"] for r in requests}
                finally:
                    await manager.close()
    finally:
        server.close()
        for task in list(model.tasks): task.cancel()
        await asyncio.gather(*model.tasks, return_exceptions=True)
        await server.wait_closed()


async def eventually(fn, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = await fn()
        if result: return result
        await asyncio.sleep(.025)
    raise AssertionError("Condition did not become true")


async def quiet(client):
    async def check():
        value = await client.request("_worker.status", {})
        return value if not any(value[k] for k in ("active_work", "queued_work", "pending_deliveries", "active_commands")) else None
    return await eventually(check)


async def suspend_when_quiet(lifecycle, agent_id):
    async def attempt():
        try: return await lifecycle.suspend(agent_id)
        except CoreError as exc:
            if exc.code != "runtime_busy": raise
            return None
    return await eventually(attempt)


def descendants(pid):
    found, pending = [], [pid]
    while pending:
        current = pending.pop()
        try: children = Path(f"/proc/{current}/task/{current}/children").read_text().split()
        except FileNotFoundError: continue
        for child in children:
            value = int(child); found.append(value); pending.append(value)
    return found


@pytest.mark.parametrize("mode", ["self_hosted", "hosted"])
async def test_actual_worker_artifact_memory_restart_and_cross_runtime_boundaries(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("STURGIS_TEST_SECRET", "must-not-enter-worker")
    manager = WorkerManager(Settings(root=tmp_path / "installation", mode=mode, worker_timeout=10))
    try:
        a, b = await asyncio.gather(manager.start("agent_a"), manager.start("agent_b"))
        assert (await a.request("overview", {}))["agent_id"] == "agent_a"
        assert await manager.start("agent_a") is a
        attachment = await a.request("attachments.upload", {"name": "source.txt", "media_type": "text/plain",
            "data_base64": base64.b64encode(b"durable native knowledge").decode(), "operation_id": "upload"})
        knowledge = await a.request("knowledge.add", {"attachment_id": attachment["id"], "title": "Known source", "operation_id": "knowledge"})
        assert knowledge["indexing"]["status"] == "ok"
        assert (await a.request("memory.query", {"text": "durable native"}))["data"]["records"]
        assert (await b.request("memory.query", {}))["data"]["records"] == []
        with pytest.raises(CoreError) as error: await b.request("attachments.get", {"id": attachment["id"]})
        assert error.value.code == "not_found"
        with pytest.raises(CoreError) as error:
            await WorkerClient(b.config, a._key).request("overview", {})
        assert error.value.code == "worker_protocol"
        await a.request("files.write", {"path": "own.txt", "data_base64": base64.b64encode(b"own").decode()}, request_id="write_once")
        for path in ("../agent_b/private/ipc.key", str(b.config.private_root / "ipc.key"), ".sturgis/profiles"):
            with pytest.raises(CoreError): await a.request("files.read", {"path": path})
        if mode == "hosted":
            tree = descendants(manager._processes["agent_a"].pid)
            workers = [pid for pid in tree if Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")[0] == os.fsencode(sys.executable)]
            assert workers, tree
            pid = workers[0]
            for namespace in ("user", "pid", "mnt", "ipc", "uts"):
                assert Path(f"/proc/{pid}/ns/{namespace}").stat().st_ino != Path(f"/proc/self/ns/{namespace}").stat().st_ino
            sandbox_root = Path(f"/proc/{pid}/root")
            assert (sandbox_root / str(a.config.workspace / "own.txt").lstrip("/")).read_bytes() == b"own"
            assert not (sandbox_root / str(b.config.private_root / "ipc.key").lstrip("/")).exists()
            assert not (sandbox_root / str(manager.settings.root / ".worker-manager.lock").lstrip("/")).exists()
            assert b"STURGIS_TEST_SECRET" not in Path(f"/proc/{pid}/environ").read_bytes()
            assert "2147483648" in Path(f"/proc/{pid}/limits").read_text()
        original_pid = (await manager.status("agent_a"))["pid"]
        await manager.stop("agent_a")
        assert (await manager.status("agent_a"))["state"] == "stopped"
        a = await manager.start("agent_a")
        assert (await manager.status("agent_a"))["pid"] != original_pid
        assert (await a.request("memory.query", {"text": "durable native"}))["data"]["records"]
        replay = await a.request("files.write", {"path": "own.txt", "data_base64": base64.b64encode(b"own").decode()}, request_id="write_once")
        assert replay["before_sha256"] is None
        assert await manager.delete("agent_b") == {"agent_id": "agent_b", "deleted": True}
        assert not b.config.root.parent.exists()
    finally: await manager.close()


async def test_message_payload_precedes_wake_secret_params_are_transient_and_retries_read_receipts(tmp_path):
    journal = Journal(tmp_path / "control.sqlite")
    manager = WorkerManager(Settings(root=tmp_path / "installation", worker_timeout=5))
    lifecycle = Lifecycle(journal, manager)
    try:
        await lifecycle.create("agent")
        conversation = await lifecycle.command("agent", "conversations.create", {"title": "Durable"}, request_id="conversation")
        assert await lifecycle.command("agent", "conversations.create", {"title": "Durable"}, request_id="conversation") == conversation
        await manager.stop("agent")
        settings = manager.settings
        manager.settings = replace(settings, native_binary=tmp_path / "missing-native")
        params = {"conversation_id": conversation["id"], "message_id": "message_one", "text": "Keep this before waking"}
        with pytest.raises(CoreError):
            await lifecycle.command("agent", "messages.send", params, request_id="send")
        queued = journal.get(COMMANDS, "agent:send", CONTROL)
        assert queued.value["state"] == "queued" and queued.value["params"] == params
        secret = "provider-private-value-never-in-control-journal"
        with pytest.raises(CoreError):
            await lifecycle.command("agent", "providers.credentials.put", {"name": "key", "secret": secret}, request_id="credential")
        assert journal.get(COMMANDS, "agent:credential", CONTROL).value["params"] is None
        assert secret not in canonical_json([r.value for r in journal.list(COMMANDS, CONTROL)])
        manager.settings = settings
        await lifecycle.start()
        async def admitted():
            row = journal.get(COMMANDS, "agent:send", CONTROL)
            return row if row.value["state"] == "completed" else None
        completed = await eventually(admitted)
        assert completed.value["params"] is None
        receipt = await lifecycle.command("agent", "messages.send", params, request_id="send")
        assert receipt["status"] == "admitted" and receipt["event_id"]
        assert await lifecycle.command("agent", "messages.send", params, request_id="send") == receipt
        with pytest.raises(CoreError) as error:
            await lifecycle.command("agent", "messages.send", {**params, "text": "changed"}, request_id="send")
        assert error.value.code == "conflict"
        await lifecycle.command("agent", "providers.credentials.put", {"name": "key", "secret": secret}, request_id="credential")
        with pytest.raises(CoreError) as error:
            await lifecycle.command("agent", "providers.credentials.put", {"name": "key", "secret": secret}, request_id="credential")
        assert error.value.code == "result_unavailable"
        assert secret.encode() not in (tmp_path / "control.sqlite").read_bytes()
        assert (await lifecycle.status("agent"))["desired"] == "running"
    finally:
        await lifecycle.close(); journal.close()


async def test_ambiguous_dispatch_is_reconciled_by_receipt_without_effect_reexecution(tmp_path):
    journal = Journal(tmp_path / "control.sqlite")
    manager = WorkerManager(Settings(root=tmp_path / "installation", worker_timeout=5))
    lifecycle = Lifecycle(journal, manager)
    try:
        await lifecycle.create("agent"); client = await manager.start("agent")
        params = {"path": "effect.txt", "data_base64": base64.b64encode(b"real effect").decode()}
        row = lifecycle._prepare_command("agent", "files.write", params, "effect")
        lifecycle._command_state(row.id, "dispatching")
        # A real worker completes the command; simulate only the control receipt gap.
        first = await client.request("files.write", params, request_id="effect")
        lifecycle._recover_commands()
        assert journal.get(COMMANDS, row.id, CONTROL).value["state"] == "indeterminate"
        recovered = await lifecycle.command("agent", "files.write", params, request_id="effect")
        assert recovered == first and recovered["before_sha256"] is None
        stored = Journal(client.config.root / "operations.sqlite")
        try: assert len(stored.events("agent", kinds=["content.file_written"])) == 1
        finally: stored.close()
        absent = lifecycle._prepare_command("agent", "files.write", {**params, "path": "absent.txt"}, "lost")
        lifecycle._command_state(absent.id, "dispatching"); lifecycle._recover_commands()
        with pytest.raises(CoreError) as error:
            await lifecycle.command("agent", "files.write", {**params, "path": "absent.txt"}, request_id="lost")
        assert error.value.code == "result_unavailable" and not (client.config.workspace / "absent.txt").exists()
        failed = lifecycle._prepare_command("agent", "unknown.command", {}, "known_failure")
        lifecycle._command_state(failed.id, "dispatching")
        with pytest.raises(CoreError): await client.request("unknown.command", {}, request_id="known_failure")
        lifecycle._recover_commands()
        with pytest.raises(CoreError) as error:
            await lifecycle.command("agent", "unknown.command", {}, request_id="known_failure")
        assert error.value.code == "unknown_command"
        assert journal.get(COMMANDS, failed.id, CONTROL).value["state"] == "failed"
        os.killpg(manager._processes["agent"].pid, signal.SIGKILL)
        await manager._processes["agent"].wait()
        assert (await lifecycle.recover("agent"))["process"]["state"] == "running"
        assert (await lifecycle.command("agent", "files.read", {"path": "effect.txt"}))["bytes"] == len(b"real effect")
    finally:
        await lifecycle.close(); journal.close()


async def test_suspend_retains_due_schedule_and_real_watcher_wakes_worker(tmp_path):
    journal = Journal(tmp_path / "control.sqlite")
    manager = WorkerManager(Settings(root=tmp_path / "installation", worker_timeout=5))
    lifecycle = Lifecycle(journal, manager, poll_interval=.025)
    try:
        await lifecycle.create("agent")
        conversation = await lifecycle.command("agent", "conversations.create", {"title": "Scheduled"})
        client = manager.client("agent")
        await quiet(client)
        at = time.time() + 4
        core_journal = Journal(client.config.root / "operations.sqlite")
        try:
            binding = core_journal.get("consumer.conversations", conversation["id"], "agent").value["binding_id"]
            schedule = Scheduler(core_journal).schedule("agent", {"kind": "once", "at": at}, "scheduled_prompt",
                {"binding_id": binding, "text": "A persisted scheduled prompt"}, resources={"tokens": 10000, "elapsed_ms": 60000})
        finally: core_journal.close()
        suspended = await suspend_when_quiet(lifecycle, "agent")
        assert suspended["desired"] == "suspended" and suspended["next_wake_at"] == at
        assert (await manager.status("agent"))["state"] == "stopped"
        await lifecycle.start()
        async def fired():
            if (await manager.status("agent"))["state"] != "running": return None
            db = Journal(client.config.root / "operations.sqlite")
            try:
                row = db.get("schedules", schedule.id, "agent")
                events = db.events("agent", kinds=["schedule.prompt"])
                return row if row.value["occurrences"] == 1 and events else None
            finally: db.close()
        occurrence = await eventually(fired)
        assert occurrence.value["occurrences"] == 1
        assert (await lifecycle.status("agent"))["desired"] == "running"
    finally:
        await lifecycle.close(); journal.close()


async def test_archived_suspended_runtime_never_wakes_for_due_schedule_or_commands(tmp_path):
    journal = Journal(tmp_path / "control.sqlite")
    settings = Settings(root=tmp_path / "installation", worker_timeout=5)
    manager = WorkerManager(settings)
    lifecycle = Lifecycle(journal, manager, poll_interval=.025)
    try:
        await lifecycle.create("agent")
        conversation = await lifecycle.command("agent", "conversations.create", {}, request_id="room")
        client = manager.client("agent")
        db = Journal(client.config.root / "operations.sqlite")
        try:
            binding = db.get("consumer.conversations", conversation["id"], "agent").value["binding_id"]
            due = time.time() + 60
            schedule = Scheduler(db).schedule("agent", {"kind": "once", "at": due}, "scheduled_prompt",
                {"binding_id": binding, "text": "Must remain archived"}, resources={"tokens": 10000})
            await suspend_when_quiet(lifecycle, "agent")
            # Advance the persisted occurrence time while stopped, without a
            # running scheduler or long wall-clock sleep in the fixture.
            row = db.get("schedules", schedule.id, "agent")
            db.put(row.namespace, row.id, "agent", {**row.value, "next_due": time.time() - 1}, row.revision)
            lifecycle._update("agent", next_wake_at=time.time() - 1)
            generation = (await lifecycle.status("agent"))["generation"]
            archived = await lifecycle.archive("agent")
            assert archived["desired"] == archived["observed"] == "archived"
            assert archived["next_wake_at"] is None and archived["generation"] == generation
            assert (await manager.status("agent"))["state"] == "stopped"
            assert await lifecycle.archive("agent") == archived
            before = len(journal.list(COMMANDS, CONTROL))
            for operation in (lambda: lifecycle.command("agent", "overview", {}),
                    lambda: lifecycle.command("agent", "conversations.create", {}, request_id="room"),
                    lambda: lifecycle.start_agent("agent"), lambda: lifecycle.recover("agent"),
                    lambda: lifecycle.suspend("agent")):
                with pytest.raises(CoreError) as error: await operation()
                assert error.value.code == "runtime_archived"
            assert len(journal.list(COMMANDS, CONTROL)) == before
            # A legacy queued input must not defeat the durable archive fence.
            journal.put(COMMANDS, "agent:legacy", CONTROL, {"agent_id": "agent", "state": "queued",
                "params": {"conversation_id": conversation["id"], "message_id": "legacy", "text": "Do not wake"}})
            await lifecycle.close()
            manager = WorkerManager(settings)
            lifecycle = Lifecycle(journal, manager, poll_interval=.025)
            await lifecycle.start(); await lifecycle._tick(); await asyncio.sleep(.1)
            assert lifecycle._wake_tasks == {}
            await lifecycle._wake("agent")  # A wake task queued before archival also rechecks.
            assert (await lifecycle.status("agent"))["desired"] == "archived"
            assert (await manager.status("agent"))["state"] == "stopped"
            assert db.get("schedules", schedule.id, "agent").value["occurrences"] == 0
            assert db.events("agent", kinds=["schedule.prompt"]) == []
        finally:
            db.close()
    finally:
        await lifecycle.close(); journal.close()


async def test_archive_busy_rejection_and_directory_cas_are_atomic(tmp_path):
    from consumer.identity import Accounts, AgentDirectory
    journal = Journal(tmp_path / "control.sqlite")
    manager = WorkerManager(Settings(root=tmp_path / "installation", worker_timeout=5))
    lifecycle = Lifecycle(journal, manager)
    accounts = Accounts(journal, bootstrap_token="fixture-bootstrap-token")
    login = accounts.bootstrap("owner@example.test", "a-fixture-password", "fixture-bootstrap-token")
    owner = accounts.authenticate(login["token"])
    directory = AgentDirectory(journal)
    agent = directory.create(owner, "Archive atomically")
    agent_id = agent["id"]
    try:
        await lifecycle.create(agent_id)
        await lifecycle.start_agent(agent_id)
        client = manager.client(agent_id)
        await quiet(client)
        db = Journal(client.config.root / "operations.sqlite")
        try:
            pending = Scheduler(db).submit(agent_id, "accepted_work", {}, resources={"turns": 1})
            with pytest.raises(CoreError) as error: await lifecycle.archive(agent_id)
            assert error.value.code == "runtime_busy"
            assert (await client.request("overview", {}))["accepting"]
            Scheduler(db).cancel(agent_id, pending.id)
        finally:
            db.close()
        await quiet(client)
        queued = lifecycle._prepare_command(agent_id, "messages.send", {"text": "accepted before archive"}, "queued")
        with pytest.raises(CoreError) as error: await lifecycle.archive(agent_id)
        assert error.value.code == "runtime_busy"
        lifecycle._command_state(queued.id, "completed")
        changed = directory.rename(owner, agent_id, "Concurrent rename", agent["revision"])
        with pytest.raises(CoreError) as error:
            await lifecycle.archive(agent_id, finalize=lambda: directory.archive(owner, agent_id, agent["revision"]))
        assert error.value.code == "conflict"
        assert not directory.authorize(owner, agent_id)["archived"]
        assert journal.get(RUNTIMES, agent_id, CONTROL).value["desired"] == "running"
        assert (await client.request("overview", {}))["accepting"]
        def aborted_finalizer():
            directory.archive(owner, agent_id, changed["revision"])
            raise CoreError("archive_aborted", "Transaction fixture aborts after metadata mutation")
        with pytest.raises(CoreError) as error:
            await lifecycle.archive(agent_id, finalize=aborted_finalizer)
        assert error.value.code == "archive_aborted"
        assert not directory.authorize(owner, agent_id)["archived"]
        assert (await client.request("overview", {}))["accepting"]
        async def attempt():
            try: return await lifecycle.archive(agent_id, finalize=lambda: directory.archive(owner, agent_id, changed["revision"]))
            except CoreError as exc:
                if exc.code != "runtime_busy": raise
                return None
        result = await eventually(attempt)
        assert result["desired"] == result["observed"] == "archived"
        assert directory.authorize(owner, agent_id)["archived"]
        assert (await manager.status(agent_id))["state"] == "stopped"
        await lifecycle.create("cold")
        with pytest.raises(CoreError): await lifecycle.archive("cold", finalize=42)
        assert (await lifecycle.archive("cold"))["desired"] == "archived"
        assert await lifecycle.archive("cold", finalize=lambda: None)
    finally:
        await lifecycle.close(); journal.close()


async def test_cancelled_archive_commit_remains_fenced_and_retry_finishes_stop(tmp_path):
    import threading
    journal = Journal(tmp_path / "control.sqlite")
    manager = WorkerManager(Settings(root=tmp_path / "installation", worker_timeout=5))
    lifecycle = Lifecycle(journal, manager)
    entered, release = threading.Event(), threading.Event()
    try:
        await lifecycle.create("agent"); await lifecycle.start_agent("agent")
        client = manager.client("agent")
        await quiet(client)
        def finalizer():
            journal.put("archive_fixture", "agent", CONTROL, {"archived": True})
            entered.set()
            assert release.wait(5)
        async def attempt():
            while True:
                try: return await lifecycle.archive("agent", finalize=finalizer)
                except CoreError as error:
                    if error.code != "runtime_busy": raise
                    await asyncio.sleep(.025)
        pending = asyncio.create_task(attempt())
        async def committing(): return entered.is_set()
        await eventually(committing)
        pending.cancel(); await asyncio.sleep(.025)
        assert not pending.done()
        release.set()
        with pytest.raises(asyncio.CancelledError): await pending
        row = journal.get(RUNTIMES, "agent", CONTROL)
        assert row.value["desired"] == "archived" and row.value["observed"] == "stopping"
        assert journal.get("archive_fixture", "agent", CONTROL).value["archived"]
        assert (await client.request("_worker.status", {}))["draining"]
        with pytest.raises(CoreError) as error: await client.request("overview", {})
        assert error.value.code == "worker_draining"
        assert (await lifecycle.archive("agent"))["observed"] == "archived"
        assert (await manager.status("agent"))["state"] == "stopped"
    finally:
        release.set()
        await lifecycle.close(); journal.close()


async def test_busy_suspend_cancellation_failure_receipts_and_lifecycle_controls(tmp_path):
    journal = Journal(tmp_path / "control.sqlite")
    manager = WorkerManager(Settings(root=tmp_path / "installation", worker_timeout=5))
    lifecycle = Lifecycle(journal, manager)
    try:
        with pytest.raises(CoreError): Lifecycle(journal, manager, poll_interval=0)
        first = await lifecycle.create("agent")
        assert await lifecycle.create("agent") == first
        with pytest.raises(CoreError): await lifecycle.status("missing")
        await lifecycle.start_agent("agent")
        client = manager.client("agent"); await quiet(client)
        db = Journal(client.config.root / "operations.sqlite")
        try:
            hold = Scheduler(db).submit("agent", "accepted_external_work", {}, resources={"turns": 1})
            with pytest.raises(CoreError) as error: await lifecycle.suspend("agent")
            assert error.value.code == "runtime_busy" and error.value.details["queued_work"]
            assert (await client.request("overview", {}))["accepting"]
            Scheduler(db).cancel("agent", hold.id)
        finally: db.close()
        with pytest.raises(CoreError): await lifecycle.command("agent", "_worker.status", {})
        with pytest.raises(CoreError):
            await lifecycle.command("agent", "messages.send", {"secret": "not-admitted"})
        assert all("not-admitted" not in canonical_json(r.value) for r in journal.list(COMMANDS, CONTROL))
        for _ in range(2):
            with pytest.raises(CoreError) as error:
                await lifecycle.command("agent", "unknown.command", {}, request_id="bad")
            assert error.value.code == "unknown_command"
        await client.request("_worker.quiesce", {})
        with pytest.raises(CoreError) as error:
            await lifecycle.command("agent", "overview", {}, request_id="paused")
        assert error.value.code == "worker_draining"
        assert journal.get(COMMANDS, "agent:paused", CONTROL).value["state"] == "queued"
        await client.request("_worker.resume", {})
        assert (await lifecycle.command("agent", "overview", {}, request_id="paused"))["agent_id"] == "agent"
        process = manager._processes["agent"]
        os.kill(process.pid, signal.SIGSTOP)
        pending = asyncio.create_task(lifecycle.command("agent", "overview", {}, request_id="cancelled"))
        try:
            async def dispatched():
                row = journal.get(COMMANDS, "agent:cancelled", CONTROL)
                return row if row and row.value["state"] == "dispatching" else None
            await eventually(dispatched)
            await asyncio.sleep(.05)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError): await pending
            assert journal.get(COMMANDS, "agent:cancelled", CONTROL).value["state"] == "indeterminate"
            state = await lifecycle.status("agent")
            assert state["readiness_error"]["code"] == "worker_indeterminate"
        finally:
            os.kill(process.pid, signal.SIGCONT)
        await quiet(client)
        await suspend_when_quiet(lifecycle, "agent")
        assert (await lifecycle.suspend("agent"))["desired"] == "suspended"
        await lifecycle.start(); assert await lifecycle.start() is lifecycle
        assert (await lifecycle.delete("agent"))["desired"] == "deleted"
        with pytest.raises(CoreError): await lifecycle.create("agent")
        with pytest.raises(CoreError): await lifecycle.status("agent")
        await lifecycle.create("cold")
        assert (await suspend_when_quiet(lifecycle, "cold"))["desired"] == "suspended"
    finally:
        await lifecycle.close(); await lifecycle.close(); journal.close()
    with pytest.raises(CoreError): await lifecycle.start()


async def test_nonresponsive_real_process_is_forcibly_reaped(tmp_path):
    manager = WorkerManager(Settings(root=tmp_path / "installation", worker_timeout=5))
    try:
        await manager.start("agent")
        process = manager._processes["agent"]
        os.kill(process.pid, signal.SIGSTOP)
        stopped = await manager.stop("agent")
        assert stopped["state"] == "stopped" and process.returncode == -signal.SIGKILL
    finally: await manager.close()


async def test_parent_death_terminates_real_worker_and_new_owner_recovers(tmp_path):
    import json
    root, marker = tmp_path / "installation", tmp_path / "ready.json"
    driver = """
import asyncio,json,sys
from pathlib import Path
from consumer.config import Settings
from consumer.workers import WorkerManager
async def main():
    manager=WorkerManager(Settings(root=Path(sys.argv[1]),worker_timeout=5))
    client=await manager.start('agent')
    await client.request('files.write', {'path':'survives.txt','data_base64':'ZHVyYWJsZQ=='})
    Path(sys.argv[2]).write_text(json.dumps({'pid':manager._processes['agent'].pid}))
    await asyncio.Event().wait()
asyncio.run(main())
"""
    parent = await asyncio.create_subprocess_exec(sys.executable, "-c", driver, str(root), str(marker),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    manager = None
    try:
        async def ready():
            if parent.returncode is not None:
                raise AssertionError((await parent.stderr.read()).decode()[-2048:])
            return marker.exists()
        await eventually(ready)
        pid = json.loads(marker.read_text())["pid"]
        parent.kill(); await parent.wait()
        async def gone():
            path = Path(f"/proc/{pid}/stat")
            return not path.exists() or path.read_text().split()[2] == "Z"
        await eventually(gone)
        manager = WorkerManager(Settings(root=root, worker_timeout=5))
        client = await manager.start("agent")
        assert (await client.request("files.read", {"path": "survives.txt"}))["bytes"] == 7
        assert (await manager.status("agent"))["pid"] != pid
    finally:
        if parent.returncode is None: parent.kill(); await parent.wait()
        if manager is not None: await manager.close()
