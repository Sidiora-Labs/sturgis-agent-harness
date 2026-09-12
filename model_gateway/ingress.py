"""Native Messages and Responses client envelopes over the shared chat router.

Only caller-supplied conversation history is translated. Stored response IDs,
server tools and asynchronous/background work require separate gateway services
and are rejected explicitly rather than being silently ignored.
"""
from __future__ import annotations

from uuid import uuid4

from .errors import GatewayError
from .wire import json_dumps, json_loads


def _invalid(message="The native request contains unsupported conversation content."):
    raise GatewayError("unsupported_request", message)


def _text_parts(content, native):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        _invalid()
    parts = []
    for part in content:
        if not isinstance(part, dict):
            _invalid()
        kind = part.get("type")
        if kind in {"text", "input_text", "output_text"}:
            parts.append({"type": "text", "text": part["text"]})
        elif kind == "input_image":
            if "image_url" not in part:
                _invalid("Provide image content directly; hosted file IDs are not available on this gateway.")
            parts.append({"type": "image_url", "image_url": {"url": part["image_url"]}})
        elif native == "anthropic" and kind == "image":
            source = part["source"]
            if source.get("type") == "base64":
                url = "data:" + source["media_type"] + ";base64," + source["data"]
            elif source.get("type") == "url":
                url = source["url"]
            else:
                _invalid()
            parts.append({"type": "image_url", "image_url": {"url": url}})
        elif kind == "refusal":
            parts.append({"type": "text", "text": part["refusal"]})
        else:
            _invalid()
    return parts


def to_chat(protocol, payload):
    """Convert a native client request to the gateway's public chat contract."""
    try:
        if protocol not in {"anthropic", "openai-responses"} or not isinstance(payload, dict):
            _invalid("Choose Messages or Responses as the client protocol.")
        body = json_loads(json_dumps(payload))
        if any(body.get(key) for key in ("previous_response_id", "conversation", "background")):
            _invalid("Send the complete conversation history. Stored response references and background requests are not supported.")
        if body.get("store") is True:
            _invalid("This gateway does not store provider responses; set store to false.")
        if body.get("thinking", {}).get("type") in {"enabled", "adaptive"}:
            _invalid("Explicit native thinking replay is not supported by this translated route. Use ordinary conversation mode.")
        result = {"model": body["model"], "messages": [], "stream": body.get("stream", False)}
        for key in ("temperature", "top_p", "parallel_tool_calls"):
            if key in body:
                result[key] = body[key]
        limit = "max_tokens" if protocol == "anthropic" else "max_output_tokens"
        if limit in body:
            result["max_tokens"] = body[limit]
        tools = body.get("tools", [])
        if protocol == "anthropic":
            if body.get("system"):
                result["messages"].append({"role": "system", "content": _text_parts(body["system"], protocol)})
            for message in body["messages"]:
                content = message.get("content", "")
                if not isinstance(content, list):
                    result["messages"].append({"role": message["role"], "content": content})
                    continue
                regular, calls, outputs = [], [], []
                for block in content:
                    if block.get("type") == "tool_use":
                        calls.append({"id": block["id"], "type": "function", "function": {"name": block["name"], "arguments": json_dumps(block["input"])}})
                    elif block.get("type") == "tool_result":
                        output = block.get("content", "")
                        if not isinstance(output, str):
                            output = json_dumps(output)
                        if block.get("is_error"):
                            output = json_dumps({"error": output})
                        outputs.append({"role": "tool", "tool_call_id": block["tool_use_id"], "content": output})
                    else:
                        regular.append(block)
                if outputs:
                    if message["role"] != "user" or calls:
                        _invalid("Tool results belong to a user message following the assistant's tool calls.")
                    result["messages"].extend(outputs)
                if regular or calls:
                    converted = {"role": message["role"], "content": _text_parts(regular, protocol)}
                    if calls:
                        if message["role"] != "assistant":
                            _invalid("Tool calls belong to assistant messages.")
                        converted["tool_calls"] = calls
                    result["messages"].append(converted)
            for tool in tools:
                if tool.get("type", "custom") not in {"custom", "function"} or "name" not in tool:
                    _invalid("This gateway accepts client-executed function tools; provider-hosted tools are not supported here.")
            if tools:
                result["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                   "parameters": t.get("input_schema", {"type": "object"})}} for t in tools]
            choice = body.get("tool_choice")
            if choice:
                if choice.get("type") == "tool":
                    result["tool_choice"] = {"type": "function", "function": {"name": choice["name"]}}
                elif choice.get("type") in {"auto", "any", "none"}:
                    result["tool_choice"] = {"any": "required"}.get(choice["type"], choice["type"])
                else:
                    _invalid("Choose auto, any, none, or a named function tool.")
                if "disable_parallel_tool_use" in choice:
                    result["parallel_tool_calls"] = not choice["disable_parallel_tool_use"]
            if "stop_sequences" in body:
                result["stop"] = body["stop_sequences"]
        else:
            if body.get("instructions"):
                result["messages"].append({"role": "system", "content": body["instructions"]})
            inputs = body["input"]
            if isinstance(inputs, str):
                inputs = [{"role": "user", "content": inputs}]
            if not isinstance(inputs, list):
                _invalid()
            for item in inputs:
                kind = item.get("type", "message")
                if kind == "function_call":
                    result["messages"].append({"role": "assistant", "content": None, "tool_calls": [{"type": "function", "id": item["call_id"],
                        "function": {"name": item["name"], "arguments": item["arguments"]}}]})
                elif kind == "function_call_output":
                    result["messages"].append({"role": "tool", "tool_call_id": item["call_id"], "content": item["output"] if isinstance(item["output"], str) else json_dumps(item["output"])})
                elif kind == "message":
                    result["messages"].append({"role": item["role"], "content": _text_parts(item["content"], protocol)})
                else:
                    _invalid("Send explicit messages and function results instead of provider-stored items.")
            for tool in tools:
                if tool.get("type") != "function":
                    _invalid("This gateway accepts client-executed function tools; provider-hosted tools are not supported here.")
            if tools:
                result["tools"] = [{"type": "function", "function": {k: v for k, v in t.items() if k != "type"}} for t in tools]
            choice = body.get("tool_choice")
            if choice:
                if isinstance(choice, str):
                    result["tool_choice"] = choice
                elif choice.get("type") == "function":
                    result["tool_choice"] = {"type": "function", "function": {"name": choice["name"]}}
                else:
                    _invalid("Choose auto, required, none, or a named function tool.")
            if "reasoning" in body and body["reasoning"].get("effort"):
                result["reasoning_effort"] = body["reasoning"]["effort"]
            response_format = body.get("text", {}).get("format")
            if response_format:
                result["response_format"] = {"type": "json_schema", "json_schema": {k: v for k, v in response_format.items() if k != "type"}} if response_format.get("type") == "json_schema" else response_format
        return result
    except GatewayError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError):
        _invalid("Check the fields in this native API request.")


def _native_usage(usage):
    if usage is None:
        return None
    result = {"input_tokens": usage.get("prompt_tokens", 0), "output_tokens": usage.get("completion_tokens", 0)}
    if "total_tokens" in usage:
        result["total_tokens"] = usage["total_tokens"]
    return result


def _response_output(message, identity):
    output = []
    content = []
    if message.get("content"):
        content.append({"type": "output_text", "text": message["content"], "annotations": [], "logprobs": []})
    if message.get("refusal"):
        content.append({"type": "refusal", "refusal": message["refusal"]})
    if content:
        output.append({"id": "msg_" + identity, "type": "message", "role": "assistant", "status": "completed", "content": content})
    for call in message.get("tool_calls", []):
        output.append({"id": "fc_" + call["id"], "type": "function_call", "status": "completed", "call_id": call["id"],
                       "name": call["function"]["name"], "arguments": call["function"]["arguments"]})
    return output


def from_completion(protocol, completion):
    """Render one nonstreaming native response from the common routed result."""
    choice, identity = completion["choices"][0], completion["id"]
    message, reason = choice["message"], choice["finish_reason"]
    usage = _native_usage(completion.get("usage"))
    if protocol == "anthropic":
        content = []
        if message.get("content"):
            content.append({"type": "text", "text": message["content"]})
        if message.get("refusal"):
            content.append({"type": "text", "text": message["refusal"]})
        for call in message.get("tool_calls", []):
            if reason == "tool_calls":
                content.append({"type": "tool_use", "id": call["id"], "name": call["function"]["name"], "input": json_loads(call["function"]["arguments"])})
        return {"id": "msg_" + identity, "type": "message", "role": "assistant", "model": completion["model"], "content": content,
                "stop_reason": {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens", "content_filter": "refusal"}[reason],
                "stop_sequence": None, "usage": {k: v for k, v in (usage or {}).items() if k != "total_tokens"}}
    if protocol != "openai-responses":
        _invalid("Choose Messages or Responses as the client protocol.")
    incomplete = reason in {"length", "content_filter"}
    return {"id": "resp_" + identity, "object": "response", "created_at": completion["created"], "model": completion["model"],
            "status": "incomplete" if incomplete else "completed", "error": None,
            "incomplete_details": {"reason": "max_output_tokens" if reason == "length" else "content_filter"} if incomplete else None,
            "output": _response_output(message, identity), "usage": usage, "parallel_tool_calls": True, "store": False}


async def from_chat(protocol, chunks):
    """Translate native events and close the routed stream on disconnect."""
    try:
        async for record in _from_chat(protocol, chunks):
            yield record
    finally:
        close = getattr(chunks, "aclose", None)
        if close:
            await close()


async def _from_chat(protocol, chunks):
    """Yield ``{event, data}`` records; HTTP owns framing and connection cleanup."""
    if protocol not in {"anthropic", "openai-responses"}:
        _invalid("Choose Messages or Responses as the client protocol.")
    identity, created, model, finish, usage = uuid4().hex, 0, "", None, None
    text, refusal, tools, blocks = [], [], {}, {}
    sequence, started = 0, False

    def event(kind, **fields):
        nonlocal sequence
        data = {"type": kind, **fields}
        if protocol == "openai-responses":
            data["sequence_number"] = sequence
            sequence += 1
        return {"event": kind, "data": data}

    async for chunk in chunks:
        if not started:
            started = True
            identity, created, model = chunk["id"], chunk.get("created", 0), chunk["model"]
            empty = {"id": identity, "created": created, "model": model,
                     "choices": [{"message": {"role": "assistant", "content": None}, "finish_reason": "stop"}]}
            native = from_completion(protocol, empty)
            if protocol == "anthropic":
                native.update(stop_reason=None, usage={"input_tokens": 0, "output_tokens": 0})
                yield event("message_start", message=native)
            else:
                native["status"] = "in_progress"
                yield event("response.created", response=native)
                yield event("response.in_progress", response=native)
        if chunk.get("usage") is not None:
            usage = chunk["usage"]
        for choice in chunk.get("choices", []):
            delta = choice.get("delta", {})
            finish = choice.get("finish_reason") or finish
            for field in ("content", "refusal"):
                value = delta.get(field)
                if not value:
                    continue
                (text if field == "content" else refusal).append(value)
                key = field
                if key not in blocks:
                    index = len(blocks)
                    blocks[key] = {"index": index, "type": field}
                    if protocol == "anthropic":
                        yield event("content_block_start", index=index, content_block={"type": "text", "text": ""})
                    else:
                        item = {"id": "msg_" + identity + "_" + field, "type": "message", "role": "assistant", "status": "in_progress", "content": []}
                        yield event("response.output_item.added", output_index=index, item=item)
                        part = {"type": "output_text", "text": "", "annotations": [], "logprobs": []} if field == "content" else {"type": "refusal", "refusal": ""}
                        yield event("response.content_part.added", output_index=index, item_id=item["id"], content_index=0, part=part)
                index = blocks[key]["index"]
                if protocol == "anthropic":
                    yield event("content_block_delta", index=index, delta={"type": "text_delta", "text": value})
                else:
                    yield event("response.output_text.delta" if field == "content" else "response.refusal.delta", output_index=index,
                                item_id="msg_" + identity + "_" + field, content_index=0, delta=value)
            for part in delta.get("tool_calls", []):
                key = part["index"]
                call = tools.setdefault(key, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                if part.get("id"):
                    call["id"] = part["id"]
                function = part.get("function", {})
                for field in ("name", "arguments"):
                    call["function"][field] += function.get(field, "")
                if key not in blocks and call["id"] and call["function"]["name"] and function.get("arguments"):
                    index = len(blocks)
                    blocks[key] = {"index": index, "type": "tool"}
                    if protocol == "anthropic":
                        yield event("content_block_start", index=index, content_block={"type": "tool_use", "id": call["id"], "name": call["function"]["name"], "input": {}})
                    else:
                        item = _response_output({"tool_calls": [call]}, identity)[0]
                        item.update(status="in_progress", arguments="")
                        yield event("response.output_item.added", output_index=index, item=item)
                    fragment = call["function"]["arguments"]
                else:
                    fragment = function.get("arguments", "")
                if key in blocks and fragment:
                    index = blocks[key]["index"]
                    if protocol == "anthropic":
                        yield event("content_block_delta", index=index, delta={"type": "input_json_delta", "partial_json": fragment})
                    else:
                        yield event("response.function_call_arguments.delta", output_index=index, item_id="fc_" + call["id"], delta=fragment)
    if not started or finish is None:
        raise GatewayError("provider_incomplete", "The provider disconnected before completing the response.", 502, retryable=True)
    message = {"role": "assistant", "content": "".join(text) or None}
    if refusal:
        message["refusal"] = "".join(refusal)
    if tools:
        message["tool_calls"] = list(tools.values())
    completion = {"id": identity, "created": created, "model": model, "usage": usage,
                  "choices": [{"message": message, "finish_reason": finish}]}
    native = from_completion(protocol, completion)
    completed_output = []
    for key, block in blocks.items():
        index = block["index"]
        if protocol == "anthropic":
            yield event("content_block_stop", index=index)
        elif block["type"] == "tool":
            call = tools[key]
            item = _response_output({"tool_calls": [call]}, identity)[0]
            if native["status"] == "incomplete":
                item["status"] = "incomplete"
            completed_output.append(item)
            yield event("response.function_call_arguments.done", output_index=index, item_id=item["id"], arguments=item["arguments"])
            yield event("response.output_item.done", output_index=index, item=item)
        else:
            field = block["type"]
            value = "".join(text if field == "content" else refusal)
            item_id = "msg_" + identity + "_" + field
            part = {"type": "output_text", "text": value, "annotations": [], "logprobs": []} if field == "content" else {"type": "refusal", "refusal": value}
            yield event("response.output_text.done" if field == "content" else "response.refusal.done", output_index=index, item_id=item_id,
                        content_index=0, **({"text": value} if field == "content" else {"refusal": value}))
            yield event("response.content_part.done", output_index=index, item_id=item_id, content_index=0, part=part)
            item = {"id": item_id, "type": "message", "role": "assistant", "status": "completed", "content": [part]}
            completed_output.append(item)
            yield event("response.output_item.done", output_index=index, item=item)
    if protocol == "anthropic":
        yield event("message_delta", delta={"stop_reason": native["stop_reason"], "stop_sequence": None}, usage=native["usage"])
        yield event("message_stop")
    else:
        native["output"] = completed_output
        yield event("response.incomplete" if native["status"] == "incomplete" else "response.completed", response=native)


async def collect_chat(protocol, chunks):
    """Collect the same native event stream into its complete response object."""
    response, content = None, {}
    async for record in from_chat(protocol, chunks):
        kind, data = record["event"], record["data"]
        if kind == "message_start":
            response = data["message"]
        elif kind == "content_block_start":
            content[data["index"]] = {**data["content_block"]}
            if data["content_block"]["type"] == "tool_use":
                content[data["index"]]["_arguments"] = ""
        elif kind == "content_block_delta":
            block = content[data["index"]]
            if data["delta"]["type"] == "text_delta":
                block["text"] += data["delta"]["text"]
            else:
                block["_arguments"] += data["delta"]["partial_json"]
        elif kind == "message_delta":
            response.update(data["delta"])
            response["usage"] = data["usage"]
        elif kind in {"response.completed", "response.incomplete"}:
            response = data["response"]
    if protocol == "anthropic":
        response["content"] = []
        for block in content.values():
            if block["type"] == "tool_use":
                if response["stop_reason"] != "tool_use":
                    continue
                block["input"] = json_loads(block.pop("_arguments"))
            response["content"].append(block)
    return response
