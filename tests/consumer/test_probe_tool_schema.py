"""Schema-driven upstream proves readiness and a real private-worker tool round trip."""
import asyncio
import base64
import json

import pytest

from agent.protocol import ToolCall
from agent.runtime import INVOKE_SCHEMA
from tests.consumer.test_api import PASSWORD, TOKEN, agent, command
from tests.consumer_integration.test_http import http_installation
from tests.gateway.test_transports import oai, sse, wire


PROOF = "A real workspace read reached the model."
PROOF_BASE64 = base64.b64encode(PROOF.encode()).decode()


def answer(text):
    return sse(oai({"content": text}, "stop"),
               {"choices": [], "usage": {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40}},
               b"[DONE]")


def declared_call(body, tool, arguments, *, completion_tokens=20):
    """A conforming upstream can emit only properties actually declared to it."""
    function = body["tools"][0]["function"]
    schema = function["parameters"]
    candidate = ToolCall(tool, arguments).as_dict()
    emitted = {key: candidate[key] for key in schema.get("properties", {}) if key in candidate}
    assert set(schema.get("required", [])) <= emitted.keys()
    return sse(oai({"tool_calls": [{"index": 0, "id": "schema-driven-call", "type": "function",
        "function": {"name": function["name"], "arguments": json.dumps(emitted)}}]}, "tool_calls"),
        {"choices": [], "usage": {"prompt_tokens": 50, "completion_tokens": completion_tokens,
                                   "total_tokens": 50 + completion_tokens}}, b"[DONE]")


@pytest.mark.parametrize(("supports_tools", "retry_after_fallback"), [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize("model_limit", [512, 2048])
async def test_use_model_and_chat_share_declared_tool_contract(tmp_path, supports_tools, retry_after_fallback, model_limit):
    observed = {"probe": 0, "conversation": 0, "tool_results": []}

    def upstream(request, _):
        if request["method"] == "GET":
            return {"data": [{"id": "schema-model", "context_length": 32768,
                "max_output_tokens": model_limit, "supported_parameters": ["tools"] if supports_tools else []}]}
        body = request["body"]
        prompt = body["messages"][0]["content"]
        if prompt == "Reply with OK." or prompt.startswith("Call invoke exactly once"):
            observed["probe"] += 1
            if supports_tools and body.get("tools"):
                expected = ToolCall("consumer.probe", {}).as_dict()
                assert json.loads(body["messages"][0]["content"].split("arguments: ", 1)[1]) == expected
                # This controlled model needs 300 completion tokens to form a
                # valid call. An undersized allowance reproduces the live short
                # malformed-call failure despite a tool_calls finish marker.
                if body["max_tokens"] < 300 or retry_after_fallback and observed["probe"] == 1:
                    return sse(oai({"tool_calls": [{"index": 0, "id": "initial-invalid-call", "type": "function",
                        "function": {"name": "invoke", "arguments": "{}"}}]}, "tool_calls"),
                        {"choices": [], "usage": {"prompt_tokens": 50, "completion_tokens": body["max_tokens"],
                                                   "total_tokens": 50 + body["max_tokens"]}}, b"[DONE]")
                assert body["max_tokens"] == min(model_limit, 1024)
                return declared_call(body, "consumer.probe", {}, completion_tokens=300)
            assert body["max_tokens"] == 128
            return answer("OK")
        observed["conversation"] += 1
        if not supports_tools:
            assert "tools" not in body
            return answer("The text-only model answered.")
        results = [message for message in body["messages"] if message["role"] == "tool"]
        if not results:
            return declared_call(body, "workspace.read", {"operation": "read", "path": "probe-proof.txt"})
        # Runtime context labels each observation; the ToolResult is its exact text body.
        observed["tool_results"].extend(json.loads(json.loads(message["content"])["text"]) for message in results)
        assert any(PROOF_BASE64 in message["content"] for message in results)
        assert any(result.get("state") == "completed" and result.get("outcome") == "success"
                   for result in observed["tool_results"])
        return answer("Read the workspace file successfully.")

    async with wire(upstream) as (url, _, requests):
        async with http_installation(tmp_path, url) as (client, _):
            session = await client.post("/api/v1/bootstrap", json={
                "email": "owner@example.com", "password": PASSWORD, "token": TOKEN})
            assert session.status_code == 200, session.text
            client.headers["Authorization"] = "Bearer " + session.json()["data"]["token"]
            connection = await client.post("/api/v1/gateway/connections", json={
                "preset_id": "custom-openai", "name": "Schema-driven provider", "endpoint": url + "/v1",
                "api_key": "controlled-upstream-key", "models": ["schema-model"]})
            assert connection.status_code == 200, connection.text
            alias = connection.json()["data"]["id"] + "/schema-model"
            owner = await agent(client)
            selected = await client.post(f"/api/v1/agents/{owner['id']}/gateway-model", json={"model": alias})
            assert selected.status_code == 200, selected.text
            ready = selected.json()["data"]
            if retry_after_fallback:
                assert ready["active"] and ready["ready"] and ready["tools"] is False
                assert observed["probe"] == 2, "The initial invalid tool call must fall back through a real text probe"
                selected = await client.post(f"/api/v1/agents/{owner['id']}/gateway-model", json={"model": alias})
                assert selected.status_code == 200, selected.text
                ready = selected.json()["data"]
                assert observed["probe"] == 3, "Explicit retry must perform a fresh tool probe instead of returning cached readiness"
            assert ready["active"] and ready["ready"]
            assert ready["tools"] is supports_tools
            assert ready["output_limit"] == model_limit
            assert observed["probe"] == (3 if retry_after_fallback else 1), "A valid declared tool call must not silently downgrade to text"
            probe = [row["body"] for row in requests if row["method"] == "POST"][0]
            assert probe["max_tokens"] == (min(model_limit, 1024) if supports_tools else 128)
            if supports_tools:
                assert probe["tools"] == INVOKE_SCHEMA
            else:
                assert "tools" not in probe

            await command(client, owner["id"], "files.write", {
                "path": "probe-proof.txt", "data_base64": PROOF_BASE64})
            room = await command(client, owner["id"], "conversations.create", {"title": "Tool schema proof"})
            await command(client, owner["id"], "messages.send", {
                "conversation_id": room["id"], "message_id": "schema-proof", "text": "Read the proof file if tools are available."})

            async def finished():
                while True:
                    history = await command(client, owner["id"], "conversations.get", {"id": room["id"]})
                    if history["turns"] and history["turns"][0]["state"] in {"completed", "failed"}:
                        return history
                    await asyncio.sleep(0.025)

            history = await asyncio.wait_for(finished(), 10)
            assert history["turns"][0]["state"] == "completed", history
            if supports_tools:
                assert "Read the workspace file successfully." in str(history)
                assert observed["conversation"] == 2 and len(observed["tool_results"]) == 1
                assert observed["tool_results"][0]["tool"] == "workspace.read"
                assert all(row["body"]["tools"] == INVOKE_SCHEMA for row in requests
                           if row["method"] == "POST" and row["body"].get("tools"))
            else:
                assert "The text-only model answered." in str(history)
                assert observed["conversation"] == 1 and observed["tool_results"] == []
