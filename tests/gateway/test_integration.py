"""Actual consumer HTTP -> independent gateway -> private agent -> upstream model."""
import asyncio

import pytest

from tests.consumer.test_api import TOKEN, PASSWORD, agent, command
from tests.consumer_integration.test_http import http_installation
from tests.gateway.test_service import server
from tests.gateway.test_transports import wire


async def test_gateway_before_agent_then_bind_and_chat_without_upstream_secret_leak(tmp_path):
    async with wire(server) as (url, policy, requests):
        async with http_installation(tmp_path, url) as (client, app):
            response = await client.post("/api/v1/bootstrap", json={"email": "owner@example.com", "password": PASSWORD, "token": TOKEN})
            client.headers["Authorization"] = "Bearer " + response.json()["data"]["token"]
            assert not (await client.get("/api/v1/agents")).json()["data"]["items"]
            response = await client.post("/api/v1/gateway/connections", json={"preset_id": "custom-openai", "name": "Gateway provider",
                "endpoint": url+"/v1", "api_key": "upstream-secret-in-gateway-only", "models": ["model-b"]})
            assert response.status_code == 200, response.text
            connection = response.json()["data"]
            owner = await agent(client)
            selected = await client.post(f"/api/v1/agents/{owner['id']}/gateway-model", json={"model": connection["id"]+"/model-b"})
            assert selected.status_code == 200, selected.text
            assert selected.json()["data"]["active"]
            repeat = await client.post(f"/api/v1/agents/{owner['id']}/gateway-model", json={"model": connection["id"]+"/model-b"})
            assert repeat.status_code == 200, repeat.text
            room = await command(client, owner["id"], "conversations.create", {"title": "Gateway test"})
            turn = await command(client, owner["id"], "messages.send", {"conversation_id": room["id"], "message_id": "gateway-live", "text": "Hello"})
            async def completed():
                while True:
                    history = await command(client, owner["id"], "conversations.get", {"id": room["id"]})
                    if history["turns"] and history["turns"][0]["state"] in {"completed", "failed"}: return history
                    await asyncio.sleep(.05)
            history = await asyncio.wait_for(completed(), 10)
            assert history["turns"][0]["state"] == "completed", history
            assert "Gateway response" in str(history)
            for path in (tmp_path / "runtimes" / owner["id"]).rglob("*.sqlite"):
                assert b"upstream-secret-in-gateway-only" not in path.read_bytes()
            usage = (await client.get("/api/v1/gateway/usage")).json()["data"]
            assert usage["requests"] >= 2
