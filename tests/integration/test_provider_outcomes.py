"""Provider outcomes and adversarial wire shapes over actual local HTTP streams."""
import asyncio
from contextlib import asynccontextmanager

import pytest

from agent.protocol import CoreError, ToolCall, canonical_json
from agent.provider_adapters import HTTPProvider
from test_continuity import WireModel, proposal


@asynccontextmanager
async def wire(script):
    model = WireModel([script])
    server = await asyncio.start_server(model.handle, "127.0.0.1", 0)
    endpoint = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/model"
    descriptor = {"endpoint": endpoint, "model": "outcome-fixture", "authentication": "none",
                  "tools": True, "output_limit": 1000}
    try:
        yield descriptor, model
    finally:
        server.close()
        await server.wait_closed()
        for task in list(model.tasks):
            task.cancel()
        await asyncio.gather(*model.tasks, return_exceptions=True)


def finish(reason):
    return {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}


def delta(value, **choice):
    return {"choices": [{"index": 0, "delta": value, **choice}]}


async def collect(script, **limits):
    async with wire(script) as (descriptor, model):
        events = [event async for event in HTTPProvider(**limits).stream(
            descriptor, [{"role": "user", "content": "answer"}])]
        assert model.requests[0]["stream"] is True
        return events


@pytest.mark.parametrize("raw,status,reason", [
    ("stop", "completed", "end_turn"),
    ("length", "incomplete", "max_tokens"),
    ("content_filter", "refused", "refusal"),
    (None, "completed", "unspecified"),
])
async def test_terminal_cause_preserves_visible_text(raw, status, reason):
    events = await collect(["partial ", "answer", finish(raw)])
    assert "".join(event.data["text"] for event in events if event.kind == "text") == "partial answer"
    assert events[-1].kind == "terminal"
    assert events[-1].data == {"status": status, "stop_reason": reason, "provider_finish_reason": raw}


@pytest.mark.parametrize("reason", ["length", "content_filter"])
@pytest.mark.parametrize("arguments", ["{\"tool\":", canonical_json(ToolCall("file.write", {"path": "x"}).as_dict())])
async def test_truncated_or_filtered_calls_are_never_proposals(reason, arguments):
    call = delta({"tool_calls": [{"index": 0, "id": "call-1", "type": "function",
        "function": {"name": "invoke", "arguments": arguments}}]})
    events = await collect(["still visible", call, finish(reason)])
    assert [event.kind for event in events] == ["text", "terminal"]
    assert events[-1].data["status"] in {"incomplete", "refused"}


async def test_refusal_text_overrides_stop_and_withholds_existing_tool_proposal():
    events = await collect([proposal("file.write", {"path": "x"}),
                            delta({"refusal": "I cannot provide that."}), finish("stop")])
    assert [event.kind for event in events] == ["text", "terminal"]
    assert events[0].data["text"] == "I cannot provide that."
    assert events[-1].data == {"status": "refused", "stop_reason": "refusal", "provider_finish_reason": "stop"}


async def test_valid_fragmented_call_and_final_usage_details():
    arguments = canonical_json(ToolCall("file.read", {"path": "x"}).as_dict())
    events = await collect([
        delta({"role": "assistant", "content": None, "refusal": None}),
        delta({"tool_calls": [{"index": 0, "id": "call-1", "type": "function", "function": {"name": "inv", "arguments": arguments[:12]}}]}),
        delta({"tool_calls": [{"index": 0, "id": "call-1", "function": {"name": "oke", "arguments": arguments[12:]}}]}),
        finish("tool_calls"),
        {"choices": [], "usage": {"total_tokens": 9, "prompt_tokens": 5, "completion_tokens": 4,
                                    "completion_tokens_details": {"reasoning_tokens": 1}, "prompt_tokens_details": None}},
    ])
    assert [event.kind for event in events] == ["usage", "tool", "terminal"]
    assert events[1].data["call"]["tool"] == "file.read"
    assert events[-1].data == {"status": "completed", "stop_reason": "tool_calls", "provider_finish_reason": "tool_calls"}
    compatibility = await collect([proposal("file.read", {"path": "x"})])
    assert compatibility[-1].data["stop_reason"] == "unspecified"
    assert compatibility[0].kind == "tool"


@pytest.mark.parametrize("event", [
    {}, {"choices": None}, {"choices": {}}, {"choices": [{"delta": {}}, {"delta": {}}]},
    {"choices": [None]}, {"choices": [{"index": 1, "delta": {}}]},
    {"choices": [{"index": True, "delta": {}}]}, {"choices": [{}]},
    delta(None), delta([]), delta({"role": "user"}), delta({"content": {}}),
    delta({"refusal": []}), delta({"function_call": {}}), delta({"tool_calls": {}}),
    delta({"tool_calls": [None]}), delta({"tool_calls": [{"index": True}]}),
    delta({"tool_calls": [{"id": {}}]}), delta({"tool_calls": [{"id": "x" * 257}]}),
    delta({"tool_calls": [{"type": "custom"}]}), delta({"tool_calls": [{"function": []}]}),
    delta({"tool_calls": [{"function": {"name": 4}}]}),
    delta({"tool_calls": [{"function": {"arguments": {}}}]}),
    finish("invented_terminal"), finish([]),
    {"choices": [], "usage": []}, {"choices": [], "usage": {}},
    {"choices": [], "usage": {"total_tokens": True}},
    {"choices": [], "usage": {"total_tokens": -1}},
    {"choices": [], "usage": {"total_tokens": 1.5}},
    {"choices": [], "usage": {"total_tokens": 3, "prompt_tokens": 2, "completion_tokens": 2}},
    {"choices": [], "usage": {"total_tokens": 3, "completion_tokens_details": []}},
    {"choices": [], "usage": {"total_tokens": 3, "completion_tokens_details": {"reasoning_tokens": -1}}},
])
async def test_malformed_wire_shapes_fail_before_any_tool_proposal(event):
    output = []
    async with wire(["visible before error", proposal("file.read", {"path": "x"}), event]) as (descriptor, _):
        with pytest.raises(CoreError) as caught:
            async for item in HTTPProvider().stream(descriptor, [{"role": "user", "content": "x"}]):
                output.append(item)
    assert caught.value.code in {"provider_protocol", "provider_bounds"}
    assert [item.kind for item in output] == ["text"]
    assert output[0].data["text"] == "visible before error"


@pytest.mark.parametrize("script,limits", [
    ([delta({"tool_calls": [{"index": 0, "function": {"name": "x" * 100}}]})] * 3, {}),
    ([proposal("file.read", {"path": "x"})] * 2, {"max_tool_bytes": 300}),
    ([delta({"tool_calls": [{"index": 0}]})] * 3, {"max_tool_parts": 2}),
    ([delta({"tool_calls": [{"index": 2}]})], {"max_tool_calls": 2}),
])
async def test_tool_fragments_have_aggregate_byte_part_name_and_call_bounds(script, limits):
    with pytest.raises(CoreError) as caught:
        await collect(script, **limits)
    assert caught.value.code == "provider_bounds"


@pytest.mark.parametrize("script", [
    [proposal("file.read", {}), delta({"tool_calls": [{"index": 0, "id": "substituted"}]})],
    [proposal("file.read", {}), delta({"tool_calls": [{"index": 1, "id": "call-1", "function": {"name": "invoke", "arguments": "{}"}}]})],
    [finish("stop"), "late output"], [finish("stop"), finish("length")],
    [finish("tool_calls")], [proposal("file.read", {}), finish("stop")],
    [{"choices": [], "usage": {"total_tokens": 1}}, {"choices": [], "usage": {"total_tokens": 2}}],
])
async def test_conflicting_terminal_identity_and_usage_are_rejected(script):
    with pytest.raises(CoreError) as caught:
        await collect(script)
    assert caught.value.code == "provider_protocol"


async def test_every_tool_is_validated_before_any_tool_event_is_admitted():
    second = delta({"tool_calls": [{"index": 1, "id": "call-2", "function": {"name": "invoke", "arguments": "{"}}]})
    output = []
    async with wire([proposal("file.read", {}), second, finish("tool_calls")]) as (descriptor, _):
        with pytest.raises(CoreError) as caught:
            async for item in HTTPProvider().stream(descriptor, [{"role": "user", "content": "x"}]):
                output.append(item)
    assert caught.value.code == "invalid_json" and output == []


async def test_identical_final_usage_replay_is_not_double_counted():
    usage = {"choices": [], "usage": {"total_tokens": 3}}
    events = await collect(["text", finish("stop"), usage, usage])
    assert [event.kind for event in events] == ["text", "usage", "terminal"]


@pytest.mark.parametrize("limits", [
    {"timeout": float("nan")}, {"timeout": float("inf")}, {"max_tool_bytes": 0},
    {"max_tool_parts": True}, {"max_tool_calls": 129}, {"max_event_bytes": 1.5},
])
def test_invalid_provider_resource_limits(limits):
    with pytest.raises(CoreError) as caught:
        HTTPProvider(**limits)
    assert caught.value.code == "invalid_limits"
