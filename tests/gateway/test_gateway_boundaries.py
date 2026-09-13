"""Real external clients, tenant authority, disconnect cleanup and binding recovery."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import socket
from types import SimpleNamespace

import httpx
import pytest
import uvicorn

from agent.protocol import CoreError
from consumer.gateway import ConsumerGateway
from consumer.application import AgentApplication
from model_gateway.api import create_app
from model_gateway.errors import GatewayError
from model_gateway.network import NetworkPolicy
from model_gateway.service import ModelGateway
from model_gateway.wire import json_dumps, json_loads
from tests.gateway.test_service import ANSWER, CATALOG, connect, server
from tests.gateway.test_transports import native_text, oai, payload, sse, wire
from tests.consumer.test_application import configuration


@asynccontextmanager
async def standalone(root, policy):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    address = f"http://127.0.0.1:{sock.getsockname()[1]}"
    app = create_app(root, public_origin=address, network_policy=policy)
    process = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False, lifespan="on", proxy_headers=False,
                                           timeout_graceful_shutdown=2))
    task = asyncio.create_task(process.serve(sockets=[sock]))
    async def started():
        while not process.started:
            if task.done():
                await task
            await asyncio.sleep(.005)
    await asyncio.wait_for(started(), 5)
    try:
        async with httpx.AsyncClient(base_url=address, timeout=5) as client:
            yield app.state.gateway, client
    finally:
        process.should_exit = True
        await asyncio.wait_for(task, 5)
        sock.close()


def stored_connection(gateway, tenant, endpoint, *, identity="conn_native", protocol="openai", account=None, models=("model",)):
    """Seed trusted control-plane state to isolate inference boundary behavior."""
    return gateway.store.put(tenant, "connection", identity, {"name": "Test connection", "preset_id": "custom-" + ("responses" if protocol == "openai-responses" else protocol),
        "endpoint": endpoint, "protocol": protocol, "models": [{"id": name, "name": name, "protocol": protocol,
            "context_limit": 32768, "output_limit": 1024, "tools": True} for name in models], "selected_model": models[0],
        "status": "connected", "error": None, "auth_account_id": account}, secret="upstream-only-key" if not account else None)


@pytest.mark.parametrize("protocol", ["anthropic", "openai-responses"])
@pytest.mark.parametrize("stream", [True, False])
async def test_actual_native_routes_preserve_opaque_provider_content(tmp_path, protocol, stream):
    if protocol == "anthropic":
        native = sse({"type": "message_start", "message": {"id": "msg1", "type": "message", "role": "assistant", "model": "model", "usage": {"input_tokens": 2}, "content": []}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "native-only"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "opaque-signed"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 3}}, {"type": "message_stop"})
        request = {"messages": [{"role": "user", "content": "Hello"}], "system": [{"type": "text", "text": "Rules", "cache_control": {"type": "ephemeral"}}],
                   "thinking": {"type": "adaptive"}, "max_tokens": 128}
    else:
        native = sse({"type": "response.created", "response": {"id": "resp1", "status": "in_progress"}},
            {"type": "response.completed", "response": {"id": "resp1", "status": "completed", "output": [{"type": "reasoning", "encrypted_content": "opaque-signed"}],
                                                        "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}}})
        request = {"input": [{"role": "user", "content": "Hello"}, {"type": "reasoning", "encrypted_content": "opaque-past"}], "store": False}
    async with wire([native]) as (url, policy, upstream):
        async with standalone(tmp_path, policy) as (gateway, client):
            row = stored_connection(gateway, "owner", url + "/v1", protocol=protocol)
            key = gateway.create_key("owner", name="External client", models=[row["id"] + "/model"])
            response = await client.post("/v1/" + ("messages" if protocol == "anthropic" else "responses"),
                headers={"Authorization": "Bearer " + key["key"], "Anthropic-Beta": "cache-test"},
                json={**request, "model": row["id"] + "/model", "stream": stream})
            assert response.status_code == 200, response.text
            assert "opaque-signed" in response.text and "upstream-only-key" not in response.text
            assert upstream[0]["body"]["model"] == "model"
            if protocol == "anthropic":
                assert upstream[0]["body"]["thinking"] == request["thinking"]
                assert upstream[0]["headers"]["anthropic-beta"] == "cache-test"
                assert upstream[0]["headers"]["x-api-key"] == "upstream-only-key"
            else:
                assert upstream[0]["body"]["input"] == request["input"]
            assert gateway.store.usage("owner")["total_tokens"] == 5
            with gateway.store.transaction() as db:
                for name in ("objects", "usage"):
                    assert "opaque-signed" not in repr([tuple(row) for row in db.execute(f"SELECT * FROM {name}")])


@pytest.mark.parametrize("protocol", ["anthropic", "openai-responses"])
async def test_native_clients_cross_format_and_ordinary_api_key_header(tmp_path, protocol):
    async with wire([native_text("openai")]) as (url, policy, _):
        async with standalone(tmp_path, policy) as (gateway, client):
            row = stored_connection(gateway, "owner", url)
            key = gateway.create_key("owner", name="SDK client")
            body = {"model": row["id"] + "/model", "messages": [{"role": "user", "content": "Hello"}], "max_tokens": 128} if protocol == "anthropic" else {
                "model": row["id"] + "/model", "input": "Hello"}
            response = await client.post("/v1/" + ("messages" if protocol == "anthropic" else "responses"),
                headers={"x-api-key": key["key"]} if protocol == "anthropic" else {"Authorization": "Bearer " + key["key"]}, json=body)
            assert response.status_code == 200, response.text
            result = response.json()
            assert "Hi there" in json_dumps(result)
            assert result["usage"]["input_tokens"] + result["usage"]["output_tokens"] == 10


@pytest.mark.parametrize("protocol", ["openai-responses", "gemini"])
async def test_native_refusals_are_recorded_as_refused(tmp_path, protocol):
    response = sse({"type": "response.completed", "response": {"id": "r", "status": "completed", "output": [
        {"id": "m", "type": "message", "role": "assistant", "content": [{"type": "refusal", "refusal": "Cannot help with this request."}]}]}}) if protocol == "openai-responses" else native_text("gemini", "SAFETY")
    body = {"input": "Hello"} if protocol == "openai-responses" else {"contents": [{"role": "user", "parts": [{"text": "Hello"}]}]}
    async with wire([response]) as (url, policy, _):
        gateway = ModelGateway(tmp_path, network_policy=policy)
        stored_connection(gateway, "owner", url, protocol=protocol)
        key = gateway.create_key("owner", name="Native client")
        records = [record async for record in gateway.stream_native(key["key"], {"model": "conn_native/model", **body}, protocol)]
        assert records
        assert gateway.store.usage("owner")["recent"][0]["status"] == "refused"


async def test_manual_endpoint_change_cannot_transfer_an_old_provider_secret(tmp_path):
    async with wire(server) as (first, policy, original):
        async with wire(server) as (second, _, changed):
            gateway = ModelGateway(tmp_path, network_policy=policy)
            prior = await connect(gateway, "owner", first)
            with pytest.raises(GatewayError) as caught:
                await gateway.connect("owner", preset_id="custom-openai", name="Changed endpoint", models=["model-a"],
                    endpoint=second, connection_id=prior["id"], expected_revision=prior["revision"], catalog_mode="manual")
            assert caught.value.code == "key_required"
            assert not changed
            assert gateway.connections("owner")[0]["endpoint"] == first + "/v1"


@asynccontextmanager
async def stalled_upstream(*, emit=True):
    disconnected, requests, tasks = asyncio.Event(), [], set()
    async def handle(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            lines = header.decode().split("\r\n")
            fields = dict(line.lower().split(": ", 1) for line in lines[1:] if ": " in line)
            request = json_loads(await reader.readexactly(int(fields.get("content-length", 0))))
            requests.append(request)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n" + (sse(oai({"content": "Visible before disconnect"})) if emit else b""))
            await writer.drain()
            await reader.read()
            disconnected.set()
        finally:
            writer.close()
            await writer.wait_closed()
            tasks.discard(task)
    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    try:
        yield url, NetworkPolicy(private_origins=(url,), timeout=5), disconnected, requests
    finally:
        server.close()
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await server.wait_closed()


async def test_external_http_disconnect_closes_provider_and_releases_both_slots(tmp_path):
    async with stalled_upstream() as (url, policy, closed, requests):
        async with standalone(tmp_path, policy) as (gateway, client):
            row = stored_connection(gateway, "owner", url)
            key = gateway.create_key("owner", name="Disconnecting client")
            async with client.stream("POST", "/v1/chat/completions", headers={"Authorization": "Bearer " + key["key"]},
                                     json=payload(model=row["id"] + "/model", stream=True)) as response:
                assert response.status_code == 200
                async for line in response.aiter_lines():
                    if "Visible before disconnect" in line:
                        break
            await asyncio.wait_for(closed.wait(), .75)
            for _ in range(100):
                if gateway._slots._value == 16 and policy._slots._value == 4 and gateway.store.usage("owner")["requests"] == 1:
                    break
                await asyncio.sleep(.005)
            assert gateway._slots._value == 16 and policy._slots._value == 4
            assert gateway.store.usage("owner")["recent"][0]["status"] == "cancelled"
            assert len(requests) == 1


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("protocol", ["openai", "anthropic", "openai-responses"])
async def test_external_http_disconnect_before_output_closes_upstream(tmp_path, stream, protocol):
    async with stalled_upstream(emit=False) as (url, policy, closed, requests):
        async with standalone(tmp_path, policy) as (gateway, client):
            stored_connection(gateway, "owner", url, protocol=protocol)
            key = gateway.create_key("owner", name="Client disappearing before output")
            path = {"openai": "chat/completions", "anthropic": "messages", "openai-responses": "responses"}[protocol]
            body = payload(model="conn_native/model", stream=stream) if protocol != "openai-responses" else {"model": "conn_native/model", "input": "Hello", "stream": stream}
            task = asyncio.create_task(client.post("/v1/" + path, headers={"Authorization": "Bearer " + key["key"]}, json=body))
            async def dispatched():
                while not requests:
                    await asyncio.sleep(.005)
            await asyncio.wait_for(dispatched(), 2)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.wait_for(closed.wait(), .75)
            for _ in range(100):
                if gateway._slots._value == 16 and gateway.store.usage("owner")["requests"] == 1:
                    break
                await asyncio.sleep(.005)
            assert gateway._slots._value == 16 and policy._slots._value == 4
            assert gateway.store.usage("owner")["recent"][0]["status"] == "cancelled"


async def test_tenant_key_account_and_management_authority_do_not_cross(tmp_path):
    async with wire(server) as (url, policy, upstream):
        async with standalone(tmp_path, policy) as (gateway, client):
            alice = stored_connection(gateway, "alice", url, identity="conn_shared")
            stored_connection(gateway, "bob", url, identity="conn_bob")
            ak = gateway.create_key("alice", name="Alice", models=[alice["id"] + "/model"])
            bk = gateway.create_key("bob", name="Bob")
            for key, model in ((ak["key"], "conn_bob/model"), (bk["key"], "conn_shared/model"), ("upstream-only-key", "conn_shared/model")):
                response = await client.post("/v1/chat/completions", headers={"Authorization": "Bearer " + key}, json=payload(model=model))
                assert response.status_code in {401, 403, 404}
            assert not upstream
            assert (await client.get("/api/v1/gateway/connections", headers={"Authorization": "Bearer " + ak["key"]})).status_code == 401
            models = await client.get("/v1/models", headers={"Authorization": "Bearer " + bk["key"]})
            assert [m["id"] for m in models.json()["data"]] == ["conn_bob/model"]
            assert not gateway.store.usage("alice")["requests"] and not gateway.store.usage("bob")["requests"]


@pytest.mark.parametrize("code", ["account_disconnected", "account_reauth_required", "auth_unavailable"])
async def test_expired_account_binding_fails_over_before_output_and_records_attempt(tmp_path, code):
    async with wire([ANSWER]) as (url, policy, upstream):
        gateway = ModelGateway(tmp_path, network_policy=policy)
        stored_connection(gateway, "owner", url, identity="conn_first", account="account-expired")
        stored_connection(gateway, "owner", url, identity="conn_second")
        gateway.save_route("owner", id="daily", name="Daily", models=["conn_first/model", "conn_second/model"])
        key = gateway.create_key("owner", name="Client", models=["daily"])
        async def unavailable(tenant, identity):
            raise GatewayError(code, "This account must sign in again.", 503)
        gateway.auth.credential = unavailable
        result = await gateway.complete(key["key"], payload(model="daily"))
        assert result["choices"][0]["message"]["content"] == "Gateway response"
        assert len(upstream) == 1
        assert gateway.store.usage("owner")["requests"] == 2
        assert gateway.store.usage("owner")["failures"] == 1


async def test_failed_consumer_model_switch_retires_original_active_client_key(tmp_path):
    service = ModelGateway(tmp_path)
    stored_connection(service, "owner", "https://provider.example/v1", models=("a", "b", "c"))
    async def command(agent_id, operation, body, request_id, timeout=None):
        if body["model"].endswith("/b"):
            raise CoreError("provider_unavailable", "Provider failed", worker_response=True)
        return {"active": True}
    adapter = ConsumerGateway.__new__(ConsumerGateway)
    adapter.service = service
    adapter.http = SimpleNamespace(endpoint="http://127.0.0.1:8787/model-gateway/v1")
    adapter.control = SimpleNamespace(lifecycle=SimpleNamespace(command=command))
    await adapter._bind("owner", "agent", "conn_native/a")
    first = service.store.get("owner", "client_binding", "agent")
    old_key = service.store.resolve("owner", first["secret_ref"])
    with pytest.raises(CoreError):
        await adapter._bind("owner", "agent", "conn_native/b")
    assert service.store.authenticate(old_key)
    await adapter._bind("owner", "agent", "conn_native/c")
    with pytest.raises(GatewayError) as caught:
        service.store.authenticate(old_key)
    assert caught.value.code == "unauthenticated"
    active = [row for row in service.store.keys("owner") if not row["revoked"]]
    assert len(active) == 1


@pytest.mark.parametrize("visible", [False, True])
async def test_actual_http_fallback_stops_after_any_visible_output(tmp_path, visible):
    def replies(request, number):
        if request["body"]["model"] == "bad":
            return sse(oai({"content": "Already visible"}), {"error": {"message": "secret-error"}}) if visible else (429, {"error": "secret-error"})
        return ANSWER
    async with wire(replies) as (url, policy, requests):
        async with standalone(tmp_path, policy) as (gateway, client):
            stored_connection(gateway, "owner", url, models=("bad", "good"))
            gateway.save_route("owner", id="default", name="Default", models=["conn_native/bad", "conn_native/good"])
            key = gateway.create_key("owner", name="Client", models=["default"])
            response = await client.post("/v1/chat/completions", headers={"Authorization": "Bearer " + key["key"]}, json=payload(model="default", stream=True))
            assert response.status_code == 200, response.text
            assert "secret-error" not in response.text
            if visible:
                assert "Already visible" in response.text and '"error"' in response.text
                assert "[DONE]" not in response.text and len(requests) == 1
            else:
                assert "Gateway response" in response.text and "[DONE]" in response.text and len(requests) == 2
            assert gateway.store.usage("owner")["requests"] == len(requests)
            assert gateway.store.usage("owner")["failures"] == 1


async def test_native_conflicting_credentials_are_rejected_before_upstream(tmp_path):
    async with wire([native_text("anthropic")]) as (url, policy, requests):
        async with standalone(tmp_path, policy) as (gateway, client):
            stored_connection(gateway, "owner", url, protocol="anthropic")
            one = gateway.create_key("owner", name="One")
            two = gateway.create_key("owner", name="Two")
            for headers in ([('x-api-key', one['key']), ('x-api-key', two['key'])],
                            [('x-api-key', one['key']), ('Authorization', 'Bearer ' + two['key'])]):
                response = await client.post("/v1/messages", headers=headers,
                    json={"model": "conn_native/model", "messages": [{"role": "user", "content": "Hello"}], "max_tokens": 10})
                assert response.status_code in {400, 401}
            assert not requests


async def test_same_model_capability_refresh_gets_a_new_idempotent_binding(tmp_path):
    service = ModelGateway(tmp_path)
    row = stored_connection(service, "owner", "https://provider.example/v1")
    receipts, calls = {}, []
    async def command(agent_id, operation, body, request_id, timeout=None):
        if request_id in receipts and receipts[request_id] != body:
            raise CoreError("conflict", "Request identifier was reused with different content", worker_response=True)
        receipts[request_id] = body
        calls.append(request_id)
        return {"active": True}
    adapter = ConsumerGateway.__new__(ConsumerGateway)
    adapter.service = service
    adapter.http = SimpleNamespace(endpoint="http://127.0.0.1:8787/model-gateway/v1")
    adapter.control = SimpleNamespace(lifecycle=SimpleNamespace(command=command))
    await adapter._bind("owner", "agent", "conn_native/model")
    row["models"][0]["context_limit"] = 65536
    service.store.put("owner", "connection", row["id"], row, row["revision"])
    await adapter._bind("owner", "agent", "conn_native/model")
    assert len(set(calls)) == 2
    await adapter._bind("owner", "agent", "conn_native/model")
    assert len(set(calls)) == 3
    assert len(service.store.keys("owner")) == 1


async def test_uncertain_model_selection_reuses_pending_request_before_new_retry(tmp_path):
    service = ModelGateway(tmp_path)
    stored_connection(service, "owner", "https://provider.example/v1")
    calls = []

    async def command(agent_id, operation, body, request_id, timeout=None):
        calls.append(request_id)
        if len(calls) == 1:
            raise CoreError("worker_indeterminate", "Worker receipt is uncertain")
        return {"active": True}

    adapter = ConsumerGateway.__new__(ConsumerGateway)
    adapter.service = service
    adapter.http = SimpleNamespace(endpoint="http://127.0.0.1:8787/model-gateway/v1")
    adapter.control = SimpleNamespace(lifecycle=SimpleNamespace(command=command))
    with pytest.raises(CoreError, match="uncertain"):
        await adapter._bind("owner", "agent", "conn_native/model")
    assert service.store.get("owner", "client_binding", "agent")["state"] == "pending"
    await adapter._bind("owner", "agent", "conn_native/model")
    assert calls[0] == calls[1]
    assert service.store.get("owner", "client_binding", "agent")["state"] == "active"
    await adapter._bind("owner", "agent", "conn_native/model")
    assert calls[2] != calls[1]
    assert len(service.store.keys("owner")) == 1


async def test_private_agent_rebind_applies_updated_catalog_limits(tmp_path):
    async with wire([ANSWER]) as (url, _, requests):
        endpoint = url + "/model-gateway/v1/chat/completions"
        config = replace(configuration(tmp_path, origins=(url,)), gateway_endpoint=endpoint)
        agent = AgentApplication(config)
        await agent.start()
        try:
            original = await agent.bind_gateway(endpoint, "stg_test_gateway_key", "conn/model", 32768, 1024, False)
            updated = await agent.bind_gateway(endpoint, "stg_test_gateway_key", "conn/model", 65536, 2048, False)
            assert updated["context_limit"] == 65536 and updated["output_limit"] == 2048
            assert updated["revision"] > original["revision"] and updated["active"]
            assert len(requests) == 2
        finally:
            await agent.close()


@pytest.mark.parametrize("outcome", ["recovered", "rejected_again", "refresh_failed"])
async def test_native_auth_401_refreshes_once_before_output(tmp_path, outcome):
    def replies(request, count):
        if count == 1 or outcome != "recovered":
            return 401, {"error": "private-provider-account-detail"}
        return native_text("openai-responses")
    async with wire(replies) as (url, policy, requests):
        gateway = ModelGateway(tmp_path, network_policy=policy)
        stored_connection(gateway, "owner", url, protocol="openai-responses", account="account")
        key = gateway.create_key("owner", name="Native OAuth client")
        refreshes, resolutions = [], []
        async def credential(tenant, account, *, force_refresh=False, expected_revision=None):
            resolutions.append(account)
            if force_refresh:
                refreshes.append(expected_revision)
                if outcome == "refresh_failed":
                    raise GatewayError("account_reauth_required", "Sign in again.", 401)
            return {"account_id": account, "account_revision": 7 + len(refreshes), "credential": "refreshed-token" if refreshes else "old-token",
                    "provider_id": "codex", "protocol": "openai-responses", "endpoint": url, "headers": {}}
        gateway.auth.credential = credential
        async def collect():
            return [record async for record in gateway.stream_native(key["key"], {"model": "conn_native/model", "input": "Hello"}, "openai-responses")]
        if outcome == "recovered":
            assert await collect()
        else:
            with pytest.raises(GatewayError) as caught:
                await collect()
            assert caught.value.code == ("account_reauth_required" if outcome == "refresh_failed" else "provider_auth")
            assert "private-provider-account-detail" not in str(caught.value)
        assert refreshes == [7]
        assert len(requests) == (1 if outcome == "refresh_failed" else 2)
        assert requests[0]["headers"]["authorization"] == "bearer old-token"
        if len(requests) == 2:
            assert requests[1]["headers"]["authorization"] == "bearer refreshed-token"
        assert gateway.store.usage("owner")["requests"] == len(requests)
        assert gateway._tenant_active["owner"] == 0 and gateway._slots._value == 16


@pytest.mark.parametrize("change", ["key", "connection", "route"])
async def test_queued_request_rechecks_authority_before_dispatch(tmp_path, change):
    async with wire([ANSWER]) as (url, policy, requests):
        gateway = ModelGateway(tmp_path, network_policy=policy, max_concurrency=1)
        row = stored_connection(gateway, "owner", url, models=("a", "b"))
        route = gateway.save_route("owner", id="default", name="Default", models=["conn_native/a"])
        key = gateway.create_key("owner", name="Queued client", models=["default"])
        await gateway._slots.acquire()
        task = asyncio.create_task(gateway.complete(key["key"], payload(model="default")))
        try:
            async def admitted():
                while gateway._tenant_active.get("owner", 0) != 1:
                    await asyncio.sleep(.005)
            await asyncio.wait_for(admitted(), 1)
            if change == "key":
                gateway.store.revoke_key("owner", key["id"])
            elif change == "connection":
                gateway.store.put("owner", "connection", row["id"], {**row, "name": "Updated connection"}, row["revision"])
            else:
                gateway.save_route("owner", id="default", name="Default", models=["conn_native/b"], expected_revision=route["revision"])
            gateway._slots.release()
            with pytest.raises(GatewayError) as caught:
                await task
            assert caught.value.code == ("unauthenticated" if change == "key" else "conflict")
            assert not requests and gateway.store.usage("owner")["requests"] == 0
            assert gateway._tenant_active["owner"] == 0 and gateway._slots._value == 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("waiting", ["tenant", "global"])
async def test_cancelling_queued_request_releases_only_its_own_admission(tmp_path, waiting):
    async with wire([ANSWER]) as (url, policy, requests):
        gateway = ModelGateway(tmp_path, network_policy=policy, max_concurrency=1)
        stored_connection(gateway, "owner", url)
        key = gateway.create_key("owner", name="Queued client")
        if waiting == "tenant":
            await gateway._admit("owner")
        else:
            await gateway._slots.acquire()
        task = asyncio.create_task(gateway.complete(key["key"], payload(model="conn_native/model")))
        try:
            async def queued():
                while True:
                    with gateway.store.transaction() as db:
                        row = db.execute("SELECT body FROM objects WHERE tenant='owner' AND kind='rate_window'").fetchone()
                    if row and json_loads(row[0])["count"] == (2 if waiting == "tenant" else 1) and (waiting == "tenant" or gateway._tenant_active.get("owner") == 1):
                        return
                    await asyncio.sleep(.005)
            await asyncio.wait_for(queued(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert gateway._tenant_active.get("owner", 0) == (1 if waiting == "tenant" else 0)
            assert gateway._slots._value == (1 if waiting == "tenant" else 0)
            assert not requests and gateway.store.usage("owner")["requests"] == 0
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if waiting == "tenant":
                await gateway._release("owner")
            else:
                gateway._slots.release()
        assert (await gateway.complete(key["key"], payload(model="conn_native/model")))["choices"][0]["message"]["content"] == "Gateway response"


@pytest.mark.parametrize("waiting", ["tenant", "global"])
async def test_cancel_event_interrupts_capacity_wait_without_dispatch(tmp_path, waiting):
    async with wire([ANSWER]) as (url, policy, requests):
        gateway = ModelGateway(tmp_path, network_policy=policy, max_concurrency=1)
        stored_connection(gateway, "owner", url)
        key = gateway.create_key("owner", name="Cancellable queued client")
        cancel = asyncio.Event()
        if waiting == "tenant":
            await gateway._admit("owner")
        else:
            await gateway._slots.acquire()
        stream = gateway.stream(key["key"], payload(model="conn_native/model"), cancel=cancel)
        task = asyncio.create_task(anext(stream))
        try:
            # Ensure admission has begun, then cancel without releasing capacity.
            async def queued():
                while True:
                    with gateway.store.transaction() as db:
                        row = db.execute("SELECT body FROM objects WHERE tenant='owner' AND kind='rate_window'").fetchone()
                    if row and json_loads(row[0])["count"] == (2 if waiting == "tenant" else 1):
                        return
                    await asyncio.sleep(.005)
            await asyncio.wait_for(queued(), 1)
            cancel.set()
            with pytest.raises(GatewayError) as caught:
                await asyncio.wait_for(task, .6)
            assert caught.value.code == "cancelled"
            assert not requests and gateway.store.usage("owner")["requests"] == 0
            assert gateway._tenant_active.get("owner", 0) == (1 if waiting == "tenant" else 0)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await stream.aclose()
            if waiting == "tenant":
                await gateway._release("owner")
            else:
                gateway._slots.release()


@pytest.mark.parametrize("host,public", [("127.0.0.1", None), ("::1", None), ("0.0.0.0", "https://gateway.example")])
def test_standalone_cli_composes_private_gateway_without_exposing_keys(tmp_path, monkeypatch, capsys, host, public):
    from model_gateway.__main__ import main
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: calls.append((app, options)))
    main(["serve", "--root", str(tmp_path), "--host", host, "--port", "8791", "--deny-local-servers",
          "--private-origin", "http://127.0.0.1:9000", *(["--public-origin", public] if public else [])])
    app, options = calls[0]
    assert options["host"] == host and options["port"] == 8791
    assert options["proxy_headers"] is False and options["access_log"] is False
    assert app.state.gateway.allow_local_servers is False
    assert "http://127.0.0.1:9000" in app.state.gateway.network.private_origins
    output = capsys.readouterr().out
    assert "Management key file:" in output
    assert app.state.auth.key_path.read_text() not in output
    assert (public or ("http://[::1]:8791" if host == "::1" else "http://127.0.0.1:8791")) in output


@pytest.mark.parametrize("options", [["--port", "0"], ["--port", "65536"], ["--host", "0.0.0.0"], ["--host", "::"]])
def test_standalone_cli_rejects_unbounded_or_ambiguous_listeners(tmp_path, monkeypatch, options):
    from model_gateway.__main__ import main
    def no_start(*args, **kwargs):
        pytest.fail("An invalid listener must not start the gateway")
    monkeypatch.setattr(uvicorn, "run", no_start)
    with pytest.raises(SystemExit) as caught:
        main(["serve", "--root", str(tmp_path), *options])
    assert caught.value.code == 2 and not (tmp_path / "gateway.sqlite").exists()


@pytest.mark.parametrize("refuse", [False, True])
async def test_manual_import_tests_real_inference_and_never_claims_discovery(tmp_path, refuse):
    def replies(request, count):
        if request["method"] == "GET":
            pytest.fail("Manual imports must not require an unavailable model catalog")
        return native_text("openai", "content_filter") if refuse else ANSWER
    async with wire(replies) as (url, policy, requests):
        async with standalone(tmp_path, policy) as (gateway, client):
            headers = {"Authorization": "Bearer " + (tmp_path / "admin.key").read_text()}
            response = await client.post("/api/v1/gateway/connections", headers=headers, json={
                "preset_id": "custom-openai", "name": "Catalogless provider", "endpoint": url,
                "api_key": "manual-provider-key", "models": ["manual-model"], "catalog_mode": "manual"})
            assert requests and all(request["method"] == "POST" for request in requests)
            assert requests[0]["body"]["max_tokens"] == 16
            if refuse:
                assert response.status_code == 502 and response.json()["error"]["code"] == "model_check_failed"
                assert not gateway.connections("local")
                assert gateway.store.usage("local")["recent"][0]["status"] == "failed"
            else:
                assert response.status_code == 200, response.text
                row = response.json()["data"]
                assert row["catalog_mode"] == "manual" and row["models"][0]["limits_source"] == "preset_default_manual_import"
                refreshed = await client.post(f"/api/v1/gateway/connections/{row['id']}/refresh", headers=headers, json={"expected_revision": row["revision"]})
                assert refreshed.status_code == 200 and refreshed.json()["data"]["revision"] == row["revision"] + 1
                key = gateway.create_key("local", name="Manual-model client")
                inference = await client.post("/v1/chat/completions", headers={"Authorization": "Bearer " + key["key"]}, json=payload(model=row["id"] + "/manual-model"))
                assert inference.status_code == 200 and "Gateway response" in inference.text
                assert gateway.store.usage("local")["requests"] == 3
