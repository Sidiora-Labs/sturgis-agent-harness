"""OpenRouter final usage envelopes retain accounting without reopening output."""
import asyncio

import pytest

from agent.protocol import ToolCall, canonical_json
from model_gateway.errors import GatewayError
from model_gateway.translation import StreamTranslator
from tests.consumer.test_api import PASSWORD, TOKEN, agent, command
from tests.consumer_integration.test_http import http_installation
from tests.gateway.test_transports import collect, oai, sse, wire


USAGE = {"prompt_tokens": 34, "completion_tokens": 14, "total_tokens": 48}


def terminal_choice(reason="stop"):
    delta = {"content": "OK"}
    if reason == "tool_calls":
        delta = {"tool_calls": [{"index": 0, "id": "call_probe", "type": "function",
            "function": {"name": "invoke", "arguments": canonical_json(ToolCall("consumer.probe", {}).as_dict())}}]}
    return oai(delta, reason)


def usage_tail(reason="stop", delta=None, **extra):
    return oai({"role": "assistant", "content": ""} if delta is None else delta,
               reason, usage=USAGE, **extra)


@pytest.mark.parametrize("reason", ["stop", "tool_calls", "length", "content_filter"])
@pytest.mark.parametrize("delta", [{"role": "assistant", "content": ""}, {},
    {"role": None, "content": None, "refusal": None, "tool_calls": None},
    {"role": "assistant", "content": "", "refusal": "", "tool_calls": []}])
def test_empty_usage_choice_retains_one_terminal_and_factual_final_usage(reason, delta):
    translator = StreamTranslator("openai", "openai/example-latest", {})
    output = translator.feed(terminal_choice(reason))
    assert translator.feed(usage_tail(reason, delta)) == []
    # Duplicate accounting does not duplicate the terminal or consumption.
    assert translator.feed(usage_tail(reason, delta)) == []
    output.extend(translator.end(True))
    assert [choice["finish_reason"] for chunk in output for choice in chunk["choices"]
            if choice["finish_reason"] is not None] == [reason]
    assert [chunk["usage"] for chunk in output if "usage" in chunk] == [USAGE]


@pytest.mark.parametrize("tail", [
    oai({}, "stop"),  # A repeated completion without accounting is not this extension.
    usage_tail("length"), usage_tail(None),
    usage_tail(delta={"content": "New output"}),
    usage_tail(delta={"refusal": "New refusal"}),
    usage_tail(delta={"tool_calls": [{"index": 0, "function": {"name": "invoke"}}]}),
    usage_tail(delta={"role": "user"}),
    usage_tail(delta={"reasoning": "New private output"}),
    usage_tail(delta={"content": []}), usage_tail(delta={"refusal": False}),
    usage_tail(delta={"tool_calls": {}}),
    {"usage": USAGE, "choices": [{"index": 1, "delta": {}, "finish_reason": "stop"}]},
    {"usage": USAGE, "choices": [{"index": False, "delta": {}, "finish_reason": "stop"}]},
    {"usage": USAGE, "choices": [{"index": 0, "delta": None, "finish_reason": "stop"}]},
])
def test_usage_tail_never_reopens_or_changes_completion(tail):
    translator = StreamTranslator("openai", "model", {})
    translator.feed(terminal_choice())
    with pytest.raises(GatewayError) as caught:
        translator.feed(tail)
    assert caught.value.code == "provider_protocol"


def test_final_usage_cannot_move_backwards_or_hide_missing_done():
    translator = StreamTranslator("openai", "model", {})
    translator.feed({**terminal_choice(), "usage": USAGE})
    with pytest.raises(GatewayError, match="backwards"):
        translator.feed({**usage_tail(), "usage": {"prompt_tokens": 34, "completion_tokens": 13, "total_tokens": 47}})
    fresh = StreamTranslator("openai", "model", {})
    fresh.feed(terminal_choice())
    fresh.feed(usage_tail())
    with pytest.raises(GatewayError) as caught:
        fresh.end(False)
    assert caught.value.code == "provider_incomplete"


async def test_actual_openrouter_wire_final_usage_reaches_standard_chat_client():
    response = sse(oai({"role": "assistant"}), oai({"content": "OK"}),
                   oai({}, "stop"), usage_tail(), b"[DONE]")
    async with wire([response]) as (url, policy, requests):
        chunks = await collect("openai", url + "/v1", policy)
    assert "".join(choice["delta"].get("content", "") for chunk in chunks for choice in chunk["choices"]) == "OK"
    assert chunks[-1]["usage"] == USAGE
    assert len([choice for chunk in chunks for choice in chunk["choices"] if choice["finish_reason"]]) == 1
    assert len(requests) == 1


@pytest.mark.parametrize("tools", [False, True])
async def test_consumer_use_model_and_chat_accept_openrouter_final_usage(tmp_path, tools):
    model_id = "openai/compatibility-latest"

    def upstream(request, count):
        if request["method"] == "GET":
            return {"data": [{"id": model_id, "name": "OpenRouter compatibility model",
                "context_length": 32768, "top_provider": {"max_completion_tokens": 1024},
                "supported_parameters": ["tools"] if tools else []}]}
        checking_tool = request["body"]["messages"][0]["content"].startswith("Call invoke exactly once")
        reason = "tool_calls" if checking_tool else "stop"
        return sse(terminal_choice(reason), usage_tail(reason), b"[DONE]")

    async with wire(upstream) as (url, _, requests):
        async with http_installation(tmp_path, url) as (client, _):
            session = await client.post("/api/v1/bootstrap", json={
                "email": "owner@example.com", "password": PASSWORD, "token": TOKEN})
            assert session.status_code == 200, session.text
            client.headers["Authorization"] = "Bearer " + session.json()["data"]["token"]
            connection = await client.post("/api/v1/gateway/connections", json={
                "preset_id": "custom-openai", "name": "OpenRouter wire", "endpoint": url + "/v1",
                "api_key": "test-upstream-key", "models": [model_id]})
            assert connection.status_code == 200, connection.text
            selected_id = connection.json()["data"]["id"] + "/" + model_id
            owner = await agent(client)
            selected = await client.post(f"/api/v1/agents/{owner['id']}/gateway-model", json={"model": selected_id})
            assert selected.status_code == 200, selected.text
            assert selected.json()["data"]["active"] and selected.json()["data"]["tools"] is tools
            probe_requests = [request for request in requests if request["method"] == "POST"]
            assert len(probe_requests) == 1  # No failed check or text-only downgrade.
            assert probe_requests[0]["body"]["max_tokens"] == (1024 if tools else 128)
            usage = (await client.get("/api/v1/gateway/usage")).json()["data"]
            assert usage["requests"] == 1 and usage["failures"] == 0 and usage["total_tokens"] == 48
            room = await command(client, owner["id"], "conversations.create", {"title": "OpenRouter compatibility"})
            await command(client, owner["id"], "messages.send", {
                "conversation_id": room["id"], "message_id": "openrouter-chat", "text": "Hello"})

            async def completed():
                while True:
                    history = await command(client, owner["id"], "conversations.get", {"id": room["id"]})
                    if history["turns"] and history["turns"][0]["state"] in {"completed", "failed"}:
                        return history
                    await asyncio.sleep(.05)

            history = await asyncio.wait_for(completed(), 10)
            assert history["turns"][0]["state"] == "completed", history
            assert "OK" in str(history)
            usage = (await client.get("/api/v1/gateway/usage")).json()["data"]
            assert usage["requests"] == 2 and usage["failures"] == 0 and usage["total_tokens"] == 96
            agent_usage = await command(client, owner["id"], "usage.summary", {})
            assert agent_usage["actual"]["tokens"] == 96
