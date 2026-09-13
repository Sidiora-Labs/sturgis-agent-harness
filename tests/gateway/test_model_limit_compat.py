"""Advertised model limits survive consumer activation and actual conversation I/O."""
import asyncio

import pytest

from model_gateway.errors import GatewayError
from model_gateway.service import ModelGateway
from tests.consumer.test_api import PASSWORD, TOKEN, agent, command
from tests.consumer_integration.test_http import http_installation
from tests.gateway.test_service import ANSWER
from tests.gateway.test_transports import wire


MODEL = "deepseek/deepseek-v4.1-flash"
CONTEXT = 1_048_576
OUTPUT = 384_000
CATALOG = {"data": [{"id": MODEL, "context_length": CONTEXT,
                      "top_provider": {"max_completion_tokens": OUTPUT}, "supported_parameters": []}]}


def upstream(request, _):
    return CATALOG if request["method"] == "GET" else ANSWER


async def test_actual_use_model_then_worker_chat_preserves_large_advertised_output(tmp_path):
    async with wire(upstream) as (url, _, requests):
        async with http_installation(tmp_path, url) as (client, _):
            session = await client.post("/api/v1/bootstrap", json={
                "email": "owner@example.com", "password": PASSWORD, "token": TOKEN})
            assert session.status_code == 200, session.text
            client.headers["Authorization"] = "Bearer " + session.json()["data"]["token"]
            connection = await client.post("/api/v1/gateway/connections", json={
                "preset_id": "custom-openai", "name": "Large output model", "endpoint": url + "/v1",
                "api_key": "controlled-provider-key", "models": [MODEL]})
            assert connection.status_code == 200, connection.text
            imported = connection.json()["data"]
            assert imported["models"][0]["context_limit"] == CONTEXT
            assert imported["models"][0]["output_limit"] == OUTPUT
            alias = imported["id"] + "/" + MODEL
            owner = await agent(client)
            selected = await client.post(f"/api/v1/agents/{owner['id']}/gateway-model", json={"model": alias})
            assert selected.status_code == 200, selected.text
            assert selected.json()["data"]["active"]
            assert selected.json()["data"]["context_limit"] == CONTEXT
            assert selected.json()["data"]["output_limit"] == OUTPUT
            assert [row["body"]["max_tokens"] for row in requests if row["method"] == "POST"] == [128]

            room = await command(client, owner["id"], "conversations.create", {"title": "Advertised limit"})
            await command(client, owner["id"], "messages.send", {
                "conversation_id": room["id"], "message_id": "large-output-chat", "text": "Hello"})

            async def finished():
                while True:
                    history = await command(client, owner["id"], "conversations.get", {"id": room["id"]})
                    if history["turns"] and history["turns"][0]["state"] in {"completed", "failed"}:
                        return history
                    await asyncio.sleep(0.025)

            history = await asyncio.wait_for(finished(), 10)
            assert history["turns"][0]["state"] == "completed", history
            assert "Gateway response" in str(history)
            sent = [row["body"] for row in requests if row["method"] == "POST"]
            assert [row["max_tokens"] for row in sent] == [128, OUTPUT]
            assert all(row["model"] == MODEL for row in sent)

            # Public native clients use the same unchanged imported capability.
            created = await client.post("/api/v1/gateway/keys", json={"name": "Native limit client", "models": [alias]})
            assert created.status_code == 200, created.text
            headers = {"Authorization": "Bearer " + created.json()["data"]["key"]}
            native = [
                ("/model-gateway/v1/messages", {"model": alias, "max_tokens": OUTPUT,
                    "messages": [{"role": "user", "content": "Hello"}]}),
                ("/model-gateway/v1/responses", {"model": alias, "max_output_tokens": OUTPUT, "input": "Hello"}),
                ("/model-gateway/v1/chat/completions", {"model": alias, "max_completion_tokens": OUTPUT,
                    "messages": [{"role": "user", "content": "Hello"}]}),
            ]
            for path, payload in native:
                response = await client.post(path, json=payload, headers=headers)
                assert response.status_code == 200, response.text
                assert "Gateway response" in response.text
                last = requests[-1]["body"]
                assert last.get("max_completion_tokens", last.get("max_tokens")) == OUTPUT
            listing = (await client.get("/api/v1/gateway/models")).json()["data"]
            assert listing[0]["context_limit"] == CONTEXT and listing[0]["output_limit"] == OUTPUT


@pytest.mark.parametrize("field", ["max_tokens", "max_completion_tokens"])
@pytest.mark.parametrize("value,code", [(True, "invalid_request"), (0, "invalid_request"),
    (-1, "invalid_request"), (384000.0, "invalid_request"), ("384000", "invalid_request"),
    (2_000_001, "invalid_request"), (2**100, "invalid_request"), (OUTPUT + 1, "output_limit"),
    (2_000_000, "output_limit")])
async def test_limits_remain_integer_bounded_and_cannot_exceed_imported_model(tmp_path, field, value, code):
    async with wire(upstream) as (url, policy, requests):
        gateway = ModelGateway(tmp_path, network_policy=policy)
        row = await gateway.connect("owner", preset_id="custom-openai", name="Limits", endpoint=url + "/v1",
                                    api_key="controlled-key", models=[MODEL])
        alias = row["id"] + "/" + MODEL
        key = gateway.create_key("owner", name="Client", models=[alias])["key"]
        with pytest.raises(GatewayError) as caught:
            await gateway.complete(key, {"model": alias, "messages": [{"role": "user", "content": "Hello"}], field: value})
        assert caught.value.code == code
        assert all(request["method"] == "GET" for request in requests)
