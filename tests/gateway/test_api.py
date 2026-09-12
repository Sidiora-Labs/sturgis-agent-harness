"""Standalone management and independent inference through real upstream sockets."""
from contextlib import asynccontextmanager
import importlib
import sys

import httpx
import pytest

from model_gateway.api import create_app
from model_gateway.errors import GatewayError
from tests.gateway.test_service import server
from tests.gateway.test_transports import wire


@asynccontextmanager
async def installation(tmp_path):
    async with wire(server) as (url, policy, requests):
        app = create_app(tmp_path, public_origin="http://gateway.test", network_policy=policy)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway.test") as client:
            yield client, app, url, requests


async def setup(client, app, url):
    client.headers["Authorization"] = "Bearer " + app.state.auth.key_path.read_text()
    response = await client.post("/api/v1/gateway/connections", json={"preset_id": "custom-openai", "name": "Wire",
        "endpoint": url+"/v1", "api_key": "upstream-secret", "models": ["model-a"]})
    assert response.status_code == 200, response.text
    connection = response.json()["data"]
    response = await client.post("/api/v1/gateway/keys", json={"name": "External client", "models": [connection["id"]+"/model-a"]})
    assert response.status_code == 200, response.text
    return connection, response.json()["data"]


async def test_gateway_is_usable_without_agent_accounts_or_workers(tmp_path):
    async with installation(tmp_path) as (client, app, url, requests):
        assert (await client.get("/health")).json()["version"] == "gateway/1"
        assert (await client.get("/api/v1/gateway/connections")).status_code == 401
        connection, key = await setup(client, app, url)
        assert (await client.get("/api/v1/gateway/status")).json()["data"]["endpoint"] == "http://gateway.test/v1"
        assert (await client.get("/api/v1/gateway/presets")).json()["data"]
        management = client.headers.pop("Authorization")
        assert (await client.get("/v1/models")).status_code == 401
        client.headers["Authorization"] = "Bearer " + key["key"]
        model = connection["id"] + "/model-a"
        assert (await client.get("/v1/models")).json()["data"][0]["id"] == model
        assert (await client.get("/api/v1/gateway/connections")).status_code == 401
        payload = {"model": model, "messages": [{"role": "user", "content": "Hello"}]}
        response = await client.post("/v1/chat/completions", json=payload)
        assert response.status_code == 200 and response.json()["choices"][0]["message"]["content"] == "Gateway response"
        streamed = await client.post("/v1/chat/completions", json={**payload, "stream": True})
        assert streamed.status_code == 200 and "data: [DONE]" in streamed.text
        assert "upstream-secret" not in streamed.text
        client.headers["Authorization"] = management
        assert (await client.get("/api/v1/gateway/usage")).json()["data"]["requests"] == 2
        assert "key" not in (await client.get("/api/v1/gateway/keys")).json()["data"][0]
        assert not (tmp_path / "runtimes").exists()


async def test_admin_browser_session_csrf_and_logout(tmp_path):
    async with installation(tmp_path) as (client, app, url, requests):
        assert not (await client.get("/api/v1/gateway/session")).json()["data"]["authenticated"]
        assert (await client.post("/api/v1/gateway/session", json={"admin_key": app.state.auth.key_path.read_text()})).status_code == 403
        client.headers["Origin"] = "http://gateway.test"
        assert (await client.post("/api/v1/gateway/session", json={"admin_key": "wrong"})).status_code == 401
        logged = await client.post("/api/v1/gateway/session", json={"admin_key": app.state.auth.key_path.read_text()})
        assert logged.json()["data"]["authenticated"]
        assert "HttpOnly" in logged.headers.get_list("set-cookie")[0]
        assert (await client.get("/api/v1/gateway/connections")).status_code == 200
        assert (await client.post("/api/v1/gateway/keys", json={"name": "Client"})).status_code == 401
        client.headers["X-CSRF-Token"] = client.cookies["sturgis_csrf"]
        assert (await client.post("/api/v1/gateway/keys", json={"name": "Client"})).status_code == 200
        assert (await client.delete("/api/v1/gateway/session")).status_code == 200
        assert (await client.get("/api/v1/gateway/connections")).status_code == 401


async def test_management_crud_and_key_revocation(tmp_path):
    async with installation(tmp_path) as (client, app, url, requests):
        connection, key = await setup(client, app, url)
        alias = connection["id"]+"/model-a"
        saved = await client.post("/api/v1/gateway/routes", json={"id": "everyday", "name": "Everyday", "models": [alias]})
        assert saved.status_code == 200
        assert (await client.get("/api/v1/gateway/routes")).json()["data"][0]["id"] == "everyday"
        assert len((await client.get("/api/v1/gateway/models")).json()["data"]) == 2
        refreshed = await client.post(f"/api/v1/gateway/connections/{connection['id']}/refresh", json={"expected_revision": 1})
        assert refreshed.status_code == 200
        edited = await client.patch(f"/api/v1/gateway/connections/{connection['id']}", json={"preset_id": "custom-openai", "name": "Updated",
            "models": ["model-a"], "endpoint": url+"/v1", "expected_revision": 2})
        assert edited.json()["data"]["name"] == "Updated"
        assert (await client.delete(f"/api/v1/gateway/connections/{connection['id']}", json={"expected_revision": 2}) if False else
                await client.request("DELETE", f"/api/v1/gateway/connections/{connection['id']}", json={"expected_revision": 2})).status_code == 409
        assert (await client.request("DELETE", f"/api/v1/gateway/connections/{connection['id']}", json={"expected_revision": 3})).status_code == 200
        assert (await client.delete("/api/v1/gateway/keys/"+key["id"])).status_code == 200
        client.headers["Authorization"] = "Bearer " + key["key"]
        assert (await client.get("/v1/models")).status_code == 401


@pytest.mark.parametrize("path,method,body,status", [
    ("/api/v1/gateway/unknown", "GET", None, 404),
    ("/api/v1/gateway/discover", "POST", {}, 400),
    ("/api/v1/gateway/connections", "POST", {"tenant": "other"}, 400),
    ("/v1/chat/completions", "POST", {"model": "missing", "messages": []}, 400),
])
async def test_invalid_management_requests_are_sanitized(tmp_path, path, method, body, status):
    async with installation(tmp_path) as (client, app, url, requests):
        _, key = await setup(client, app, url)
        if path.startswith("/v1/"): client.headers["Authorization"] = "Bearer " + key["key"]
        response = await client.request(method, path, json=body)
        assert response.status_code == status
        assert "upstream-secret" not in response.text and not any(r["method"] == "POST" for r in requests)


async def test_admin_host_origin_ambiguous_auth_and_rate_limit(tmp_path):
    async with installation(tmp_path) as (client, app, url, requests):
        client.headers["Authorization"] = "Bearer " + app.state.auth.key_path.read_text()
        assert (await client.get("/api/v1/gateway/connections", headers={"Host": "evil.test"})).status_code == 403
        assert (await client.get("/api/v1/gateway/connections", headers={"Origin": "https://evil.test"})).status_code == 403
        client.headers.pop("Authorization")
        for _ in range(10):
            assert (await client.post("/api/v1/gateway/session", headers={"Origin": "http://gateway.test"}, json={"admin_key": "wrong"})).status_code == 401
        assert (await client.post("/api/v1/gateway/session", headers={"Origin": "http://gateway.test"}, json={"admin_key": "wrong"})).status_code == 429
