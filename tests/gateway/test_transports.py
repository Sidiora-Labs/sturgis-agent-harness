"""Standalone transport qualification against real local HTTP/SSE sockets."""
import asyncio
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit

import pytest

from model_gateway.errors import GatewayError
from model_gateway.network import NetworkPolicy
from model_gateway.translation import StreamTranslator, token_usage, translate_request
from model_gateway.transports import GatewayTransport, discover_models, native_client_headers
from model_gateway.wire import json_dumps, json_loads


def sse(*events):
    return b"".join((b"data: " + (event if isinstance(event, bytes) else json_dumps(event).encode()) + b"\r\n\r\n") for event in events)


@asynccontextmanager
async def wire(replies, *, timeout=3, max_bytes=8388608, delay_headers=None, delay_body=None):
    requests, tasks = [], set()

    async def handle(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        try:
            raw = await reader.readuntil(b"\r\n\r\n")
            first, *lines = raw.decode().split("\r\n")
            headers = dict(line.lower().split(": ", 1) for line in lines if ": " in line)
            body = await reader.readexactly(int(headers.get("content-length", 0)))
            request = {"method": first.split()[0], "path": first.split()[1], "headers": headers,
                       "body": json_loads(body) if body else None}
            requests.append(request)
            response = replies(request, len(requests)) if callable(replies) else replies[min(len(requests) - 1, len(replies) - 1)]
            status, content = response if isinstance(response, tuple) else (200, response)
            if not isinstance(content, bytes):
                content = json_dumps(content).encode()
            if delay_headers:
                await delay_headers.wait()
            writer.write(f"HTTP/1.1 {status} Test\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n".encode())
            await writer.drain()
            writer.write(content)
            await writer.drain()
            if delay_body:
                await delay_body.wait()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            tasks.discard(task)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    endpoint = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    try:
        yield endpoint, NetworkPolicy(private_origins=[endpoint], timeout=timeout, max_bytes=max_bytes), requests
    finally:
        server.close()
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await server.wait_closed()


def payload(**extra):
    return {"model": "test-model", "messages": [{"role": "user", "content": "Hello"}], "max_tokens": 128, **extra}


async def collect(protocol, endpoint, policy, body=None, **kwargs):
    return [event async for event in GatewayTransport(policy).stream(protocol, endpoint, body or payload(), **kwargs)]


def oai(delta=None, finish=None, **extra):
    return {"choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}], **extra}


def native_text(protocol, reason=None):
    if protocol == "anthropic":
        return sse({"type": "message_start", "message": {"usage": {"input_tokens": 5, "cache_read_input_tokens": 2}}},
                   {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": "Hi"}},
                   {"type": "ping"}, {"type": "future_event"},
                   {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": " there"}},
                   {"type": "content_block_stop", "index": 0},
                   {"type": "message_delta", "delta": {"stop_reason": reason or "end_turn"}, "usage": {"output_tokens": 3}},
                   {"type": "message_stop"})
    if protocol == "gemini":
        return sse({"candidates": [{"index": 0, "content": {"parts": [{"text": "private", "thought": True}, {"text": "Hi there"}]}, "finishReason": reason or "STOP"}],
                    "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3, "thoughtsTokenCount": 2, "totalTokenCount": 10}})
    if protocol == "openai-responses":
        end = {"type": "response.completed", "response": {"usage": {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}}}
        if reason:
            end = {"type": "response.incomplete", "response": {"incomplete_details": {"reason": reason}}}
        return sse({"type": "response.created"}, {"type": "response.in_progress"},
                   {"type": "response.output_text.delta", "delta": "Hi there"}, end)
    return sse(oai({"role": "assistant", "content": "Hi there", "reasoning_content": "private", "vendor": 1}, reason or "stop", vendor="extra"),
               {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10, "vendor_extra": "ignored"}}, b"[DONE]")


@pytest.mark.parametrize("protocol,path,auth", [("openai", "/v1/chat/completions", "authorization"),
    ("anthropic", "/v1/messages", "x-api-key"), ("gemini", "/v1/models/test-model:streamGenerateContent?alt=sse", "x-goog-api-key"),
    ("openai-responses", "/v1/responses", "authorization")])
async def test_real_native_text_usage_and_header_translation(protocol, path, auth):
    async with wire([native_text(protocol)]) as (url, policy, requests):
        events = await collect(protocol, url + "/v1", policy, credential="secret-key")
        assert "".join(c["delta"].get("content", "") for e in events for c in e["choices"]) == "Hi there"
        assert next(e["choices"][0]["finish_reason"] for e in events if e["choices"] and e["choices"][0]["finish_reason"]) == "stop"
        assert events[-1]["usage"]["total_tokens"] == 10
        assert requests[0]["path"] == path
        assert "secret-key" in requests[0]["headers"][auth]
        assert "secret-key" not in requests[0]["path"]
        assert "private" not in json_dumps(events)
        if protocol == "openai-responses":
            assert requests[0]["body"]["store"] is False


@pytest.mark.parametrize("protocol,reason,expected", [("anthropic", "max_tokens", "length"), ("anthropic", "refusal", "content_filter"),
    ("anthropic", "stop_sequence", "stop"), ("anthropic", "model_context_window_exceeded", "length"),
    ("gemini", "MAX_TOKENS", "length"), ("gemini", "SAFETY", "content_filter"),
    ("openai", "length", "length"), ("openai", "content_filter", "content_filter"),
    ("openai-responses", "max_output_tokens", "length"), ("openai-responses", "content_filter", "content_filter")])
async def test_refusal_and_truncation_are_not_success(protocol, reason, expected):
    async with wire([native_text(protocol, reason)]) as (url, policy, _):
        events = await collect(protocol, url, policy)
        assert expected in [c["finish_reason"] for e in events for c in e["choices"]]


def function_stream(protocol):
    arguments = '{"city":"Paris"}'
    if protocol == "openai":
        return sse(oai({"tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {"name": "weather", "arguments": arguments[:5]}}]}),
                   oai({"tool_calls": [{"index": 0, "function": {"arguments": arguments[5:]}}]}, "tool_calls"), b"[DONE]")
    if protocol == "anthropic":
        return sse({"type": "message_start", "message": {}},
                   {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "call_1", "name": "weather", "input": {}}},
                   {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": arguments}},
                   {"type": "content_block_stop", "index": 1},
                   {"type": "message_delta", "delta": {"stop_reason": "tool_use"}}, {"type": "message_stop"})
    if protocol == "gemini":
        return sse({"candidates": [{"content": {"parts": [{"functionCall": {"name": "weather", "args": {"city": "Paris"}}, "thoughtSignature": "opaque-proof"}]}, "finishReason": "STOP"}]})
    return sse({"type": "response.output_item.added", "item": {"id": "item1", "type": "function_call", "call_id": "call_1", "name": "weather", "arguments": ""}},
               {"type": "response.function_call_arguments.delta", "item_id": "item1", "delta": arguments},
               {"type": "response.completed", "response": {}})


@pytest.mark.parametrize("protocol", ["openai", "anthropic", "gemini", "openai-responses"])
async def test_general_function_call_roundtrip(protocol):
    tool = {"type": "function", "function": {"name": "weather", "description": "Current weather", "parameters": {"type": "object"}}}
    async with wire([function_stream(protocol), native_text(protocol)]) as (url, policy, requests):
        transport = GatewayTransport(policy)
        first = [e async for e in transport.stream(protocol, url, payload(tools=[tool]), credential=None)]
        call = {"id": None, "type": "function", "function": {"name": "", "arguments": ""}}
        for event in first:
            for choice in event["choices"]:
                for part in choice["delta"].get("tool_calls", []):
                    call["id"] = part.get("id", call["id"])
                    for key in ("name", "arguments"):
                        call["function"][key] += part["function"].get(key, "")
        assert call["function"] == {"name": "weather", "arguments": '{"city":"Paris"}'}
        body = payload(tools=[tool], messages=[{"role": "system", "content": "Be useful"}, {"role": "developer", "content": "Use evidence"},
            {"role": "user", "content": "Weather?"}, {"role": "assistant", "content": None, "tool_calls": [call]},
            {"role": "tool", "tool_call_id": call["id"], "content": "Sunny"}])
        assert [e async for e in transport.stream(protocol, url, body)]
        sent = requests[-1]["body"]
        assert "weather" in json_dumps(sent) and "Sunny" in json_dumps(sent)
        if protocol == "gemini":
            assert sent["contents"][1]["parts"][0]["thoughtSignature"] == "opaque-proof"
            assert "opaque-proof" not in json_dumps(first)
        assert "authorization" not in requests[-1]["headers"]


async def test_opencode_public_auth_mixed_catalog_and_responses():
    models = {"data": [{"id": "big-pickle"}, {"id": "paid-model"}, {"id": "deepseek-v4-flash-free"}, {"id": "muse-spark-1.3-contributor-free"}]}
    async with wire([models, native_text("openai-responses")]) as (url, policy, requests):
        result = await discover_models("openai", url, None, policy, preset_id="opencode")
        assert [(m["id"], m["protocol"]) for m in result] == [("big-pickle", "openai"), ("muse-spark-1.3-contributor-free", "openai-responses")]
        await collect("openai-responses", url, policy, preset_id="opencode")
        assert requests[0]["path"] == "/zen/v1/models"
        assert requests[1]["path"] == "/zen/v1/responses"
        assert requests[1]["headers"]["authorization"] == "bearer public"
        assert requests[1]["headers"]["x-opencode-request"].startswith("msg_")


@pytest.mark.parametrize("protocol", ["anthropic", "gemini"])
async def test_catalog_paginates_without_trusting_remote_urls(protocol):
    def replies(request, number):
        if protocol == "anthropic":
            return {"data": [{"id": "claude" + str(number), "display_name": "Claude", "max_input_tokens": 200000, "max_output_tokens": 64000}],
                    "has_more": number == 1, "last_id": "unsafe?after=secret", "next": "https://bad.invalid/leak"}
        return {"models": [{"name": "models/gemini" + str(number), "displayName": "Gemini", "inputTokenLimit": 1000000,
                    "outputTokenLimit": 8192, "supportedGenerationMethods": ["generateContent"]},
                    {"name": "models/embedding", "supportedGenerationMethods": ["embedContent"]}],
                    **({"nextPageToken": "unsafe?after=secret"} if number == 1 else {})}
    async with wire(replies) as (url, policy, requests):
        result = await discover_models(protocol, url + "/v1/models", "key", policy)
        assert len(result) == 2 and all(m["output_limit"] > 0 for m in result)
        assert urlsplit(requests[1]["path"]).path == "/v1/models"
        assert parse_qs(urlsplit(requests[1]["path"]).query)["pageToken" if protocol == "gemini" else "after_id"] == ["unsafe?after=secret"]


async def test_catalog_metadata_dedup_and_unknown_limits():
    catalog = {"data": [{"id": "gh/model", "name": "Model", "context_length": 120000, "top_provider": {"max_completion_tokens": 1000},
        "supported_parameters": ["tools"], "pricing": {"prompt": "0", "completion": "0"}}, {"id": "gh/model"},
        {"id": "unknown", "context_length": -5, "max_output_tokens": True, "supported_parameters": [], "pricing": {"prompt": "0", "completion": "1"}}]}
    async with wire([catalog]) as (url, policy, _):
        result = await discover_models("openai", url, None, policy, preset_id="9router")
        assert len(result) == 2
        assert result[0]["id"] == "gh/model" and result[0]["tools"] and result[0]["free"]
        assert result[1]["tools"] is False and result[1]["free"] is False and "context_limit" not in result[1]


@pytest.mark.parametrize("status,code", [(401, "provider_auth"), (403, "provider_auth"), (429, "provider_rate_limit"),
    (404, "provider_not_found"), (400, "provider_request"), (422, "provider_request"), (500, "provider_unavailable"), (302, "provider_unavailable")])
async def test_upstream_errors_do_not_expose_body_or_credentials(status, code):
    async with wire([(status, b"private-key hidden response contents")]) as (url, policy, requests):
        for action in (collect("openai", url, policy, credential="private-key"), discover_models("openai", url, "private-key", policy)):
            with pytest.raises(GatewayError) as caught:
                await action
            assert caught.value.code == code
            assert "private-key" not in str(caught.value) and "hidden" not in str(caught.value)
        assert len(requests) == 2


@pytest.mark.parametrize("headers", [True, False])
async def test_cancellation_interrupts_connection_or_body(headers):
    gate, cancel = asyncio.Event(), asyncio.Event()
    async with wire([b""], **{"delay_headers" if headers else "delay_body": gate}) as (url, policy, requests):
        task = asyncio.create_task(collect("openai", url, policy, cancel=cancel))
        for _ in range(100):
            if requests:
                break
            await asyncio.sleep(.005)
        cancel.set()
        with pytest.raises(GatewayError, match="cancelled"):
            await asyncio.wait_for(task, .3)


async def test_precancel_and_absolute_deadline_survive_first_chunk():
    cancel = asyncio.Event()
    cancel.set()
    async with wire([native_text("openai")]) as (url, policy, requests):
        with pytest.raises(GatewayError) as caught:
            await collect("openai", url, policy, cancel=cancel)
        assert caught.value.code == "cancelled" and not requests
    gate = asyncio.Event()
    async with wire([sse(oai({"content": "first"}))], timeout=.1, delay_body=gate) as (url, policy, _):
        seen = []
        with pytest.raises(GatewayError) as caught:
            async for event in GatewayTransport(policy).stream("openai", url, payload()):
                seen.append(event)
        assert caught.value.code == "timeout"
        assert seen[0]["choices"][0]["delta"]["content"] == "first"


@pytest.mark.parametrize("native", [False, True])
async def test_per_request_deadline_survives_output_and_releases_network_slot(native):
    first = {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": "first"}}
    gate = asyncio.Event()
    async with wire([sse(first) if native else sse(oai({"content": "first"}))], timeout=3, delay_body=gate) as (url, policy, requests):
        transport = GatewayTransport(policy)
        stream = transport.native_stream("anthropic", url, {"model": "m"}, request_timeout=.3) if native else transport.stream("openai", url, payload(), request_timeout=.3)
        available = policy._slots._value
        seen = []
        with pytest.raises(GatewayError) as caught:
            async for event in stream:
                seen.append(event)
        assert caught.value.code == "timeout" and caught.value.retryable
        assert caught.value.status == 504 and seen and len(requests) == 1
        assert policy.timeout == 3 and policy._slots._value == available


async def test_request_deadline_closes_upstream_during_downstream_backpressure():
    gate = asyncio.Event()
    async with wire([sse(*(oai({"content": str(index)}) for index in range(4)))], timeout=3, delay_body=gate) as (url, policy, _):
        stream = GatewayTransport(policy).stream("openai", url, payload(), request_timeout=.3)
        available = policy._slots._value
        try:
            assert (await anext(stream))["choices"][0]["delta"]["content"] == "0"
            # The client pauses while the bounded queue fills. The producer's
            # deadline must close HTTP without waiting for the next client read.
            async def released():
                while policy._slots._value != available:
                    await asyncio.sleep(.005)
            await asyncio.wait_for(released(), 1)
            with pytest.raises(GatewayError) as caught:
                assert [event async for event in stream]
            assert caught.value.code == "timeout"
        finally:
            await stream.aclose()


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("limit", [True, 0, -1, float("inf"), float("nan"), "1", 4, 10**1000])
async def test_request_deadline_cannot_expand_operator_authority(native, limit):
    async with wire([native_text("openai")], timeout=3) as (url, policy, requests):
        transport = GatewayTransport(policy)
        stream = transport.native_stream("openai", url, payload(), request_timeout=limit) if native else transport.stream("openai", url, payload(), request_timeout=limit)
        with pytest.raises(GatewayError) as caught:
            assert [event async for event in stream]
        assert caught.value.code == "invalid_timeout" and not requests


async def test_network_policy_remains_authority():
    async with wire([native_text("openai")]) as (url, _, requests):
        with pytest.raises(GatewayError) as caught:
            await collect("openai", url, NetworkPolicy())
        assert caught.value.code == "network_address_denied" and not requests


@pytest.mark.parametrize("body,code", [(b"data: {bad}\n\n", "invalid_json"), (sse([]), "provider_protocol"),
    (b"data: {}", "provider_incomplete"), (b"data: {}\n", "provider_incomplete"),
    (sse(oai({"content": "partial"})), "provider_incomplete"),
    (sse({"error": {"message": "private"}}), "provider_error"),
    (sse(oai({}, "unknown"), b"[DONE]"), "provider_protocol"),
    (sse(oai({"content": 4}), b"[DONE]"), "provider_protocol"),
    (sse(oai({"role": "system"}), b"[DONE]"), "provider_protocol"),
    (sse({"choices": [{"index": 1, "delta": {}}]}), "provider_protocol"),
    (sse({"choices": [{}, {}]}), "provider_protocol"),
    (sse(oai({}, "stop"), oai({"content": "late"}), b"[DONE]"), "provider_protocol")])
async def test_malformed_streams_are_rejected(body, code):
    async with wire([body]) as (url, policy, _):
        with pytest.raises(GatewayError) as caught:
            await collect("openai", url, policy)
        assert caught.value.code == code


@pytest.mark.parametrize("body", [b"data: " + b"x" * 1048577, b"data: " + b"x" * 1048577 + b"\n\n"])
async def test_event_bound_is_enforced(body):
    async with wire([body]) as (url, policy, _):
        with pytest.raises(GatewayError) as caught:
            await collect("openai", url, policy)
        assert caught.value.code == "provider_bounds"


@pytest.mark.parametrize("catalog", [{"data": {}}, {"data": [None]}, {"data": [{"id": ""}]}, {"data": [{"id": "a", "name": 1}]},
    {"data": [{}]}, [], {"data": [{"id": "a"}] * 2001}])
async def test_bad_catalog_rejected(catalog):
    async with wire([catalog]) as (url, policy, _):
        with pytest.raises(GatewayError) as caught:
            await discover_models("openai", url, None, policy)
        assert caught.value.code == "invalid_catalog"


@pytest.mark.parametrize("catalog,code", [({"data": [], "has_more": True}, "invalid_catalog"),
    ({"data": [], "has_more": True, "last_id": "repeat"}, "catalog_bounds"),
    ({"data": [], "has_more": True, "last_id": [1]}, "catalog_bounds")])
async def test_bad_pagination_rejected(catalog, code):
    async with wire([catalog]) as (url, policy, _):
        with pytest.raises(GatewayError) as caught:
            await discover_models("anthropic", url, None, policy)
        assert caught.value.code == code


@pytest.mark.parametrize("protocol", ["anthropic", "gemini", "openai-responses"])
def test_native_request_translation_preserves_configuration(protocol):
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,YQ=="}}
    request = payload(messages=[{"role": "system", "content": [{"type": "text", "text": "Rules"}]},
        {"role": "user", "content": [{"type": "text", "text": "Hi"}, image]}, {"role": "user", "content": "Again"},
        {"role": "assistant", "content": [{"type": "text", "text": "Hello"}]}],
        tools=[{"type": "function", "function": {"name": "echo", "parameters": {"type": "object"}}}],
        tool_choice={"type": "function", "function": {"name": "echo"}}, temperature=.2, top_p=.8, stop="END",
        response_format={"type": "json_schema", "json_schema": {"name": "answer", "schema": {"type": "object"}}}, reasoning_effort="low", parallel_tool_calls=False)
    before = json_dumps(request)
    result = translate_request(protocol, request, {})
    assert json_dumps(request) == before
    assert "echo" in json_dumps(result) and "Rules" in json_dumps(result) and "YQ==" in json_dumps(result)
    for choice in ("required", "none", "auto"):
        assert translate_request(protocol, {**request, "tool_choice": choice, "stop": ["END"], "response_format": {"type": "json_object"}}, {})


def test_openai_reasoning_and_custom_fields():
    for model in ("o1", "o3-mini", "o4-mini", "gpt-5.2", "gpt-6-astra"):
        result = translate_request("openai", payload(model=model, custom={"preserve": True}), {})
        assert result["max_completion_tokens"] == 128 and "max_tokens" not in result and result["custom"]["preserve"]
    result = translate_request("openai", {"model": "normal", "messages": [{"role": "user", "content": "Hi"}]}, {})
    assert result["max_tokens"] == 4096 and result["stream_options"]["include_usage"]


@pytest.mark.parametrize("body", [{}, payload(n=2), payload(model=""), payload(messages=[{"role": "invalid"}]), payload(max_tokens=True), payload(max_tokens=-1)])
def test_invalid_requests_fail_before_dispatch(body):
    with pytest.raises(GatewayError):
        translate_request("openai", body, {})


@pytest.mark.parametrize("content", [4, [{"type": "unsupported"}], [{"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}],
    [{"type": "image_url", "image_url": {"url": "data:image/png;base64,a="}}]])
def test_native_unsupported_content_is_not_silently_dropped(content):
    with pytest.raises(GatewayError):
        translate_request("gemini", payload(messages=[{"role": "user", "content": content}]), {})
    result = translate_request("anthropic", payload(messages=[{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}]}]), {})
    assert result["messages"][0]["content"][0]["source"]["type"] == "url"


def test_missing_tool_history_is_not_fabricated():
    with pytest.raises(GatewayError, match="matching"):
        translate_request("gemini", payload(messages=[{"role": "tool", "content": "result", "tool_call_id": "unknown"}]), {})


@pytest.mark.parametrize("value,protocol", [({"prompt_tokens": -1, "completion_tokens": 0}, "openai"),
    ({"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 5}, "openai"),
    ({"promptTokenCount": 9, "totalTokenCount": 1}, "gemini"), ({"input_tokens": True}, "anthropic"), ([], "openai")])
def test_invalid_factual_usage_rejected(value, protocol):
    with pytest.raises(GatewayError):
        token_usage(value, protocol)


def test_cumulative_vendor_usage_and_refusal():
    t = StreamTranslator("openai", "model", {})
    assert t.feed(oai({"refusal": "Cannot comply"}, usage={"total_tokens": 1}))
    t.feed(oai({}, "content_filter", usage={"total_tokens": 2}))
    assert t.end(True)[0]["usage"] == {"total_tokens": 2}
    t = StreamTranslator("openai", "model", {})
    t.feed({"usage": {"total_tokens": 2}})
    with pytest.raises(GatewayError):
        t.feed({"usage": {"total_tokens": 1}})


@pytest.mark.parametrize("index,function,identity", [(128, {}, "id"), (True, {}, "id"), (0, {"name": "x" * 257}, "id"),
    (0, {"name": 1}, "id"), (0, {"arguments": "x" * 1048577}, "id"), (0, {}, ""), (0, {}, "x" * 257)])
def test_tool_bounds(index, function, identity):
    with pytest.raises(GatewayError):
        StreamTranslator("openai", "model", {}).tool(index, function, identity)


def test_tool_identity_arguments_and_fragment_count():
    for mutation in ("identity", "name", "duplicate", "json", "mismatch", "parts"):
        t = StreamTranslator("openai", "model", {})
        t.tool(0, {"name": "a" if mutation != "name" else "", "arguments": "[]" if mutation == "json" else "{}"}, "id")
        with pytest.raises(GatewayError):
            if mutation == "identity":
                t.tool(0, {}, "changed")
            elif mutation == "parts":
                for _ in range(4096):
                    t.tool(0, {})
            else:
                if mutation == "duplicate":
                    t.tool(1, {"name": "a", "arguments": "{}"}, "id")
                t.finish("stop" if mutation == "mismatch" else "tool_calls")


async def test_codex_trusted_headers_and_request_contract():
    async with wire([native_text("openai-responses")]) as (url, policy, requests):
        events = [e async for e in GatewayTransport(policy).stream("openai-responses", url + "/backend-api/codex/responses",
            payload(temperature=.2), "access-token", preset_id="codex", extra_headers={"ChatGPT-Account-ID": "account123"})]
        assert events[-1]["usage"]["total_tokens"] == 10
        request = requests[0]
        assert request["path"] == "/backend-api/codex/responses"
        assert request["headers"]["chatgpt-account-id"] == "account123"
        assert request["body"]["store"] is False and "max_output_tokens" not in request["body"]


@pytest.mark.parametrize("protocol,event", [("anthropic", {"type": "message_stop"}),
    ("gemini", {"candidates": [{}, {}]}), ("gemini", {"candidates": [{"index": 2}]}),
    ("openai-responses", {"type": "response.incomplete", "response": {"incomplete_details": {"reason": "unknown"}}}),
    ("openai-responses", {"type": "response.function_call_arguments.delta", "item_id": "missing", "delta": "{}"})])
async def test_native_protocol_failure_is_sanitized(protocol, event):
    async with wire([sse(event)]) as (url, policy, _):
        with pytest.raises(GatewayError) as caught:
            await collect(protocol, url, policy)
        assert caught.value.code == "provider_protocol"


def test_native_block_order_refusal_and_signature_bounds():
    t = StreamTranslator("anthropic", "m", {})
    start = {"type": "message_start", "message": {}}
    t.feed(start)
    with pytest.raises(GatewayError):
        t.feed(start)
    block = {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}}
    t.feed(block)
    with pytest.raises(GatewayError):
        t.feed(block)
    with pytest.raises(GatewayError):
        t.feed({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "private"}})
    t.feed({"type": "content_block_stop", "index": 0})
    with pytest.raises(GatewayError):
        t.feed({"type": "content_block_stop", "index": 0})
    with pytest.raises(GatewayError):
        t.feed({"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta"}})
    t = StreamTranslator("gemini", "m", {})
    assert t.feed({"promptFeedback": {"blockReason": "SAFETY"}})[0]["choices"][0]["finish_reason"] == "content_filter"
    cache = {str(i): "opaque" for i in range(512)}
    t = StreamTranslator("gemini", "m", cache)
    item = {"candidates": [{"content": {"parts": [{"functionCall": {"name": "a", "args": {}}, "thoughtSignature": "opaque"}]}}]}
    t.feed(item)
    assert len(cache) == 512 and "0" not in cache
    item["candidates"][0]["content"]["parts"][0]["thoughtSignature"] = "s" * 65537
    with pytest.raises(GatewayError):
        t.feed(item)
    t = StreamTranslator("openai-responses", "m", {})
    refusal = t.feed({"type": "response.refusal.delta", "delta": "Cannot comply"})
    assert refusal[0]["choices"][0]["delta"]["refusal"] == "Cannot comply"
    t.feed({"type": "response.completed", "response": {"output": [{"content": [{"type": "refusal"}]}]}})
    with pytest.raises(GatewayError):
        t.feed({"type": "response.created"})


async def test_input_boundaries_and_sse_multiline():
    async with wire([b": comment\n\ndata: {\ndata: " + b'"choices":[{"delta":{"content":"hello"},"finish_reason":"stop"}]}' + b"\n\ndata: [DONE]\n\n"]) as (url, policy, requests):
        assert await collect("openai", url, policy)
        for protocol, endpoint, credential in (("bogus", url, None), ("openai", url + "?key=private", None), ("openai", url, "bad key")):
            with pytest.raises(GatewayError):
                await collect(protocol, endpoint, policy, credential=credential)
        assert len(requests) == 1
    with pytest.raises(GatewayError):
        translate_request("unknown", payload(), {})


def test_repeated_provider_tool_id_is_emitted_once():
    t = StreamTranslator("openai", "m", {})
    assert t.tool(0, {"name": "tool", "arguments": "{"}, "same")["choices"][0]["delta"]["tool_calls"][0]["id"] == "same"
    assert "id" not in t.tool(0, {"arguments": "}"}, "same")["choices"][0]["delta"]["tool_calls"][0]


@pytest.mark.parametrize("protocol", ["openai", "anthropic", "gemini", "openai-responses"])
async def test_direct_native_stream_preserves_body_and_events(protocol):
    body = {"model": "model", "input": [{"type": "reasoning", "encrypted_content": "opaque-past"}],
            "messages": [{"role": "assistant", "content": [{"type": "thinking", "thinking": "prior", "signature": "signed"}]}],
            "thinking": {"type": "adaptive"}, "max_tokens": 123, "max_output_tokens": 123,
            "system": [{"type": "text", "text": "instructions", "cache_control": {"type": "ephemeral"}}]}
    if protocol == "anthropic":
        events = [{"type": "message_start", "message": {"usage": {"input_tokens": 2}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "provider-native"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "opaque-signed-proof"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}}, {"type": "message_stop"}]
        response = sse(*events)
    elif protocol == "openai-responses":
        events = [{"type": "response.created"}, {"type": "response.output_item.added", "item": {"type": "reasoning", "id": "rs_new", "encrypted_content": "opaque-new"}},
                  {"type": "response.completed", "response": {"output": [{"type": "reasoning", "encrypted_content": "opaque-new"}]}}]
        response = sse(*events)
    else:
        events, response = None, native_text(protocol)
    async with wire([response]) as (url, policy, requests):
        output = [e async for e in GatewayTransport(policy).native_stream(protocol, url, body, "secret", extra_headers=
            {"anthropic-beta": "prompt-caching-test", "anthropic-version": "2023-06-01"} if protocol == "anthropic" else {})]
        if events:
            assert [e["data"] for e in output] == events
        transmitted = requests[0]["body"]
        assert transmitted["system"] == body["system"] and transmitted["input"] == body["input"]
        assert transmitted["messages"] == body["messages"]
        if protocol == "anthropic":
            assert requests[0]["headers"]["anthropic-beta"] == "prompt-caching-test"
        if protocol == "openai-responses":
            assert transmitted["store"] is False
        if protocol == "gemini":
            assert "model" not in transmitted


def test_native_negotiation_headers_do_not_forward_client_authority():
    assert native_client_headers({"X-Api-Key": "gateway-key", "Authorization": "Bearer gateway-key", "Host": "evil",
        "Anthropic-Version": "2023-06-01", "Anthropic-Beta": "feature"}, "anthropic") == {
        "anthropic-version": "2023-06-01", "anthropic-beta": "feature"}
    with pytest.raises(GatewayError):
        native_client_headers({"Anthropic-Beta": "evil\nheader"}, "anthropic")


@pytest.mark.parametrize("reply,body,code", [((401, b"secret"), {"model": "m"}, "provider_auth"),
    (sse({"type": "response.function_call_arguments.delta", "item_id": "unknown", "delta": "x"}), {"model": "m"}, "provider_protocol"),
    (sse({"type": "injected\n\nevent"}), {"model": "m"}, "provider_protocol"),
    (native_text("openai-responses"), {}, "invalid_request")])
async def test_native_error_and_injection_bounds(reply, body, code):
    async with wire([reply]) as (url, policy, _):
        with pytest.raises(GatewayError) as caught:
            assert [e async for e in GatewayTransport(policy).native_stream("openai-responses", url, body)]
        assert caught.value.code == code


async def test_native_cancel_and_codex_request_specialization():
    async with wire([native_text("openai-responses")]) as (url, policy, requests):
        assert [e async for e in GatewayTransport(policy).native_stream("openai-responses", url, {"model": "m", "max_output_tokens": 100}, preset_id="codex")]
        assert requests[0]["body"]["store"] is False and "max_output_tokens" not in requests[0]["body"]
    gate, cancel = asyncio.Event(), asyncio.Event()
    async with wire([b""], delay_headers=gate) as (url, policy, requests):
        async def consume():
            return [e async for e in GatewayTransport(policy).native_stream("anthropic", url, {"model": "m"}, cancel=cancel)]
        task = asyncio.create_task(consume())
        for _ in range(100):
            if requests:
                break
            await asyncio.sleep(.005)
        cancel.set()
        with pytest.raises(GatewayError, match="cancelled"):
            await asyncio.wait_for(task, .3)


def test_empty_native_function_object_has_valid_arguments():
    t = StreamTranslator("anthropic", "m", {})
    t.feed({"type": "message_start", "message": {}})
    t.feed({"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "id", "name": "empty", "input": {}}})
    close = t.feed({"type": "content_block_stop", "index": 0})
    assert close[0]["choices"][0]["delta"]["tool_calls"][0]["function"]["arguments"] == "{}"
    t.feed({"type": "message_delta", "delta": {"stop_reason": "tool_use"}})
    t.feed({"type": "message_stop"})
    t.end()


def test_gemini_native_tool_identity_and_signature_replay_are_opaque():
    signatures = {}
    t = StreamTranslator("gemini", "m", signatures)
    event = t.feed({"candidates": [{"content": {"parts": [{"functionCall": {"id": "provider-call", "name": "weather", "args": {}},
        "thoughtSignature": "signed-content"}]}, "finishReason": "STOP"}]})[0]
    call = event["choices"][0]["delta"]["tool_calls"][0]
    identity = call["id"]
    assert identity != "provider-call" and "signed-content" not in json_dumps(event)
    request = translate_request("gemini", payload(messages=[{"role": "assistant", "tool_calls": [call]},
        {"role": "tool", "tool_call_id": identity, "content": "Sunny"}]), signatures)
    assert request["contents"][0]["parts"][0]["functionCall"]["id"] == "provider-call"
    assert request["contents"][0]["parts"][0]["thoughtSignature"] == "signed-content"
    assert request["contents"][1]["parts"][0]["functionResponse"]["id"] == "provider-call"
    with pytest.raises(GatewayError):
        StreamTranslator("gemini", "m", {}).feed({"candidates": [{"content": {"parts": [{"functionCall": {"id": [], "name": "x"}}]}}]})


async def test_responses_done_only_vendor_content_and_functions_are_not_lost():
    text_item = {"type": "message", "id": "m", "content": [{"type": "output_text", "text": "Full answer"}]}
    tool_item = {"type": "function_call", "id": "f", "call_id": "c", "name": "weather", "arguments": '{"city":"Paris"}'}
    events = [{"type": "response.output_text.delta", "item_id": "m", "content_index": 0, "delta": "Full "},
        {"type": "response.output_text.done", "item_id": "m", "content_index": 0, "text": "Full answer"},
        {"type": "response.output_item.done", "item": text_item},
        {"type": "response.output_item.added", "item": {**tool_item, "arguments": ""}},
        {"type": "response.output_item.done", "item": tool_item},
        {"type": "response.completed", "response": {"output": [text_item, tool_item]}}]
    async with wire([sse(*events)]) as (url, policy, _):
        result = await collect("openai-responses", url, policy)
        assert "".join(c["delta"].get("content", "") for e in result for c in e["choices"]) == "Full answer"
        fragments = [part for e in result for c in e["choices"] for part in c["delta"].get("tool_calls", [])]
        assert "".join(p["function"].get("arguments", "") for p in fragments) == tool_item["arguments"]
    t = StreamTranslator("openai-responses", "m", {})
    result = t.feed({"type": "response.completed", "response": {"output": [text_item, tool_item]}})
    assert any(e["choices"][0]["delta"].get("content") == "Full answer" for e in result)


def test_responses_completed_content_cannot_rewrite_observed_output():
    t = StreamTranslator("openai-responses", "m", {})
    t.feed({"type": "response.output_text.delta", "delta": "original"})
    with pytest.raises(GatewayError):
        t.feed({"type": "response.output_text.done", "text": "changed"})
    item = {"type": "function_call", "id": "f", "call_id": "c", "name": "a", "arguments": "{"}
    t.feed({"type": "response.output_item.added", "item": item})
    with pytest.raises(GatewayError):
        t.feed({"type": "response.output_item.done", "item": {**item, "name": "changed"}})
    with pytest.raises(GatewayError):
        t.feed({"type": "response.output_item.added", "item": item})
    refusal = StreamTranslator("openai-responses", "m", {}).feed({"type": "response.refusal.done", "refusal": "No"})
    assert refusal[0]["choices"][0]["delta"]["refusal"] == "No"
