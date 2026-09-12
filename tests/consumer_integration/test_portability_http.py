"""Real TCP, authenticated API, private workers and native memory portability."""
import asyncio
import base64
import hashlib
import socket

import httpx
import pytest
import uvicorn

from consumer.api import create_app
from consumer.config import Settings
from tests.consumer.test_api import PASSWORD, TOKEN, agent, command


@pytest.fixture
async def portable_http(tmp_path):
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); sock.listen()
    port = sock.getsockname()[1]
    app = create_app(Settings(root=tmp_path, host="127.0.0.1", port=port,
        bootstrap_token=TOKEN, allow_signup=True))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False, lifespan="on", proxy_headers=False))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    async def started():
        while not server.started:
            if task.done(): await task
            await asyncio.sleep(.01)
    await asyncio.wait_for(started(), 5)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=20) as client:
            response = await client.post("/api/v1/bootstrap", json={"email": "owner@example.test", "password": PASSWORD, "token": TOKEN})
            assert response.status_code == 200, response.text
            session = response.json()["data"]
            client.headers["Authorization"] = "Bearer " + session["token"]
            yield client, session
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 15)
        sock.close()


async def backup_when_idle(client, agent_id):
    deadline = asyncio.get_running_loop().time() + 10
    while True:
        response = await client.post(f"/api/v1/agents/{agent_id}/backup", json={})
        if response.status_code == 200: return response.json()["data"]
        assert response.json()["error"]["code"] == "runtime_busy", response.text
        assert asyncio.get_running_loop().time() < deadline
        await asyncio.sleep(.05)


async def test_real_http_backup_restores_fresh_agent_content_memory_and_labeled_history(portable_http):
    client, _ = portable_http
    source = await agent(client, "Original HTTP agent")
    room = await command(client, source["id"], "conversations.create", {"title": "Portable conversation"})
    raw = b"A durable source about the northern observatory."
    await command(client, source["id"], "files.write", {"path": "notes/observatory.txt", "data_base64": base64.b64encode(raw).decode()})
    attachment = await command(client, source["id"], "attachments.upload", {"name": "observatory.txt", "media_type": "text/plain",
        "data_base64": base64.b64encode(raw).decode(), "operation_id": "source"})
    document = await command(client, source["id"], "knowledge.add", {"attachment_id": attachment["id"], "title": "Observatory", "operation_id": "knowledge"})
    params = {"conversation_id": room["id"], "message_id": "historical", "text": "Remember the northern observatory"}
    admitted = await command(client, source["id"], "messages.send", params)
    backup = await backup_when_idle(client, source["id"])
    archive = base64.b64decode(backup["archive_base64"], validate=True)
    assert backup["format"] == "sturgis-backup/1" and backup["bytes"] == len(archive)
    assert backup["sha256"] == hashlib.sha256(archive).hexdigest()
    response = await client.post("/api/v1/agents/restore", json={"name": "Restored HTTP agent",
        "archive_base64": backup["archive_base64"], "sha256": backup["sha256"]})
    assert response.status_code == 200, response.text
    restored = response.json()["data"]
    assert restored["id"] != source["id"] and restored["owner_id"] == source["owner_id"]
    assert restored["restore"]["history_artifact_ids"]
    target = restored["id"]
    contents = await command(client, target, "files.read", {"path": "notes/observatory.txt"})
    assert base64.b64decode(contents["data_base64"]) == raw
    downloaded = await client.get(f"/api/v1/agents/{target}/attachments/{attachment['id']}")
    assert downloaded.status_code == 200 and downloaded.content == raw
    assert (await command(client, target, "knowledge.get", {"id": document["document"]["id"]}))["text"] == raw.decode()
    assert (await command(client, target, "overview"))["memory"]["available"]
    assert (await command(client, target, "memory.query", {"text": "northern observatory"}))["data"]["records"]
    history = await command(client, target, "conversations.get", {"id": room["id"]})
    assert history["messages"][0]["status"] == "historical"
    assert history["turns"][0]["state"] in {"failed", "interrupted", "completed"}
    page = (await client.get(f"/api/v1/agents/{target}/events", params={"conversation_id": room["id"], "wait": 0})).json()["data"]
    event = next(item for item in page["items"] if item["id"] == admitted["event_id"])
    assert event["origin"] == "portable.import" and event["kind"] == "user.prompt"
    replay = await client.post(f"/api/v1/agents/{target}/commands/messages.send", json={"params": params, "request_id": "never_replay"})
    assert replay.json()["error"]["code"] == "historical_message"
    assert await command(client, target, "connections.list") == []
    assert await command(client, target, "providers.credentials.list") == []
    # Source remains independently usable after the stopped snapshot and restore.
    assert (await command(client, source["id"], "overview"))["agent_id"] == source["id"]


async def test_real_http_backup_ownership_csrf_and_restore_hash_boundaries(portable_http):
    client, session = portable_http
    source = await agent(client)
    backup = await backup_when_idle(client, source["id"])
    body = {"name": "Denied restore", "archive_base64": backup["archive_base64"], "sha256": backup["sha256"]}
    initial = (await client.get("/api/v1/agents")).json()["data"]
    denied = await client.post("/api/v1/agents/restore", json={**body, "sha256": "0" * 64})
    assert denied.status_code == 400 and denied.json()["error"]["code"] == "invalid_backup"
    for extra in ({"scope": source["id"]}, {"root": "/tmp/caller-selected"}):
        assert (await client.post("/api/v1/agents/restore", json={**body, **extra})).status_code == 400
    assert (await client.get("/api/v1/agents")).json()["data"] == initial
    # Cookies alone do not authorize either mutation without the matching CSRF token.
    authorization = client.headers.pop("Authorization")
    for route, payload in ((f"/api/v1/agents/{source['id']}/backup", {}), ("/api/v1/agents/restore", body)):
        response = await client.post(route, json=payload, headers={"Origin": str(client.base_url).rstrip("/")})
        assert response.status_code == 401 and response.json()["error"]["code"] == "csrf_failed"
    permitted = await client.post(f"/api/v1/agents/{source['id']}/backup", json={}, headers={
        "Origin": str(client.base_url).rstrip("/"), "X-CSRF-Token": session["csrf_token"]})
    assert permitted.status_code == 200
    client.headers["Authorization"] = authorization
    async with httpx.AsyncClient(base_url=client.base_url, timeout=20) as outsider:
        signup = await outsider.post("/api/v1/auth/signup", json={"email": "other@example.test", "password": PASSWORD})
        assert signup.status_code == 200, signup.text
        outsider.headers["Authorization"] = "Bearer " + signup.json()["data"]["token"]
        denied = await outsider.post(f"/api/v1/agents/{source['id']}/backup", json={})
        assert denied.status_code == 404
        assert (await outsider.get("/api/v1/agents")).json()["data"]["items"] == []
        # A supplied archive is portable data, but restoration always makes a new
        # agent owned by the authenticated caller, never an override of its source.
        imported = await outsider.post("/api/v1/agents/restore", json=body)
        assert imported.status_code == 200, imported.text
        target = imported.json()["data"]
        assert target["owner_id"] != source["owner_id"] and target["id"] != source["id"]
        assert (await client.get(f"/api/v1/agents/{target['id']}")).status_code == 404
