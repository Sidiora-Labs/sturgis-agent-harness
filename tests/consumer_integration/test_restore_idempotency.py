"""Actual HTTP lost acknowledgments and owner crashes at restore publication."""
import asyncio
import base64
from pathlib import Path
import sys
import time

import httpx
import pytest

from agent.protocol import CoreError, canonical_json, strict_loads
from agent.state import Journal
from consumer.config import Settings
from consumer.cli import PrivateStore, main
from consumer.identity import Accounts, AgentDirectory
from consumer.lifecycle import CONTROL, RUNTIMES, Lifecycle
from consumer.portability import Portability, RESTORES
from consumer.workers import WorkerManager
from tests.consumer.test_api import PASSWORD, agent, command
from tests.consumer.test_portability import empty_backup
from tests.consumer_integration.test_portability_http import portable_http, backup_when_idle


async def test_real_lost_http_ack_replays_same_agent_and_scopes_identity_to_account(portable_http):
    client, session = portable_http
    source = await agent(client, "Original")
    await command(client, source["id"], "files.write", {"path": "source.txt", "data_base64": base64.b64encode(b"real portable content").decode()})
    archive = await backup_when_idle(client, source["id"])
    body = {"name": "Restored", "archive_base64": archive["archive_base64"], "sha256": archive["sha256"], "request_id": "lost_ack"}
    for invalid_id in (None, "", "../restore"):
        invalid = await client.post("/api/v1/agents/restore", json={**body, "request_id": invalid_id})
        assert invalid.status_code == 400 and invalid.json()["error"]["code"] == "invalid_identifier"
    payload = canonical_json(body).encode()
    reader, writer = await asyncio.open_connection(client.base_url.host, client.base_url.port)
    writer.write((f"POST /api/v1/agents/restore HTTP/1.1\r\nHost: {client.base_url.host}:{client.base_url.port}\r\n"
        f"Authorization: Bearer {session['token']}\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\n"
        "Connection: close\r\n\r\n").encode() + payload)
    await writer.drain()
    headers = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 20)
    assert headers.startswith(b"HTTP/1.1 200"), headers
    # The first caller loses the response body and therefore never learns its ID.
    writer.close(); await writer.wait_closed()
    replay = await client.post("/api/v1/agents/restore", json=body)
    assert replay.status_code == 200, replay.text
    restored = replay.json()["data"]
    assert restored["restore"]["request_id"] == "lost_ack"
    replay_again = await client.post("/api/v1/agents/restore", json=body)
    assert replay_again.json()["data"] == restored
    assert len((await client.get("/api/v1/agents")).json()["data"]["items"]) == 2
    file = await command(client, restored["id"], "files.read", {"path": "source.txt"})
    assert base64.b64decode(file["data_base64"]) == b"real portable content"
    mismatch = await client.post("/api/v1/agents/restore", json={**body, "name": "Changed"})
    assert mismatch.status_code == 409 and mismatch.json()["error"]["code"] == "conflict"
    other_archive = await backup_when_idle(client, source["id"])
    mismatch = await client.post("/api/v1/agents/restore", json={**body,
        "archive_base64": other_archive["archive_base64"], "sha256": other_archive["sha256"]})
    assert mismatch.status_code == 409
    concurrent = await asyncio.gather(*(client.post("/api/v1/agents/restore", json={**body, "request_id": "concurrent"}) for _ in range(2)))
    assert all(response.status_code == 200 for response in concurrent)
    assert concurrent[0].json()["data"] == concurrent[1].json()["data"]
    assert len((await client.get("/api/v1/agents")).json()["data"]["items"]) == 3
    async with httpx.AsyncClient(base_url=client.base_url, timeout=20) as other:
        signup = await other.post("/api/v1/auth/signup", json={"email": "second@example.test", "password": PASSWORD})
        other.headers["Authorization"] = "Bearer " + signup.json()["data"]["token"]
        independent = await other.post("/api/v1/agents/restore", json=body)
        assert independent.status_code == 200, independent.text
        result = independent.json()["data"]
        assert result["owner_id"] != restored["owner_id"] and result["id"] != restored["id"]
        assert (await other.get(f"/api/v1/agents/{restored['id']}")).status_code == 404


@pytest.mark.parametrize("published", [False, True])
async def test_actual_owner_crash_restore_receipt_survives_atomic_publication(empty_backup, tmp_path, published):
    _, _, archive = empty_backup
    installation = tmp_path / "restart"; bundle = tmp_path / "archive.json"; marker = tmp_path / "target"
    bundle.write_text(canonical_json(archive))
    driver = r'''
import asyncio, os, sys
from pathlib import Path
from agent.protocol import strict_loads
from agent.state import Journal
from consumer.config import Settings
from consumer.identity import Accounts
from consumer.lifecycle import Lifecycle
from consumer.portability import Portability
from consumer.workers import WorkerManager
async def run():
    root = Path(sys.argv[1]); root.mkdir()
    journal = Journal(root / 'control.sqlite')
    accounts = Accounts(journal, bootstrap_token='restart-bootstrap-token')
    owner = accounts.authenticate(accounts.bootstrap('owner@example.test','restart-test-password','restart-bootstrap-token')['token'])
    manager = WorkerManager(Settings(root=root / 'runtime'))
    lifecycle = Lifecycle(journal, manager)
    portability = Portability(journal, lifecycle, manager)
    if sys.argv[4] == 'yes':
        original = portability._publish
        def crash(owner_id, request_id, result):
            original(owner_id, request_id, result)
            Path(sys.argv[3]).write_text(result['id'])
            os._exit(42)
        portability._publish = crash
    else:
        original = portability._restore_memory
        async def crash(config, memory):
            await original(config, memory)
            Path(sys.argv[3]).write_text(config.agent_id)
            os._exit(42)
        portability._restore_memory = crash
    archive = strict_loads(Path(sys.argv[2]).read_bytes())
    await portability.restore(owner,name='Restored',archive_base64=archive['archive_base64'],sha256=archive['sha256'],request_id='crash_request')
asyncio.run(run())
'''
    process = await asyncio.create_subprocess_exec(sys.executable, "-c", driver, str(installation), str(bundle), str(marker),
        "yes" if published else "no", cwd=Path(__file__).resolve().parents[2],
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, error = await asyncio.wait_for(process.communicate(), 20)
    assert process.returncode == 42, error.decode()
    target = marker.read_text()
    journal = Journal(installation / "control.sqlite")
    manager = WorkerManager(Settings(root=installation / "runtime")); lifecycle = Lifecycle(journal, manager)
    portability = Portability(journal, lifecycle, manager)
    try:
        accounts = Accounts(journal, bootstrap_token="restart-bootstrap-token")
        owner = accounts.authenticate(accounts.login("owner@example.test", "restart-test-password")["token"])
        assert await portability.recover() == {"discarded": [] if published else [target]}
        params = {"name": "Restored", "archive_base64": archive["archive_base64"], "sha256": archive["sha256"], "request_id": "crash_request"}
        if published:
            original = journal.get(RESTORES, "crash_request", owner.id).value["result"]
            assert await portability.restore(owner, **params) == original
            assert original["id"] == target and len(AgentDirectory(journal).list(owner)) == 1
            assert (await manager.start(target)) is not None
        else:
            with pytest.raises(CoreError) as interrupted: await portability.restore(owner, **params)
            assert interrupted.value.code == "restore_interrupted"
            assert not AgentDirectory(journal).list(owner)
            assert journal.get(RESTORES, "crash_request", owner.id).value["state"] == "failed"
            assert not manager.config(target).root.parent.exists()
            fresh = await portability.restore(owner, **{**params, "request_id": "new_attempt"})
            assert fresh["id"] != target
    finally:
        await lifecycle.close(); journal.close()


async def test_cli_repeated_restore_reuses_identity_and_explicit_new_request_creates_copy(portable_http, tmp_path, capsys):
    client, session = portable_http
    source = await agent(client, "CLI source")
    backup = await backup_when_idle(client, source["id"])
    archive = tmp_path / "portable.sturgis"
    archive.write_bytes(base64.b64decode(backup["archive_base64"]))
    config = tmp_path / "cli"
    url = str(client.base_url).rstrip("/")
    with PrivateStore(config) as store:
        store.write("session.json", {"url": url, "token": session["token"], "expires_at": time.time() + 100})
    arguments = ["--config-dir", str(config), "--url", url, "--json", "restore", str(archive), "--name", "CLI restored"]
    outputs = []
    for _ in range(2):
        assert await asyncio.to_thread(main, arguments) == 0
        outputs.append(strict_loads(capsys.readouterr().out))
    assert outputs[0] == outputs[1]
    assert outputs[0]["restore"]["request_id"].startswith("restore_")
    assert len((await client.get("/api/v1/agents")).json()["data"]["items"]) == 2
    assert await asyncio.to_thread(main, [*arguments, "--request-id", "intentional_copy"]) == 0
    copied = strict_loads(capsys.readouterr().out)
    assert copied["id"] != outputs[0]["id"] and copied["restore"]["request_id"] == "intentional_copy"


async def test_durable_in_progress_receipt_fences_another_service_instance(empty_backup, monkeypatch):
    (journal, owner, _, directory, lifecycle, manager, service), _, archive = empty_backup
    second = Portability(journal, lifecycle, manager)
    entered, release = asyncio.Event(), asyncio.Event()
    original = service._restore_memory
    async def pause(config, memory):
        await original(config, memory)
        entered.set(); await release.wait()
    monkeypatch.setattr(service, "_restore_memory", pause)
    params = {"name": "Concurrent durable", "archive_base64": archive["archive_base64"], "sha256": archive["sha256"], "request_id": "durable_busy"}
    task = asyncio.create_task(service.restore(owner, **params))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        with pytest.raises(CoreError) as busy: await second.restore(owner, **params)
        assert busy.value.code == "busy"
    finally:
        release.set()
    result = await task
    assert await second.restore(owner, **params) == result
    assert len(directory.list(owner)) == 2
