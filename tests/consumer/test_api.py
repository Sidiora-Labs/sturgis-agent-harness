"""Shared HTTP commands across real account storage and private application workers."""
import asyncio
import base64
import hashlib
import hmac
import os
from pathlib import Path
import time

import httpx
import pytest

from agent.protocol import CoreError, new_id
from consumer.api import create_app, _bootstrap
from consumer.config import Settings
from consumer.webhooks import signing_bytes

TOKEN = "operator-bootstrap-test-token"
PASSWORD = "test-account-password"


@pytest.fixture
async def api(tmp_path):
    app = create_app(Settings(root=tmp_path, host="testserver", port=80, bootstrap_token=TOKEN, allow_signup=True))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.post("/api/v1/bootstrap", json={"email": "owner@example.com", "password": PASSWORD, "token": TOKEN})
            assert response.status_code == 200, response.text
            client.headers["Authorization"] = "Bearer " + response.json()["data"]["token"]
            yield client, app


async def command(client, agent_id, method, params=None, request_id=None):
    response = await client.post(f"/api/v1/agents/{agent_id}/commands/{method}", json={"params": params or {}, "request_id": request_id or new_id("test")})
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def agent(client, name="My agent"):
    response = await client.post("/api/v1/agents", json={"name": name})
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def test_accounts_agents_worker_and_revisioned_lifecycle(api):
    client, app = api
    assert (await client.get("/api/v1/health")).json()["data"]["status"] == "ok"
    assert not (await client.get("/api/v1/bootstrap")).json()["data"]["bootstrap_required"]
    assert (await client.get("/api/v1/auth/me")).json()["data"]["email"] == "owner@example.com"
    created = await agent(client)
    assert (await client.get("/api/v1/agents")).json()["data"]["items"] == [created]
    renamed = await client.patch(f"/api/v1/agents/{created['id']}", json={"name": "Renamed", "expected_revision": created["revision"]})
    assert renamed.status_code == 200
    assert (await client.get(f"/api/v1/agents/{created['id']}")).json()["data"]["name"] == "Renamed"
    started = await client.post(f"/api/v1/agents/{created['id']}/lifecycle/start", json={})
    assert started.status_code == 200, started.text
    assert (await command(client, created["id"], "overview"))["agent_id"] == created["id"]
    assert (await client.post(f"/api/v1/agents/{created['id']}/lifecycle/status", json={})).status_code == 200
    assert (await client.post(f"/api/v1/agents/{created['id']}/lifecycle/unknown", json={})).status_code == 404
    assert (await client.patch(f"/api/v1/agents/{created['id']}", json={"archived": False, "expected_revision": 2})).status_code == 400
    for _ in range(20):
        suspended = await client.post(f"/api/v1/agents/{created['id']}/lifecycle/suspend", json={})
        if suspended.status_code == 200: break
        assert suspended.json()["error"]["code"] == "runtime_busy"
        await asyncio.sleep(.1)
    assert suspended.status_code == 200, suspended.text
    archived = await client.patch(f"/api/v1/agents/{created['id']}", json={"archived": True, "expected_revision": 2})
    assert archived.status_code == 200, archived.text
    assert not (await client.get("/api/v1/agents")).json()["data"]["items"]
    assert (await client.get("/api/v1/agents?include_archived=true")).json()["data"]["items"][0]["archived"]
    assert (await client.get("/api/v1/agents?include_archived=perhaps")).status_code == 400
    assert (await client.post("/api/v1/auth/logout", json={})).status_code == 200
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    client.headers.pop("Authorization")
    logged = await client.post("/api/v1/auth/login", json={"email": "owner@example.com", "password": PASSWORD})
    assert logged.status_code == 200


async def test_conversation_content_events_and_no_worker_scope_override(api):
    client, app = api
    owner = await agent(client)
    room = await command(client, owner["id"], "conversations.create", {"title": "HTTP conversation"})
    upload = await command(client, owner["id"], "attachments.upload", {"name": "note.html", "media_type": "text/html", "data_base64": base64.b64encode(b"<script>danger()</script><p>Source</p>").decode(), "operation_id": "upload"})
    response = await client.get(f"/api/v1/agents/{owner['id']}/attachments/{upload['id']}")
    assert response.status_code == 200 and response.content.startswith(b"<script>")
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-security-policy"].startswith("sandbox")
    assert "attachment" in response.headers["content-disposition"]
    receipt = await command(client, owner["id"], "messages.send", {"conversation_id": room["id"], "message_id": "m1", "text": "HTTP input", "attachments": [upload["id"]]}, "stable-send")
    assert receipt["status"] == "admitted"
    replay = await command(client, owner["id"], "messages.send", {"conversation_id": room["id"], "message_id": "m1", "text": "HTTP input", "attachments": [upload["id"]]}, "stable-send")
    assert replay["event_id"] == receipt["event_id"]
    events = await client.get(f"/api/v1/agents/{owner['id']}/events", params={"conversation_id": room["id"], "wait": .3})
    assert events.status_code == 200 and events.json()["data"]["items"]
    sse = await client.get(f"/api/v1/agents/{owner['id']}/events?wait=0.15", headers={"Accept": "text/event-stream"})
    assert sse.status_code == 200 and "event: page" in sse.text
    after = (await command(client, owner["id"], "messages.events", {"limit": 1000}))["cursor"]
    waited = await client.get(f"/api/v1/agents/{owner['id']}/events", params={"after": after, "wait": .1})
    assert waited.status_code == 200
    for query in ("after=bad", "wait=bad", "after=-1", "wait=30"):
        assert (await client.get(f"/api/v1/agents/{owner['id']}/events?{query}")).status_code == 400
    denied = await client.post(f"/api/v1/agents/{owner['id']}/commands/overview", json={"params": {"scope": "other"}, "request_id": "scope"})
    assert denied.status_code == 400
    assert (await command(client, owner["id"], "work.overview"))["work"]
    assert await command(client, owner["id"], "plugins.list") == []
    assert (await command(client, owner["id"], "diagnostics.get"))["scope"] == owner["id"]


async def test_signed_webhook_preserves_exact_wire_body_and_never_becomes_owner_input(api):
    client, app = api
    owner = await agent(client)
    credential = await command(client, owner["id"], "providers.credentials.put", {"name": "hook", "secret": "actual-signing-key"})
    configured = await command(client, owner["id"], "connections.configure", {"id": "hook", "kind": "webhook", "endpoint": "https://hooks.example/in", "credential_ref": credential["reference"]})
    granted = await command(client, owner["id"], "connections.grant", {"id": "hook", "operations": ["receive"], "expected_revision": configured["revision"]})
    ready = await command(client, owner["id"], "connections.probe", {"id": "hook", "expected_revision": granted["revision"]})
    raw = b'{ "event_id": "wire-event", "text": "External source", "data": {} }'
    timestamp, nonce = int(time.time()), "wire_nonce"
    signature = hmac.new(b"actual-signing-key", signing_bytes("hook", ready["revision"], timestamp, nonce, raw), hashlib.sha256).hexdigest()
    headers = {"Content-Type": "application/json", "X-Sturgis-Timestamp": str(timestamp), "X-Sturgis-Nonce": nonce, "X-Sturgis-Signature": signature}
    url = f"/api/v1/external/{owner['id']}/webhook/hook/receive"
    response = await client.post(url, content=raw, headers=headers)
    assert response.status_code == 200, response.text
    event = response.json()["data"]
    assert event["kind"] == "external.webhook" and event["payload"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert (await client.post(url, content=raw, headers=headers)).json()["data"]["id"] == event["id"]
    public_events = await command(client, owner["id"], "messages.events", {"limit": 1000})
    assert not any(e["kind"].startswith("user.") for e in public_events["items"])
    bad = await client.post(url, content=raw, headers={**headers, "X-Sturgis-Signature": "0" * 64})
    assert bad.status_code == 403
    assert (await client.post(url, content=raw, headers={**headers, "X-Sturgis-Timestamp": "bad"})).status_code == 400
    assert (await client.post(url.replace("hook/", "missing/"), content=raw, headers=headers)).status_code == 404
    assert (await client.post(url.replace("receive", "wrong"), content=raw, headers=headers)).status_code == 404
    await command(client, owner["id"], "connections.revoke", {"id": "hook", "expected_revision": ready["revision"]})
    assert (await client.post(url, content=raw, headers=headers)).status_code == 404


def test_private_bootstrap_file_and_operator_origin_configuration(tmp_path):
    token = _bootstrap(tmp_path, "")
    assert _bootstrap(tmp_path, "") == token
    assert (tmp_path / "bootstrap.token").stat().st_mode & 0o777 == 0o600
    os.chmod(tmp_path / "bootstrap.token", 0o644)
    with pytest.raises(CoreError):
        _bootstrap(tmp_path, "")
    os.chmod(tmp_path / "bootstrap.token", 0o600)
    (tmp_path / "bootstrap.token").write_text("short")
    with pytest.raises(CoreError):
        _bootstrap(tmp_path, "")
    with pytest.raises(CoreError):
        create_app(Settings(root=tmp_path / "bad", host="0.0.0.0"))
    settings = Settings(root=tmp_path, public_origin="https://sturgis.example/", worker_memory_bytes=274877906944)
    assert settings.public_origin == "https://sturgis.example"
    env = Settings.from_env({"STURGIS_PUBLIC_ORIGIN": "http://local.test:8000", "STURGIS_WORKER_MEMORY_BYTES": "2147483648", "STURGIS_BROWSER_BINARY": "/usr/bin/chromium"})
    assert env.browser_binary == Path("/usr/bin/chromium").resolve()
    with pytest.raises(CoreError):
        Settings(worker_memory_bytes=0)
    with pytest.raises(CoreError):
        Settings(public_origin="ws://not-a-site")
