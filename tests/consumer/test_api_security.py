"""Adversarial HTTP requests using real accounts, lifespan and private workers."""
from contextlib import asynccontextmanager
import asyncio
import base64
import hashlib
import hmac
import time

import httpx
import pytest

from consumer.api import COOKIE, CSRF_COOKIE, create_app
from consumer.config import Settings
from consumer.contracts import MAX_BODY, VERSION
from consumer.lifecycle import COMMANDS, CONTROL
from consumer.device import device_signing_bytes
from agent.protocol import ToolCall, canonical_json
from tests.integration.test_continuity import WireModel, proposal

TOKEN = "long-test-bootstrap-token-for-http-security"
PASSWORD = "long-test-owner-password"


@asynccontextmanager
async def installation(tmp_path, *, secure=False, origins=()):
    settings = Settings(root=tmp_path / "installation", host="testserver", port=80,
        bootstrap_token=TOKEN, allow_signup=True,
        public_origin="https://testserver" if secure else None, private_network_origins=origins)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                base_url="https://testserver" if secure else "http://testserver") as client:
            yield client, app.state.control


def data(response):
    assert response.status_code == 200, response.text
    value = response.json()
    assert set(value) == {"version", "request_id", "data", "error"}
    assert value["version"] == VERSION and value["error"] is None
    return value["data"]


def denied(response, code, status=None):
    if status is not None:
        assert response.status_code == status, response.text
    else:
        assert response.status_code >= 400, response.text
    value = response.json()
    assert value["data"] is None and value["error"]["code"] == code
    assert value["version"] == VERSION
    return value["error"]


async def bootstrap(client):
    return data(await client.post("/api/v1/bootstrap", json={"email": "owner@example.test", "password": PASSWORD, "token": TOKEN}))


def bearer(session):
    return {"Authorization": "Bearer " + session["token"]}


async def agent(client, session, name="Agent"):
    return data(await client.post("/api/v1/agents", headers=bearer(session), json={"name": name}))


async def command(client, session, agent_id, method, params, request_id):
    return await client.post(f"/api/v1/agents/{agent_id}/commands/{method}",
        headers=bearer(session), json={"params": params, "request_id": request_id})


@pytest.mark.parametrize("headers,code", [
    ({"Host": "attacker.example"}, "host_denied"),
    ({"Host": "testserver:80"}, "host_denied"),
    ({"Host": ""}, "host_denied"),
    ([("Host", "testserver"), ("Host", "testserver")], "host_denied"),
    ({"Origin": "null"}, "origin_denied"),
    ({"Origin": "http://attacker.example"}, "origin_denied"),
    ({"Origin": "http://testserver/"}, "origin_denied"),
    ([("Origin", "http://testserver"), ("Origin", "http://testserver")], "origin_denied"),
    ({"Host": "attacker.example", "X-Forwarded-Host": "testserver"}, "host_denied"),
])
async def test_host_origin_and_forwarding_headers_do_not_change_authority(tmp_path, headers, code):
    async with installation(tmp_path) as (client, _):
        response = await client.get("/api/v1/health", headers=headers)
        denied(response, code, 403)
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"


async def test_all_routes_and_static_assets_share_security_headers(tmp_path):
    async with installation(tmp_path) as (client, _):
        for path in ("/", "/assets/unknown.js", "/unknown", "/api/v1/health"):
            response = await client.get(path, headers={"Host": "wrong.example"})
            denied(response, "host_denied", 403)
        health = await client.get("/api/v1/health", headers={"X-Forwarded-Host": "attacker", "X-Forwarded-Proto": "https"})
        assert data(health)["status"] == "ok"
        for key in ("content-security-policy", "x-content-type-options", "referrer-policy", "cache-control"):
            assert key in health.headers
        assert "frame-ancestors 'none'" in health.headers["content-security-policy"]
        unsupported = await client.delete("/api/v1/auth/login")
        denied(unsupported, "method_not_allowed", 405)


async def test_cookie_mutation_requires_origin_and_matching_session_csrf(tmp_path):
    async with installation(tmp_path) as (client, control):
        session = await bootstrap(client)
        assert data(await client.get("/api/v1/auth/me"))["id"] == session["principal"]["id"]
        denied(await client.post("/api/v1/agents", json={"name": "No origin"}), "origin_denied", 403)
        denied(await client.post("/api/v1/agents", headers={"Origin": control.origin}, json={"name": "No CSRF"}), "csrf_failed", 401)
        denied(await client.post("/api/v1/agents", headers={"Origin": control.origin, "X-CSRF-Token": "incorrect"}, json={"name": "Wrong CSRF"}), "csrf_failed", 401)
        cookie_headers = {"Origin": control.origin, "X-CSRF-Token": session["csrf_token"]}
        created = data(await client.post("/api/v1/agents", headers=cookie_headers, json={"name": "Browser agent"}))
        assert created["owner_id"] == session["principal"]["id"]
        logout = await client.post("/api/v1/auth/logout", headers=cookie_headers, json={})
        assert data(logout)["logged_out"]
        assert all("Max-Age=0" in value for value in logout.headers.get_list("set-cookie"))
        denied(await client.get("/api/v1/auth/me", headers=bearer(session)), "unauthenticated", 401)


async def test_bearer_has_explicit_precedence_and_query_token_is_not_authentication(tmp_path):
    async with installation(tmp_path) as (client, control):
        owner = await bootstrap(client)
        user = data(await client.post("/api/v1/auth/signup", json={"email": "user@example.test", "password": PASSWORD}))
        # The cookie now belongs to user, while the bearer belongs to owner.
        result = data(await client.get("/api/v1/auth/me", headers=bearer(owner)))
        assert result["id"] == owner["principal"]["id"]
        denied(await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer invalid"}), "unauthenticated", 401)
        denied(await client.get("/api/v1/auth/me", headers={"Authorization": "Basic anything"}), "unauthenticated", 401)
        created = await agent(client, owner, "CLI bearer without Origin")
        assert created["owner_id"] == owner["principal"]["id"]
        denied(await client.post("/api/v1/agents", headers={**bearer(owner), "Origin": "http://attacker.example"}, json={"name": "No"}), "origin_denied", 403)
        client.cookies.clear()
        denied(await client.get("/api/v1/auth/me", params={"token": user["token"]}), "unauthenticated", 401)


async def test_https_cookie_attributes_and_login_rotation(tmp_path):
    async with installation(tmp_path, secure=True) as (client, _):
        response = await client.post("/api/v1/bootstrap", json={"email": "owner@example.test", "password": PASSWORD, "token": TOKEN})
        first = data(response)
        cookies = response.headers.get_list("set-cookie")
        session_cookie = next(value for value in cookies if value.startswith(COOKIE + "="))
        csrf_cookie = next(value for value in cookies if value.startswith(CSRF_COOKIE + "="))
        assert all("Secure" in value and "SameSite=strict" in value and "Path=/" in value for value in cookies)
        assert "HttpOnly" in session_cookie and "HttpOnly" not in csrf_cookie
        logged = data(await client.post("/api/v1/auth/login", json={"email": "owner@example.test", "password": PASSWORD}))
        assert logged["token"] != first["token"] and logged["csrf_token"] != first["csrf_token"]
        denied(await client.post("/api/v1/agents", headers={"Origin": "https://testserver", "X-CSRF-Token": first["csrf_token"]}, json={"name": "Mixed sessions"}), "csrf_failed", 401)


@pytest.mark.parametrize("kind", ["authorization", "csrf", "cookie_headers", "cookie_names"])
async def test_ambiguous_authentication_headers_fail_closed(tmp_path, kind):
    async with installation(tmp_path) as (client, control):
        session = await bootstrap(client)
        client.cookies.clear()
        headers = [("Origin", control.origin)]
        if kind == "authorization":
            headers += [("Authorization", "Bearer " + session["token"]), ("Authorization", "Bearer invalid")]
        elif kind == "csrf":
            headers += [("Cookie", COOKIE + "=" + session["token"]),
                        ("X-CSRF-Token", session["csrf_token"]), ("X-CSRF-Token", "invalid")]
        elif kind == "cookie_headers":
            headers += [("Cookie", COOKIE + "=" + session["token"]), ("Cookie", COOKIE + "=" + session["token"]),
                        ("X-CSRF-Token", session["csrf_token"])]
        else:
            headers += [("Cookie", COOKIE + "=invalid; " + COOKIE + "=" + session["token"]),
                        ("X-CSRF-Token", session["csrf_token"])]
        response = await client.post("/api/v1/agents", headers=headers, json={"name": "Ambiguous credentials"})
        assert response.status_code in {400, 401, 403}, response.text
        assert not control.journal.list("consumer.agents", CONTROL)


@pytest.mark.parametrize("body", [
    b'{"email":"owner@example.test","email":"second@example.test","password":"private-value"}',
    b'{"email":"owner@example.test","password":NaN}',
    b'{"email":"owner@example.test","password":Infinity}', b'[]', b'null', b'{', b'\xff',
])
async def test_malformed_json_is_typed_safe_and_cannot_create_accounts(tmp_path, body):
    async with installation(tmp_path) as (client, control):
        response = await client.post("/api/v1/auth/login", content=body, headers={"Content-Type": "application/json"})
        assert response.status_code == 400
        assert response.json()["error"]["code"] in {"invalid_json", "invalid_request"}
        assert "private-value" not in response.text
        assert not control.journal.list("consumer.users", CONTROL)


async def test_streamed_body_bound_and_content_type_before_domain_effects(tmp_path):
    async with installation(tmp_path) as (client, control):
        async def chunks():
            yield b'{"password":"'
            yield b"x" * (MAX_BODY // 2)
            yield b"x" * (MAX_BODY // 2)
            yield b'"}'
        oversized = await client.post("/api/v1/auth/login", content=chunks(), headers={"Content-Type": "application/json"})
        denied(oversized, "body_too_large", 413)
        assert len(oversized.content) < 4096
        for content_type in ("text/plain", "application/x-www-form-urlencoded", "text/html"):
            denied(await client.post("/api/v1/auth/login", content=b"{}", headers={"Content-Type": content_type}), "invalid_request", 400)
        assert not control.journal.list("consumer.users", CONTROL)


async def test_cross_owner_requests_and_reserved_commands_never_reach_worker(tmp_path):
    async with installation(tmp_path) as (client, control):
        administrator = await bootstrap(client)
        private_owner = data(await client.post("/api/v1/auth/signup", json={"email": "private@example.test", "password": PASSWORD}))
        private = await agent(client, private_owner, "Private agent")
        assert (await control.manager.status(private["id"]))["state"] == "stopped"
        before = control.journal.list(COMMANDS, CONTROL)
        # Administrator privileges do not grant another user's private runtime.
        for suffix in ("", "/events", "/attachments/missing"):
            denied(await client.get("/api/v1/agents/" + private["id"] + suffix, headers=bearer(administrator)), "not_found", 404)
        denied(await command(client, administrator, private["id"], "overview", {}, "wrong-owner"), "not_found", 404)
        denied(await client.post(f"/api/v1/agents/{private['id']}/lifecycle/start", headers=bearer(administrator), json={}), "not_found", 404)
        for index, method in enumerate(("_worker.status", "_worker.receipt", "_worker.quiesce", "ingress.webhook", "ingress.device.claim", "__dict__")):
            denied(await command(client, private_owner, private["id"], method, {}, f"reserved-{index}"), "unknown_command", 404)
        assert control.journal.list(COMMANDS, CONTROL) == before
        assert (await control.manager.status(private["id"]))["state"] == "stopped"
        owner_principal = control.accounts.authenticate(administrator["token"])
        control.accounts.disable(owner_principal, private_owner["principal"]["id"], 1)
        denied(await client.get("/api/v1/agents/" + private["id"], headers=bearer(private_owner)), "unauthenticated", 401)


async def test_private_command_scope_fields_and_duplicate_json_fail_closed(tmp_path):
    async with installation(tmp_path) as (client, control):
        session = await bootstrap(client)
        owned = await agent(client, session)
        path = f"/api/v1/agents/{owned['id']}/commands/overview"
        duplicate = await client.post(path, headers={**bearer(session), "Content-Type": "application/json"},
            content=b'{"params":{},"params":{"scope":"elsewhere"},"request_id":"duplicate"}')
        denied(duplicate, "invalid_json", 400)
        assert not control.journal.list(COMMANDS, CONTROL)
        # This request reaches a real authenticated private worker, whose own
        # command schema rejects caller-selected authority fields.
        for index, params in enumerate(({"scope": "elsewhere"}, {"root": "/tmp"}, {"reviewer": "owner"}, {"method": "_worker.status"})):
            response = await command(client, session, owned["id"], "overview", params, f"inject-{index}")
            denied(response, "invalid_request", 400)
        overview = data(await command(client, session, owned["id"], "overview", {}, "real-overview"))
        assert overview["agent_id"] == owned["id"]
        assert (await control.manager.status(owned["id"]))["state"] == "running"


async def test_stale_archive_revision_does_not_suspend_running_worker(tmp_path):
    async with installation(tmp_path) as (client, control):
        session = await bootstrap(client)
        owned = await agent(client, session)
        data(await command(client, session, owned["id"], "overview", {}, "start-real-worker"))
        before = await control.manager.status(owned["id"])
        assert before["state"] == "running"
        response = await client.patch(f"/api/v1/agents/{owned['id']}", headers=bearer(session),
            json={"archived": True, "expected_revision": 0})
        denied(response, "conflict", 409)
        after = await control.manager.status(owned["id"])
        assert after["state"] == "running" and after["pid"] == before["pid"]
        assert not data(await client.get(f"/api/v1/agents/{owned['id']}", headers=bearer(session)))["archived"]


async def test_secret_inspection_and_attachment_output_are_safe(tmp_path):
    async with installation(tmp_path) as (client, control):
        session = await bootstrap(client)
        owned = await agent(client, session, "<script>alert('name')</script>")
        secret = "transient-provider-secret-only-for-this-test"
        stored = data(await command(client, session, owned["id"], "providers.credentials.put", {"name": "private", "secret": secret}, "put-secret"))
        assert stored["active"] and secret not in str(stored)
        inspected = await command(client, session, owned["id"], "providers.credentials.list", {}, "list-secrets")
        assert secret not in inspected.text and data(inspected)[0]["reference"] == stored["reference"]
        payload = b'<script>fetch("/api/v1/auth/me")</script>'
        uploaded = data(await command(client, session, owned["id"], "attachments.upload", {
            "name": "untrusted.html", "media_type": "text/html", "data_base64": base64.b64encode(payload).decode(), "operation_id": "html-upload"}, "upload-html"))
        download = await client.get(f"/api/v1/agents/{owned['id']}/attachments/{uploaded['id']}", headers=bearer(session))
        assert download.status_code == 200, download.text
        assert download.content == payload
        assert download.headers["content-type"] == "application/octet-stream"
        assert download.headers["content-disposition"].startswith("attachment;")
        assert "sandbox" in download.headers["content-security-policy"]
        assert download.headers["x-content-type-options"] == "nosniff"
        artifact_path = f"/api/v1/agents/{owned['id']}/artifacts/{uploaded['artifact_id']}"
        artifact = await client.get(artifact_path, headers=bearer(session))
        assert artifact.status_code == 200 and artifact.content == payload
        assert artifact.headers["content-type"] == "application/octet-stream"
        assert artifact.headers["content-disposition"].startswith("attachment;")
        assert artifact.headers["content-security-policy"] == "sandbox; default-src 'none'"
        outsider = data(await client.post("/api/v1/auth/signup", json={"email": "artifact-outsider@example.test", "password": PASSWORD}))
        before = control.journal.list(COMMANDS, CONTROL)
        denied(await client.get(artifact_path, headers=bearer(outsider)), "not_found", 404)
        assert control.journal.list(COMMANDS, CONTROL) == before
        denied(await client.get(f"/api/v1/agents/{owned['id']}/artifacts/art_" + "0" * 64, headers=bearer(session)), "not_found", 404)
        record = await client.get(f"/api/v1/agents/{owned['id']}", headers=bearer(session))
        assert record.headers["content-type"].startswith("application/json")
        assert data(record)["name"] == owned["name"]
        # The only plaintext sent to the vault is transient. The control plane
        # and worker transport retain digests/metadata, never command secrets.
        config = control.manager.config(owned["id"])
        for path in (control.settings.root / "control.sqlite", config.private_root / "transport.sqlite", config.root / "operations.sqlite"):
            for candidate in (path, path.with_name(path.name + "-wal")):
                if candidate.exists():
                    assert secret.encode() not in candidate.read_bytes()


async def test_external_device_http_routes_pair_and_complete_actual_authorized_work(tmp_path):
    model = WireModel([[proposal("consumer.probe", {})]])
    server = await asyncio.start_server(model.handle, "127.0.0.1", 0)
    origin = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    try:
        async with installation(tmp_path, origins=(origin,)) as (client, control):
            session = await bootstrap(client)
            owned = await agent(client, session)
            async def owner_command(method, params):
                return data(await command(client, session, owned["id"], method, params, "device-http-" + str(time.monotonic_ns())))
            configured = await owner_command("connections.configure", {"id": "device", "kind": "device", "endpoint": "https://device.invalid",
                "options": {"device_id": "peer", "operations": {"write_note": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False}}}})
            pair = await owner_command("devices.pair", {"connection_id": "device", "expected_revision": configured["revision"]})
            base = f"/api/v1/external/{owned['id']}/device/device/"
            # External peers authenticate with a one-use pairing proof and HMAC,
            # with no owner browser session or bearer token.
            client.cookies.clear()
            claim = data(await client.post(base + "claim", json={"pairing_token": pair["pairing_token"], "device_id": "peer"}))
            denied(await client.post(base + "claim", json={"pairing_token": pair["pairing_token"], "device_id": "peer"}), "pairing_denied")
            granted = await owner_command("connections.grant", {"id": "device", "operations": ["request"], "expected_revision": claim["revision"]})
            ready = await owner_command("connections.probe", {"id": "device", "expected_revision": granted["revision"]})
            assert ready["status"] == "ready" and not ready["probe"]["actuator_verified"]
            def headers(action, nonce, body=b""):
                timestamp = int(time.time())
                revision = 0 if action == "sync" else ready["revision"]
                signature = hmac.new(claim["secret"].encode(), device_signing_bytes(action, "device", revision,
                    timestamp, nonce, body), hashlib.sha256).hexdigest()
                return {"Content-Type": "application/json", "X-Sturgis-Timestamp": str(timestamp),
                    "X-Sturgis-Nonce": nonce, "X-Sturgis-Signature": signature}
            sync = data(await client.post(base + "sync", json={}, headers=headers("sync", "sync-http")))
            assert sync["revision"] == ready["revision"]
            assert not data(await client.post(base + "poll", json={}, headers=headers("poll", "poll-empty")))["requests"]
            denied(await client.post(base + "poll", json={}, headers={**headers("poll", "poll-bad"), "X-Sturgis-Signature": "0" * 64}), "device_signature", 403)
            provider = await owner_command("providers.configure", {"provider_id": "wire", "endpoint": origin + "/model", "model": "wire",
                "context_limit": 60000, "output_limit": 512, "tools": True})
            assert (await owner_command("providers.probe", {"provider_id": "wire", "expected_revision": provider["revision"]}))["ready"]
            await owner_command("providers.activate", {"provider_id": "wire", "expected_revision": provider["revision"]})
            await owner_command("permissions.set", {"tool": "connection.device.request", "mode": "allow"})
            call = ToolCall("connection.device.request", {"operation": "write_note", "arguments": {"text": "Observed device peer effect"}}, tool_version=str(ready["revision"]))
            output = proposal(call.tool, call.arguments)
            output["choices"][0]["delta"]["tool_calls"][0]["function"]["arguments"] = canonical_json(call.as_dict())
            model.scripts.extend([[output], ["Device reported its result"]])
            room = await owner_command("conversations.create", {})
            receipt = await owner_command("messages.send", {"conversation_id": room["id"], "message_id": "device-message", "text": "Ask the paired device to write the note"})
            request = None
            for index in range(80):
                page = data(await client.post(base + "poll", json={}, headers=headers("poll", "poll-http-" + str(index))))
                if page["requests"]:
                    request = page["requests"][0]
                    break
                await asyncio.sleep(.05)
            assert request is not None
            peer_file = tmp_path / "device-peer-note.txt"
            peer_file.write_text(request["arguments"]["text"])
            body = canonical_json({"request_id": request["request_id"], "status": "success", "data": {"sha256": hashlib.sha256(peer_file.read_bytes()).hexdigest()}}).encode()
            signed_headers = headers("respond", "respond-http", body)
            response = data(await client.post(base + "respond", content=body, headers=signed_headers))
            assert response["state"] == "responded"
            assert data(await client.post(base + "respond", content=body, headers=signed_headers)) == response
            for _ in range(80):
                work = await owner_command("work.get", {"id": receipt["work_id"]})
                if work["state"] in {"completed", "failed", "cancelled"}:
                    break
                await asyncio.sleep(.05)
            assert work["state"] == "completed" and peer_file.read_text() == "Observed device peer effect"
            await owner_command("devices.revoke", {"connection_id": "device", "expected_revision": ready["revision"]})
            route = control.journal.get("consumer.external_routes", owned["id"] + ":device", CONTROL)
            assert not route.value["active"]
            before = control.journal.list(COMMANDS, CONTROL)
            denied(await client.post(base + "poll", json={}, headers=headers("poll", "poll-after-revoke")), "not_found", 404)
            assert control.journal.list(COMMANDS, CONTROL) == before
    finally:
        server.close()
        for task in list(model.tasks):
            task.cancel()
        await asyncio.gather(*model.tasks, return_exceptions=True)
        await server.wait_closed()
