"""Real worker/native portability plus bounded adversarial archive fixtures."""
import asyncio
import base64
from contextlib import asynccontextmanager
from dataclasses import replace
import hashlib
import io
from pathlib import Path
import stat
import sys
import threading
import time
import zipfile

import pytest

from agent.protocol import CoreError, canonical_json, strict_loads
from agent.scheduler import Scheduler
from agent.state import Journal
from consumer.config import Settings
from consumer.identity import Accounts, AgentDirectory
from consumer.lifecycle import CONTROL, RUNTIMES, Lifecycle
from consumer.portability import FORMAT, Portability, _unpack
from consumer.workers import WorkerManager
from tests.consumer_integration.test_lifecycle import eventually, quiet, suspend_when_quiet


@asynccontextmanager
async def installation(path):
    journal = Journal(path / "control.sqlite")
    accounts = Accounts(journal, bootstrap_token="test-bootstrap-token", allow_signup=True)
    session = accounts.bootstrap("owner@example.test", "a-test-password", "test-bootstrap-token")
    owner = accounts.authenticate(session["token"])
    other = accounts.authenticate(accounts.signup("other@example.test", "another-password")["token"])
    manager = WorkerManager(Settings(root=path / "installation", worker_timeout=10))
    lifecycle = Lifecycle(journal, manager, poll_interval=.025)
    directory = AgentDirectory(journal)
    portability = Portability(journal, lifecycle, manager)
    try:
        yield journal, owner, other, directory, lifecycle, manager, portability
    finally:
        await lifecycle.close(); journal.close()


async def backup_when_idle(portability, owner, agent_id):
    async def attempt():
        try: return await portability.backup(owner, agent_id)
        except CoreError as error:
            if error.code != "runtime_busy": raise
            return None
    return await eventually(attempt)


def repack(backup, mutate=None, *, files=None, extra=None):
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(backup["archive_base64"]))) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    manifest = strict_loads(entries.pop("manifest.json"))
    if files is not None: files(entries)
    manifest["files"] = {name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()} for name, data in entries.items()}
    if mutate: mutate(manifest)
    entries["manifest.json"] = canonical_json(manifest).encode()
    with io.BytesIO() as output:
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in entries.items(): archive.writestr(name, data)
            if extra: extra(archive)
        raw = output.getvalue()
    return {"archive_base64": base64.b64encode(raw).decode(), "sha256": hashlib.sha256(raw).hexdigest()}


async def test_actual_private_worker_backup_restores_content_memory_history_without_authority(tmp_path):
    async with installation(tmp_path) as (control, owner, other, directory, lifecycle, manager, portability):
        agent = directory.create(owner, "Original")
        agent_id = agent["id"]; await lifecycle.create(agent_id)
        async def command(method, params): return await lifecycle.command(agent_id, method, params)
        room = await command("conversations.create", {"title": "Original conversation"})
        source = await command("attachments.upload", {"name": "source.html", "media_type": "text/html",
            "data_base64": base64.b64encode(b'<p>Portable factual source</p><script>not source text</script>').decode(), "operation_id": "source"})
        document = (await command("knowledge.add", {"attachment_id": source["id"], "title": "Portable source", "operation_id": "document"}))["document"]
        await command("knowledge.add", {"attachment_id": source["id"], "title": "Revised source", "operation_id": "revision",
            "document_id": document["id"], "expected_revision": document["revision"]})
        await command("files.write", {"path": "source.txt", "data_base64": base64.b64encode(b"persistent workspace").decode()})
        await command("skills.install", {"manifest": {"id": "read", "version": "1", "description": "Read with care",
            "triggers": ["read"], "steps": ["Inspect supplied sources"], "required_tools": ["workspace.read"]}})
        secret = "never-export-managed-provider-secret"
        credential = await command("providers.credentials.put", {"name": "key", "secret": secret})
        connection = await command("connections.configure", {"id": "remote", "kind": "http", "endpoint": "https://example.test/",
            "credential_ref": credential["reference"]})
        await command("connections.grant", {"id": "remote", "operations": ["fetch"], "expected_revision": connection["revision"]})
        receipt = await command("messages.send", {"conversation_id": room["id"], "message_id": "old_input", "text": "Preserve this historical input"})
        client = manager.client(agent_id)
        await quiet(client)
        db = Journal(client.config.root / "operations.sqlite")
        try:
            binding = db.get("consumer.conversations", room["id"], agent_id).value["binding_id"]
            Scheduler(db).schedule(agent_id, {"kind": "once", "at": time.time() + 3600}, "scheduled_prompt", {"binding_id": binding, "text": "Never auto replay"})
            db.put("consumer.permission_decisions", "old_grant", agent_id, {"state": "approved", "expires_at": time.time() + 3600})
        finally: db.close()
        backup = await backup_when_idle(portability, owner, agent_id)
        assert backup["format"] == FORMAT and backup["bytes"] == len(base64.b64decode(backup["archive_base64"]))
        manifest, entries, history, memory = _unpack(backup["archive_base64"], backup["sha256"])
        assert secret.encode() not in b"".join(entries.values())
        assert not any("private" in key or "plugins/" in key or ".sqlite" in key for key in entries)
        assert memory["canonical"] and memory["retractions"]
        assert (await manager.status(agent_id))["state"] == "running"
        with pytest.raises(CoreError): await portability.backup(other, agent_id)
        restored = await portability.restore(owner, name="Restored", archive_base64=backup["archive_base64"], sha256=backup["sha256"])
        target = restored["id"]
        assert target != agent_id and directory.authorize(owner, target)["owner_id"] == owner.id
        assert (await manager.status(target))["state"] == "stopped"
        with pytest.raises(CoreError): directory.authorize(other, target)
        new = await manager.start(target)
        assert base64.b64decode((await new.request("files.read", {"path": "source.txt"}))["data_base64"]) == b"persistent workspace"
        attachment = await new.request("attachments.read", {"id": source["id"]})
        assert base64.b64decode(attachment["data_base64"]).startswith(b"<p>Portable")
        knowledge = await new.request("knowledge.get", {"id": document["id"]})
        assert knowledge["revision"] == 2 and knowledge["title"] == "Revised source"
        assert (await new.request("knowledge.get", {"id": document["id"], "revision": 1}))["title"] == "Portable source"
        assert (await new.request("memory.query", {"text": "Portable factual"}))["data"]["records"]
        assert await new.request("connections.list", {}) == []
        assert await new.request("providers.credentials.list", {}) == []
        assert all(row["state"] == "withdrawn" for row in await new.request("skills.list", {}))
        transcript = await new.request("conversations.get", {"id": room["id"]})
        assert transcript["portable_import"]["authority"] == "imported_history"
        assert transcript["messages"][0]["status"] == "historical"
        assert transcript["turns"][0]["portable_import"]
        with pytest.raises(CoreError) as error:
            await new.request("messages.send", {"conversation_id": room["id"], "message_id": "old_input", "text": "Preserve this historical input"})
        assert error.value.code == "historical_message"
        restored_db = Journal(new.config.root / "operations.sqlite")
        try:
            for namespace in ("schedules", "work_claims", "consumer.permission_decisions", "consumer.workflow_runs", "subagents", "plugins"):
                assert restored_db.list(namespace, target) == []
            assert all(row.value["state"] == "cancelled" and row.value["capabilities"] == [] for row in restored_db.list("work", target)
                if row.value["kind"] == "imported_history")
            original = next(e for e in history["events"] if e["kind"] == "user.prompt")
            imported = restored_db.event(target, original["id"])
            assert imported.source == "portable.import" and imported.payload == original["payload"]
            roots = restored_db.get("profiles", target, target).value
            assert roots["workspace_root"] == str(new.config.workspace)
            assert all(str(new.config.workspace) in path for path in roots["resource_roots"].values())
        finally: restored_db.close()
        original_history = await new.request("artifacts.read", {"id": restored["restore"]["history_artifact_ids"][0], "limit": 4194304})
        assert base64.b64decode(original_history["data_base64"]) == entries["journal.json"]
        await quiet(new)
        await lifecycle.archive(target)
        assert (await backup_when_idle(portability, owner, target))["format"] == FORMAT
        assert (await lifecycle.status(target))["desired"] == "archived"


@pytest.fixture
async def empty_backup(tmp_path):
    async with installation(tmp_path) as context:
        _, owner, _, directory, lifecycle, _, portability = context
        agent = directory.create(owner, "Empty")
        await lifecycle.create(agent["id"])
        yield context, agent, await portability.backup(owner, agent["id"])


@pytest.mark.parametrize("mutation", [
    lambda m: m.update(format="future/2"), lambda m: m.update(relocation={"workspace": "/etc"}),
    lambda m: m.update(source_scope="../tenant"), lambda m: m.update(created_at=-1),
    lambda m: m.update(extra=True), lambda m: m["files"]["journal.json"].update(sha256="0" * 64),
    lambda m: m.update(files={}), lambda m: m.update(artifacts=[{"id": "missing", "media_type": "text/plain"}]),
])
async def test_manifest_schema_hash_and_restore_failures_leave_no_visible_agent(empty_backup, mutation):
    (_, owner, _, directory, _, _, portability), _, backup = empty_backup
    before = directory.list(owner)
    damaged = repack(backup, mutation)
    with pytest.raises(CoreError): await portability.restore(owner, name="Invalid", **damaged)
    assert directory.list(owner) == before


@pytest.mark.parametrize("path", ["../escape", "/absolute", "workspace/../escape", "workspace/.sturgis/state", "workspace/a\\b", "workspace//alias", "workspace/directory/"])
async def test_archive_paths_fail_before_any_content_is_written(empty_backup, path):
    (_, owner, _, _, _, manager, portability), _, backup = empty_backup
    bad = repack(backup, files=lambda files: files.update({path: b"bad"}))
    with pytest.raises(CoreError): await portability.restore(owner, name="Unsafe", **bad)
    assert not (manager.settings.root / "escape").exists()


@pytest.mark.parametrize("kind", [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFSOCK])
async def test_archive_special_files_and_duplicate_names_are_rejected(empty_backup, kind):
    (_, owner, _, _, _, _, portability), _, backup = empty_backup
    def extra(archive):
        item = zipfile.ZipInfo("workspace/special"); item.external_attr = (kind | 0o600) << 16
        archive.writestr(item, b"target")
    with pytest.raises(CoreError): await portability.restore(owner, name="Unsafe", **repack(backup, extra=extra))
    with pytest.warns(UserWarning): duplicate = repack(backup, extra=lambda archive: archive.writestr("journal.json", b"{}"))
    with pytest.raises(CoreError): await portability.restore(owner, name="Duplicate", **duplicate)


@pytest.mark.parametrize("encoded,digest", [(None, "0" * 64), ("!", "0" * 64), ("eA==", "bad"),
    ("eA==", "0" * 64), ("eA==", hashlib.sha256(b"x").hexdigest())])
async def test_transport_and_corrupt_zip_are_rejected(empty_backup, encoded, digest):
    (_, owner, _, _, _, _, portability), _, _ = empty_backup
    with pytest.raises(CoreError): await portability.restore(owner, name="Invalid", archive_base64=encoded, sha256=digest)


async def test_snapshot_lock_cancellation_and_filesystem_bounds(empty_backup, monkeypatch):
    import consumer.portability as module
    (_, owner, _, _, lifecycle, manager, portability), agent, backup = empty_backup
    client = await manager.start(agent["id"])
    await quiet(client)
    entered, release = threading.Event(), threading.Event()
    original = module._export
    def slow(*args):
        entered.set(); assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(module, "_export", slow)
    task = asyncio.create_task(backup_when_idle(portability, owner, agent["id"]))
    async def copying(): return entered.is_set()
    await eventually(copying)
    command = asyncio.create_task(lifecycle.command(agent["id"], "overview", {}))
    await asyncio.sleep(.05)
    assert not command.done() and (await manager.status(agent["id"]))["state"] == "stopped"
    task.cancel(); await asyncio.sleep(.025); assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError): await task
    assert (await command)["agent_id"] == agent["id"]
    monkeypatch.setattr(module, "_export", original)
    path = client.config.workspace / "link"; path.symlink_to(client.config.private_root)
    with pytest.raises(CoreError) as error: await backup_when_idle(portability, owner, agent["id"])
    assert error.value.code == "unsafe_backup_path"
    path.unlink()
    (client.config.workspace / "big").write_bytes(b"x" * 1025)
    monkeypatch.setattr(module, "MAX_FILE", 1024)
    with pytest.raises(CoreError): await backup_when_idle(portability, owner, agent["id"])


async def test_logical_memory_validation_and_missing_worker_cleanup(empty_backup):
    (_, owner, _, directory, _, manager, portability), _, backup = empty_backup
    def malformed(files):
        body = strict_loads(files["memory.json"])
        body["canonical"] = [{"id": "bad", "revision": 1, "content": "malformed"}]
        files["memory.json"] = canonical_json(body).encode()
    before = directory.list(owner)
    with pytest.raises(CoreError): await portability.restore(owner, name="Bad memory", **repack(backup, files=malformed))
    assert directory.list(owner) == before
    manager.settings = replace(manager.settings, native_binary=manager.settings.root / "missing")
    with pytest.raises(CoreError) as error:
        await portability.restore(owner, name="No worker", archive_base64=backup["archive_base64"], sha256=backup["sha256"])
    assert error.value.code == "memory_unavailable" and directory.list(owner) == before


@pytest.mark.parametrize("field,bound", [("MAX_FILES", 2), ("MAX_TOTAL", 128), ("MAX_FILE", 64), ("MAX_ARCHIVE", 64)])
async def test_independent_archive_inflation_file_count_and_transport_bounds(empty_backup, monkeypatch, field, bound):
    import consumer.portability as module
    (_, owner, _, _, _, _, portability), _, backup = empty_backup
    monkeypatch.setattr(module, field, bound)
    with pytest.raises(CoreError):
        await portability.restore(owner, name="Bounded", archive_base64=backup["archive_base64"], sha256=backup["sha256"])


async def test_current_derived_projection_vectors_revision_retraction_and_source_history_survive(empty_backup):
    from agent.memory import NativeMemoryClient
    (_, owner, _, _, lifecycle, manager, portability), agent, _ = empty_backup
    client = await manager.start(agent["id"])
    await quiet(client)
    await suspend_when_quiet(lifecycle, agent["id"])
    partition = hashlib.sha256(agent["id"].encode()).hexdigest()
    memory = NativeMemoryClient(client.config.workspace / ".sturgis/profiles" / partition / "memory/memory.sqlite")
    try:
        await memory.start()
        record = {"id": "canonical", "kind": "observation", "content": "First observed value", "source_time": 1,
            "observed_at": 2, "valid_from": 1, "valid_to": None, "concepts": ["observation"],
            "sources": [{"id": "source", "revision": 1, "start": 0, "end": 5}],
            "embedding": {"model": "test-vector", "vector": [1.0, 0.0]}}
        assert (await memory.ingest(agent["id"], record, operation_id="one"))["status"] == "ok"
        assert (await memory.ingest(agent["id"], {**record, "content": "Corrected observed value"}, expected_revision=1, operation_id="two"))["status"] == "ok"
        parent = (await memory.request(agent["id"], "ping", {}))["data"]["current_generation"]
        begin = await memory.request(agent["id"], "begin_generation", {"operation_id": "begin", "expected_parent": parent})
        generation = begin["data"]["generation"]
        assert (await memory.request(agent["id"], "stage", {"operation_id": "stage", "generation": generation,
            "records": [{**record, "id": "derived", "kind": "belief", "content": "A derived observation"}]}))["status"] == "ok"
        assert (await memory.request(agent["id"], "publish", {"operation_id": "publish", "generation": generation, "expected_parent": parent}))["status"] == "ok"
        await memory.request(agent["id"], "retract", {"operation_id": "retract", "id": "canonical", "revision": 1, "reason": "Corrected"})
    finally: await memory.close()
    backup = await portability.backup(owner, agent["id"])
    restored = await portability.restore(owner, name="Derived", archive_base64=backup["archive_base64"], sha256=backup["sha256"])
    target = await manager.start(restored["id"])
    historical = await target.request("memory.get", {"id": "canonical", "revision": 1})
    assert historical["data"]["record"]["retracted"]
    results = await target.request("memory.query", {"embedding": {"model": "test-vector", "vector": [1.0, 0.0]}})
    records = {row["record"]["id"]: row for row in results["data"]["records"]}
    assert records["canonical"]["record"]["revision"] == 2 and "derived" in records
    assert records["derived"]["vector_score"] == pytest.approx(1)


async def test_restore_reservation_fences_commands_and_cancelled_import_is_removed(empty_backup, monkeypatch):
    (_, owner, _, directory, lifecycle, manager, portability), _, backup = empty_backup
    entered, release = threading.Event(), threading.Event()
    original = portability._restore_content
    def slow(*args):
        entered.set(); assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(portability, "_restore_content", slow)
    before = {row["id"] for row in directory.list(owner)}
    task = asyncio.create_task(portability.restore(owner, name="Pending", archive_base64=backup["archive_base64"], sha256=backup["sha256"]))
    async def importing(): return entered.is_set()
    await eventually(importing)
    target = next(row["id"] for row in directory.list(owner) if row["id"] not in before)
    with pytest.raises(CoreError) as error:
        lifecycle._prepare_command(target, "overview", {}, "blocked")
    assert error.value.code == "runtime_restoring"
    task.cancel(); await asyncio.sleep(.025); assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError): await task
    assert {row["id"] for row in directory.list(owner)} == before
    assert not manager.config(target).root.parent.exists()


@pytest.mark.parametrize("boundary", ["reserve", "create", "publish"])
async def test_cancelled_restore_settles_reservation_directory_and_publication_writes(empty_backup, monkeypatch, boundary):
    (_, owner, _, directory, lifecycle, manager, portability), _, backup = empty_backup
    entered, release = threading.Event(), threading.Event()
    async_entered, async_release = asyncio.Event(), asyncio.Event()
    before = {row["id"] for row in directory.list(owner)}
    if boundary == "reserve":
        original = portability.agents.create
        def delayed(*args):
            result = original(*args)
            entered.set(); assert release.wait(5)
            return result
        monkeypatch.setattr(portability.agents, "create", delayed)
    elif boundary == "create":
        original = manager.create
        async def delayed(*args):
            result = await original(*args)
            async_entered.set(); await async_release.wait()
            return result
        monkeypatch.setattr(manager, "create", delayed)
    else:
        original = lifecycle._update
        def delayed(*args, **kwargs):
            result = original(*args, **kwargs)
            if kwargs.get("desired") == "stopped":
                entered.set(); assert release.wait(5)
            return result
        monkeypatch.setattr(lifecycle, "_update", delayed)
    task = asyncio.create_task(portability.restore(owner, name="Cancelled", archive_base64=backup["archive_base64"], sha256=backup["sha256"]))
    try:
        async def at_boundary(): return entered.is_set() or async_entered.is_set()
        await eventually(at_boundary)
        task.cancel(); await asyncio.sleep(.025)
        assert not task.done()
    finally:
        release.set(); async_release.set()
    with pytest.raises(asyncio.CancelledError): await task
    targets = [row for row in lifecycle.journal.list(RUNTIMES, CONTROL) if row.id not in before]
    assert len(targets) == 1
    if boundary == "publish":
        assert targets[0].value["desired"] == "stopped"
        assert {row["id"] for row in directory.list(owner)} == before | {targets[0].id}
        replay = await portability.restore(owner, name="Cancelled", archive_base64=backup["archive_base64"], sha256=backup["sha256"],
            request_id=targets[0].value["restore_request"]["request_id"])
        assert replay["id"] == targets[0].id and manager.config(replay["id"]).root.parent.exists()
    else:
        assert {row["id"] for row in directory.list(owner)} == before
        assert targets[0].value["desired"] == "deleted"
        assert not manager.config(targets[0].id).root.parent.exists()


async def test_owner_crash_before_restore_publication_is_discarded_on_startup(empty_backup, tmp_path):
    (_, _, _, _, _, _, _), _, backup = empty_backup
    target_root = tmp_path / "crashed-installation"
    bundle = tmp_path / "archive.json"; bundle.write_text(canonical_json(backup))
    marker = tmp_path / "crash-target"
    driver = r'''
import asyncio, os, sys
from pathlib import Path
from agent.protocol import strict_loads
from agent.state import Journal
from consumer.config import Settings
from consumer.identity import Accounts, AgentDirectory
from consumer.lifecycle import Lifecycle
from consumer.portability import Portability
from consumer.workers import WorkerManager
async def run():
    root = Path(sys.argv[1]); root.mkdir()
    journal = Journal(root / 'control.sqlite')
    accounts = Accounts(journal, bootstrap_token='crash-bootstrap-token')
    owner = accounts.authenticate(accounts.bootstrap('owner@example.test', 'a-test-password', 'crash-bootstrap-token')['token'])
    manager = WorkerManager(Settings(root=root / 'runtime'))
    lifecycle = Lifecycle(journal, manager)
    keep = AgentDirectory(journal).create(owner, 'Keep existing agent')
    await lifecycle.create(keep['id'])
    portability = Portability(journal, lifecycle, manager)
    original = portability._restore_memory
    async def crash_after_native(config, memory):
        await original(config, memory)
        Path(sys.argv[3]).write_text(config.agent_id)
        os._exit(41)
    portability._restore_memory = crash_after_native
    archive = strict_loads(Path(sys.argv[2]).read_bytes())
    await portability.restore(owner, name='Interrupted import', archive_base64=archive['archive_base64'], sha256=archive['sha256'])
asyncio.run(run())
'''
    process = await asyncio.create_subprocess_exec(sys.executable, "-c", driver, str(target_root), str(bundle), str(marker),
        cwd=Path(__file__).resolve().parents[2], stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    output, error = await asyncio.wait_for(process.communicate(), 15)
    assert process.returncode == 41, error.decode()
    target = marker.read_text()
    journal = Journal(target_root / "control.sqlite")
    manager = WorkerManager(Settings(root=target_root / "runtime"))
    lifecycle = Lifecycle(journal, manager)
    portability = Portability(journal, lifecycle, manager)
    try:
        accounts = Accounts(journal, bootstrap_token="crash-bootstrap-token")
        owner = accounts.authenticate(accounts.login("owner@example.test", "a-test-password")["token"])
        assert journal.get(RUNTIMES, target, CONTROL).value["desired"] == "restoring"
        with pytest.raises(CoreError) as denied: await lifecycle.start_agent(target)
        assert denied.value.code == "runtime_restoring"
        config = manager.config(target)
        assert list(config.workspace.rglob("memory.sqlite"))
        assert await portability.recover() == {"discarded": [target]}
        assert not config.root.parent.exists()
        assert [row["name"] for row in AgentDirectory(journal).list(owner)] == ["Keep existing agent"]
        assert await portability.recover() == {"discarded": []}
        await lifecycle.start()
        with pytest.raises(CoreError): await portability.recover()
    finally:
        await lifecycle.close(); journal.close()
