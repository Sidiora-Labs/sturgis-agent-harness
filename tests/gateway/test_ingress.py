"""Native external client requests traverse the real upstream wire adapters."""
import pytest

from model_gateway.errors import GatewayError
from model_gateway.ingress import collect_chat, from_chat, from_completion, to_chat
from model_gateway.transports import GatewayTransport, discover_models
from model_gateway.wire import json_dumps
from tests.gateway.test_transports import function_stream, native_text, payload, wire


async def chunks(*values):
    for value in values:
        yield value


def chunk(delta=None, finish=None, usage=None):
    return {"id": "chatcmpl-test", "created": 1234, "model": "model", "choices":
            [{"index": 0, "delta": delta or {}, "finish_reason": finish}] if usage is None else [],
            **({"usage": usage} if usage is not None else {})}


def native_request(protocol):
    if protocol == "anthropic":
        return {"model": "model", "messages": [{"role": "user", "content": "Hello"}], "max_tokens": 128, "stream": True}
    return {"model": "model", "input": "Hello", "max_output_tokens": 128, "stream": True, "store": False}


@pytest.mark.parametrize("client", ["anthropic", "openai-responses"])
@pytest.mark.parametrize("upstream", ["openai", "anthropic", "gemini", "openai-responses"])
async def test_native_clients_stream_text_through_each_wire(upstream, client):
    async with wire([native_text(upstream), native_text(upstream)]) as (url, policy, requests):
        transport = GatewayTransport(policy)
        body = to_chat(client, native_request(client))
        events = [e async for e in from_chat(client, transport.stream(upstream, url, body))]
        assert "Hi there" in json_dumps(events) or client == "anthropic"
        assert events[-1]["event"] == ("message_stop" if client == "anthropic" else "response.completed")
        response = await collect_chat(client, transport.stream(upstream, url, body))
        if client == "anthropic":
            assert response["content"][0]["text"] == "Hi there"
            assert response["stop_reason"] == "end_turn"
        else:
            assert response["output"][0]["content"][0]["text"] == "Hi there"
            sequence = [e["data"]["sequence_number"] for e in events]
            assert sequence == list(range(len(sequence)))
            emitted = [e["data"]["item"] for e in events if e["event"] == "response.output_item.done"]
            assert events[-1]["data"]["response"]["output"] == emitted
        assert response["usage"]["input_tokens"] + response["usage"]["output_tokens"] == 10
        assert len(requests) == 2


@pytest.mark.parametrize("client", ["anthropic", "openai-responses"])
@pytest.mark.parametrize("upstream", ["openai", "anthropic", "gemini", "openai-responses"])
async def test_native_function_results_through_each_wire(upstream, client):
    async with wire([function_stream(upstream)]) as (url, policy, _):
        transport = GatewayTransport(policy)
        response = await collect_chat(client, transport.stream(upstream, url, to_chat(client, native_request(client))))
        if client == "anthropic":
            assert response["stop_reason"] == "tool_use"
            assert response["content"][0]["input"] == {"city": "Paris"}
            assert response["content"][0]["name"] == "weather"
        else:
            assert response["output"][0]["name"] == "weather"
            assert response["output"][0]["arguments"] == '{"city":"Paris"}'


def test_messages_input_images_parallel_tools_and_error_results():
    body = {"model": "model", "system": [{"type": "text", "text": "rules"}], "max_tokens": 100, "temperature": .2, "top_p": .8,
        "stop_sequences": ["END"], "tools": [{"name": "echo", "description": "Echo", "input_schema": {"type": "object"}}],
        "tool_choice": {"type": "tool", "name": "echo", "disable_parallel_tool_use": True},
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "YQ=="}},
            {"type": "image", "source": {"type": "url", "url": "https://example.com/a.png"}}]},
            {"role": "assistant", "content": [{"type": "text", "text": "Checking"}, {"type": "tool_use", "id": "call1", "name": "echo", "input": {"value": "hi"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call1", "content": [{"type": "text", "text": "Failed"}], "is_error": True}]}]}
    result = to_chat("anthropic", body)
    assert result["parallel_tool_calls"] is False
    assert result["messages"][-1]["role"] == "tool" and "error" in result["messages"][-1]["content"]
    assert result["messages"][2]["tool_calls"][0]["function"]["name"] == "echo"
    assert result["tools"][0]["function"]["parameters"] == {"type": "object"}
    assert result["stop"] == ["END"]
    for choice, target in (("any", "required"), ("auto", "auto"), ("none", "none")):
        assert to_chat("anthropic", {**body, "tool_choice": {"type": choice}})["tool_choice"] == target


def test_responses_history_and_structured_output_translation():
    body = {"model": "m", "instructions": "rules", "input": [{"type": "message", "role": "user", "content":
        [{"type": "input_text", "text": "hello"}, {"type": "input_image", "image_url": "data:image/png;base64,YQ=="}]},
        {"type": "function_call", "call_id": "c", "name": "echo", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c", "output": {"answer": 1}},
        {"role": "assistant", "content": [{"type": "refusal", "refusal": "No"}]}],
        "tools": [{"type": "function", "name": "echo", "parameters": {"type": "object"}, "strict": True}],
        "tool_choice": {"type": "function", "name": "echo"}, "reasoning": {"effort": "low"},
        "text": {"format": {"type": "json_schema", "name": "answer", "schema": {"type": "object"}}}}
    result = to_chat("openai-responses", body)
    assert result["messages"][0]["role"] == "system" and result["reasoning_effort"] == "low"
    assert result["messages"][3]["content"] == '{"answer":1}'
    assert result["response_format"]["json_schema"]["name"] == "answer"
    for choice in ("auto", "required", "none"):
        assert to_chat("openai-responses", {**body, "tool_choice": choice, "text": {"format": {"type": "json_object"}}})["tool_choice"] == choice


@pytest.mark.parametrize("protocol,mutation", [("anthropic", {"thinking": {"type": "adaptive"}}), ("anthropic", {"store": True}),
    ("openai-responses", {"previous_response_id": "resp_old"}), ("openai-responses", {"background": True}),
    ("openai-responses", {"conversation": "server-owned"}), ("anthropic", {"tools": [{"type": "web_search"}]}),
    ("openai-responses", {"tools": [{"type": "web_search"}]}), ("anthropic", {"tool_choice": {"type": "bad"}}),
    ("openai-responses", {"tool_choice": {"type": "bad"}}), ("openai-responses", {"input": 123}),
    ("openai-responses", {"input": [{"type": "item_reference", "id": "remote"}]}),
    ("openai-responses", {"input": [{"role": "user", "content": [{"type": "input_image", "file_id": "remote"}]}]}),
    ("anthropic", {"messages": [{"role": "user", "content": [{"type": "tool_use", "id": "c", "name": "a", "input": {}}]}]}),
    ("anthropic", {"messages": [{"role": "assistant", "content": [{"type": "tool_result", "tool_use_id": "c"}]}]}),
    ("anthropic", {"messages": [{"role": "assistant", "content": [{"type": "thinking", "thinking": "private", "signature": "opaque"}]}]}),
    ("anthropic", {"messages": [{"role": "user", "content": [{"type": "image", "source": {"type": "unknown"}}]}]})])
def test_unsupported_native_requests_fail_explicitly(protocol, mutation):
    with pytest.raises(GatewayError) as caught:
        to_chat(protocol, {**native_request(protocol), **mutation})
    assert caught.value.code == "unsupported_request"


@pytest.mark.parametrize("body", [None, {}, {"model": "m", "input": [{}]}, {"model": "m", "input": [{"role": "user", "content": 1}]},
    {"model": "m", "input": [{"role": "user", "content": [None]}]}])
def test_bad_native_shapes_are_safe(body):
    with pytest.raises(GatewayError):
        to_chat("openai-responses", body)
    with pytest.raises(GatewayError):
        to_chat("unknown", native_request("anthropic"))


@pytest.mark.parametrize("protocol", ["anthropic", "openai-responses"])
@pytest.mark.parametrize("finish", ["length", "content_filter"])
async def test_native_incomplete_and_refusal_keep_terminal_semantics(protocol, finish):
    result = await collect_chat(protocol, chunks(chunk({"content": "Partial", "refusal": "No"}),
        chunk({"tool_calls": [{"index": 0, "id": "call", "function": {"name": "a", "arguments": "{"}}]}),
        chunk(finish=finish), chunk(usage={"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5})))
    if protocol == "anthropic":
        assert result["stop_reason"] == ("max_tokens" if finish == "length" else "refusal")
        assert all(block["type"] == "text" for block in result["content"])
    else:
        assert result["status"] == "incomplete"
        assert result["output"][-1]["status"] == "incomplete"


@pytest.mark.parametrize("protocol", ["anthropic", "openai-responses"])
async def test_native_tool_name_fragments_and_unknown_usage(protocol):
    result = await collect_chat(protocol, chunks(
        chunk({"tool_calls": [{"index": 0, "id": "c", "function": {"name": "weath"}}]}),
        chunk({"tool_calls": [{"index": 0, "function": {"name": "er", "arguments": "{"}}]}),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": "}"}}]}), chunk(finish="tool_calls")))
    assert (result["content"] if protocol == "anthropic" else result["output"])[0]["name"] == "weather"
    assert result["usage"] == ({} if protocol == "anthropic" else None)


async def test_stream_disconnect_closes_upstream_and_missing_finish_rejected():
    closed = []
    async def upstream():
        try:
            yield chunk({"content": "Hello"})
            yield chunk(finish="stop")
        finally:
            closed.append(True)
    translated = from_chat("anthropic", upstream())
    await anext(translated)
    await translated.aclose()
    assert closed == [True]
    for source in (chunks(), chunks(chunk({"content": "incomplete"}))):
        with pytest.raises(GatewayError) as caught:
            await collect_chat("anthropic", source)
        assert caught.value.code == "provider_incomplete"
    with pytest.raises(GatewayError):
        await collect_chat("bad", chunks())


def test_nonstream_completion_mapper():
    completion = {"id": "one", "created": 123, "model": "model", "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        "choices": [{"finish_reason": "tool_calls", "message": {"content": "Hello", "refusal": "No", "tool_calls":
            [{"id": "call", "function": {"name": "tool", "arguments": "{}"}}]}}]}
    assert from_completion("anthropic", completion)["content"][-1]["input"] == {}
    assert from_completion("openai-responses", completion)["output"][-1]["arguments"] == "{}"
    with pytest.raises(GatewayError):
        from_completion("bad", completion)


async def test_discovery_omits_specialized_nonchat_models():
    catalog = {"data": [{"id": "text-embedding-3-large"}, {"id": "dall-e-3"}, {"id": "gpt-realtime"},
        {"id": "audio-only", "architecture": {"output_modalities": ["audio"]}}, {"id": "not-chat", "supports_chat": False},
        {"id": "chat", "supports_tools": True}, {"id": "unknown"}]}
    async with wire([catalog]) as (url, policy, _):
        models = await discover_models("openai", url, None, policy)
        assert [m["id"] for m in models] == ["chat", "unknown"]
        assert models[0]["tools"] is True and "tools" not in models[1]
