"""General chat wire translation; no agent tool or execution semantics live here."""
from __future__ import annotations

import base64
import re
import time
from uuid import uuid4

from .errors import GatewayError
from .wire import json_dumps, json_loads

PROTOCOLS = frozenset({"openai", "anthropic", "gemini", "openai-responses"})
MAX_TOOL_CALLS = 128
MAX_TOOL_PARTS = 4096
MAX_TOOL_BYTES = 1048576


def fail(message="The provider returned an invalid streaming response."):
    raise GatewayError("provider_protocol", message, status=502)


def object_value(value):
    if not isinstance(value, dict):
        fail()
    return value


def count(value):
    if type(value) is not int or not 0 <= value < 2 ** 63:
        fail("The provider returned invalid token usage.")
    return value


def text_value(value):
    if not isinstance(value, str):
        fail()
    return value


def token_usage(value, protocol):
    """Normalize factual usage, including native cache and thinking consumption."""
    value = object_value(value)
    if protocol == "anthropic":
        prompt = sum(count(value.get(key, 0)) for key in
                     ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        completion = count(value.get("output_tokens", 0))
    elif protocol == "gemini":
        prompt = count(value.get("promptTokenCount", 0))
        completion = count(value.get("candidatesTokenCount", 0)) + count(value.get("thoughtsTokenCount", 0))
        total = count(value.get("totalTokenCount", prompt + completion))
        # Providers may include other accounted modalities in their total.
        if total >= prompt + completion:
            completion = total - prompt
        else:
            fail("The provider returned inconsistent token usage.")
    else:
        pkey, ckey = ("input_tokens", "output_tokens") if protocol == "openai-responses" else ("prompt_tokens", "completion_tokens")
        if pkey not in value or ckey not in value:
            return {"total_tokens": count(value["total_tokens"])}
        prompt, completion = count(value[pkey]), count(value[ckey])
        if "total_tokens" in value and count(value["total_tokens"]) != prompt + completion:
            fail("The provider returned inconsistent token usage.")
    return {"prompt_tokens": count(prompt), "completion_tokens": count(completion),
            "total_tokens": count(prompt + completion)}


def _parts(content, protocol):
    if content is None:
        return []
    if isinstance(content, str):
        return ([{"type": "text", "text": content}] if protocol == "anthropic" else [{"text": content}]) if content else []
    if not isinstance(content, list):
        raise GatewayError("invalid_request", "Message content must be text or content parts.")
    result = []
    for part in content:
        part = object_value(part)
        if part.get("type") == "text":
            result.extend(_parts(text_value(part["text"]), protocol))
        elif part.get("type") == "image_url":
            url = text_value(object_value(part["image_url"])["url"])
            match = re.fullmatch(r"data:(image/[A-Za-z0-9.+-]+);base64,([A-Za-z0-9+/=\r\n]+)", url)
            if match:
                try:
                    base64.b64decode(match[2], validate=True)
                except ValueError:
                    raise GatewayError("invalid_request", "The inline image is not valid base64.") from None
                result.append({"type": "image", "source": {"type": "base64", "media_type": match[1], "data": match[2]}}
                              if protocol == "anthropic" else {"inlineData": {"mimeType": match[1], "data": match[2]}})
            elif protocol == "anthropic" and url.startswith(("https://", "http://")):
                result.append({"type": "image", "source": {"type": "url", "url": url}})
            else:
                raise GatewayError("unsupported_content", "This provider needs an inline base64 image for this content.")
        else:
            raise GatewayError("unsupported_content", "This provider does not support that message content type.")
    return result


def _arguments(value):
    parsed = json_loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, dict):
        raise GatewayError("invalid_request", "Function arguments must be a JSON object.")
    return parsed


def translate_request(protocol, payload, signatures):
    """Translate chat messages without changing their meaning or inventing history."""
    if protocol not in PROTOCOLS:
        raise GatewayError("unsupported_protocol", "Choose a supported provider protocol.")
    body = json_loads(json_dumps(payload))
    if (not isinstance(body, dict) or not isinstance(body.get("model"), str) or not body["model"]
            or not isinstance(body.get("messages"), list) or not body["messages"]
            or body.get("n", 1) != 1):
        raise GatewayError("invalid_request", "Provide a model, messages, and a single response choice.")
    for message in body["messages"]:
        if not isinstance(message, dict) or message.get("role") not in {"system", "developer", "user", "assistant", "tool"}:
            raise GatewayError("invalid_request", "Each message needs a supported conversation role.")
    limit = body.get("max_completion_tokens", body.get("max_tokens", 4096))
    if type(limit) is not int or not 0 < limit <= 2000000:
        raise GatewayError("invalid_request", "The output token limit must be a positive integer.")
    if protocol == "openai":
        body["stream"] = True
        body.setdefault("stream_options", {"include_usage": True})
        if re.match(r"^(?:o[134](?:-|$)|gpt-5(?:[.-]|$)|gpt-6(?:[.-]|$))", body["model"]):
            body["max_completion_tokens"] = limit
            body.pop("max_tokens", None)
        elif "max_tokens" not in body and "max_completion_tokens" not in body:
            body["max_tokens"] = limit
        return body
    systems, messages, calls = [], [], {}
    for message in body["messages"]:
        role = message["role"]
        if role in {"system", "developer"}:
            systems.extend(_parts(message.get("content"), "anthropic" if protocol == "openai-responses" else protocol))
            continue
        if protocol == "openai-responses":
            if role == "tool":
                messages.append({"type": "function_call_output", "call_id": message["tool_call_id"], "output": message.get("content", "")})
            else:
                content = message.get("content")
                if content:
                    if isinstance(content, list):
                        content = [{"type": "input_text" if role == "user" else "output_text", "text": p["text"]}
                                   if p.get("type") == "text" else {"type": "input_image", "image_url": p["image_url"]["url"]} for p in content]
                    messages.append({"role": role, "content": content})
                for call in message.get("tool_calls", []):
                    messages.append({"type": "function_call", "call_id": call["id"], "name": call["function"]["name"],
                                     "arguments": call["function"]["arguments"]})
            continue
        parts = []
        if role == "tool":
            identity = message["tool_call_id"]
            if identity not in calls:
                raise GatewayError("invalid_request", "A tool result must follow its matching function call.")
            if protocol == "anthropic":
                parts.append({"type": "tool_result", "tool_use_id": identity, "content": message.get("content", "")})
            else:
                content = message.get("content", "")
                parts.append({"functionResponse": {"name": calls[identity], "id": identity,
                              "response": content if isinstance(content, dict) else {"result": content}}})
        else:
            parts.extend(_parts(message.get("content"), protocol))
            for call in message.get("tool_calls", []):
                function = object_value(call["function"])
                calls[call["id"]] = function["name"]
                arguments = _arguments(function["arguments"])
                if protocol == "anthropic":
                    parts.append({"type": "tool_use", "id": call["id"], "name": function["name"], "input": arguments})
                else:
                    part = {"functionCall": {"id": call["id"], "name": function["name"], "args": arguments}}
                    signature = signatures.get(call["id"])
                    if signature:
                        part["thoughtSignature"] = signature
                    parts.append(part)
        native_role = ("assistant" if protocol == "anthropic" else "model") if role == "assistant" else "user"
        field = "content" if protocol == "anthropic" else "parts"
        if messages and messages[-1]["role"] == native_role:
            messages[-1][field].extend(parts)
        elif parts:
            messages.append({"role": native_role, field: parts})
    functions = [object_value(item["function"]) for item in body.get("tools", [])]
    choice = body.get("tool_choice")
    if protocol == "anthropic":
        result = {"model": body["model"], "messages": messages, "max_tokens": limit, "stream": True}
        if systems:
            result["system"] = systems
        if functions:
            result["tools"] = [{"name": f["name"], "description": f.get("description", ""), "input_schema": f.get("parameters", {"type": "object"})} for f in functions]
        if choice:
            result["tool_choice"] = {"type": {"required": "any"}.get(choice, choice)} if isinstance(choice, str) else {"type": "tool", "name": choice["function"]["name"]}
        for key in ("temperature", "top_p"):
            if key in body:
                result[key] = body[key]
        if "stop" in body:
            result["stop_sequences"] = [body["stop"]] if isinstance(body["stop"], str) else body["stop"]
    elif protocol == "gemini":
        config = {"maxOutputTokens": limit}
        for key, native in (("temperature", "temperature"), ("top_p", "topP")):
            if key in body:
                config[native] = body[key]
        if "stop" in body:
            config["stopSequences"] = [body["stop"]] if isinstance(body["stop"], str) else body["stop"]
        result = {"contents": messages, "generationConfig": config}
        if systems:
            result["systemInstruction"] = {"parts": systems}
        if functions:
            result["tools"] = [{"functionDeclarations": [{"name": f["name"], "description": f.get("description", ""),
                "parametersJsonSchema": f.get("parameters", {"type": "object"})} for f in functions]}]
        if choice:
            result["toolConfig"] = {"functionCallingConfig": {"mode": {"auto": "AUTO", "none": "NONE", "required": "ANY"}.get(choice, "AUTO")}
                if isinstance(choice, str) else {"mode": "ANY", "allowedFunctionNames": [choice["function"]["name"]]}}
        response = body.get("response_format", {})
        if response.get("type") in {"json_object", "json_schema"}:
            config["responseMimeType"] = "application/json"
            if response.get("type") == "json_schema":
                config["responseJsonSchema"] = response["json_schema"]["schema"]
    else:
        result = {"model": body["model"], "input": messages, "max_output_tokens": limit, "stream": True}
        if systems:
            result["instructions"] = "\n\n".join(p["text"] for p in systems)
        if functions:
            result["tools"] = [{"type": "function", **f} for f in functions]
        if choice:
            result["tool_choice"] = choice if isinstance(choice, str) else {"type": "function", "name": choice["function"]["name"]}
        for key in ("temperature", "top_p", "parallel_tool_calls"):
            if key in body:
                result[key] = body[key]
        if "reasoning_effort" in body:
            result["reasoning"] = {"effort": body["reasoning_effort"]}
        if "response_format" in body:
            response = body["response_format"]
            result["text"] = {"format": {"type": "json_schema", **response["json_schema"]} if response.get("type") == "json_schema" else response}
    return result


class StreamTranslator:
    """A request-local bounded stream state machine yielding compatible chunks."""

    def __init__(self, protocol, model, signatures):
        self.protocol, self.model, self.signatures = protocol, model, signatures
        self.identity, self.created = "chatcmpl-" + uuid4().hex, int(time.time())
        self.usage, self.native_usage, self.reason, self.done = None, {}, None, False
        self.tools, self.parts, self.tool_bytes = {}, 0, 0
        self.blocks, self.response_tools = {}, {}
        self.started = False

    def chunk(self, delta=None, reason=None, usage=None):
        result = {"id": self.identity, "object": "chat.completion.chunk", "created": self.created,
                  "model": self.model, "choices": [] if usage is not None else [{"index": 0, "delta": delta or {}, "finish_reason": reason}]}
        if usage is not None:
            result["usage"] = usage
        return result

    def tool(self, index, function, identity=None):
        if type(index) is not int or not 0 <= index < MAX_TOOL_CALLS:
            fail("The provider exceeded the function-call limit.")
        self.parts += 1
        self.tool_bytes += len(json_dumps(function).encode()) + len((identity or "").encode())
        if self.parts > MAX_TOOL_PARTS or self.tool_bytes > MAX_TOOL_BYTES:
            fail("The provider exceeded the function-fragment limit.")
        previous = self.tools.setdefault(index, {"id": None, "name": "", "arguments": ""})
        first_identity = previous["id"] is None
        if identity is not None:
            if (not isinstance(identity, str) or not identity or len(identity) > 256
                    or previous["id"] not in {None, identity}):
                fail("The provider changed a function-call identity.")
            previous["id"] = identity
        for key in ("name", "arguments"):
            previous[key] += text_value(function.get(key, ""))
        if len(previous["name"]) > 256:
            fail()
        part = {"index": index, "type": "function", "function": function}
        if identity and first_identity:
            part["id"] = identity
        return self.chunk({"tool_calls": [part]})

    def finish(self, reason):
        if self.reason is not None or reason not in {"stop", "tool_calls", "length", "content_filter"}:
            fail("The provider returned an unsupported completion reason.")
        if reason in {"stop", "tool_calls"}:
            if (reason == "tool_calls") != bool(self.tools):
                fail("The provider finish reason disagrees with its function calls.")
            identities = set()
            for item in self.tools.values():
                if not item["id"] or item["id"] in identities or not item["name"]:
                    fail("The provider returned incomplete function calls.")
                identities.add(item["id"])
                _arguments(item["arguments"])
        self.reason = reason
        return self.chunk(reason=reason)

    def feed(self, event):
        event = object_value(event)
        if "error" in event or event.get("type") in {"error", "response.failed"}:
            raise GatewayError("provider_error", "The provider could not complete this request. Try again or choose another model.", status=502, retryable=True)
        if self.done:
            fail("The provider continued after completing its response.")
        if self.protocol == "openai":
            return self._openai(event)
        if self.protocol == "anthropic":
            return self._anthropic(event)
        if self.protocol == "gemini":
            return self._gemini(event)
        return self._responses(event)

    def _openai(self, event):
        result = []
        if event.get("usage") is not None:
            usage = token_usage(event["usage"], "openai")
            # Several vendors send cumulative usage on every chunk; only expose final usage.
            if self.usage and usage["total_tokens"] < self.usage["total_tokens"]:
                fail("The provider token usage moved backwards.")
            self.usage = usage
        choices = event.get("choices", [])
        if not isinstance(choices, list) or len(choices) > 1:
            fail()
        for choice in choices:
            choice = object_value(choice)
            if choice.get("index", 0) != 0 or self.reason is not None:
                fail()
            delta = object_value(choice.get("delta", {}))
            out = {}
            if delta.get("role") is not None:
                if delta["role"] != "assistant":
                    fail()
                out["role"] = "assistant"
            for field in ("content", "refusal"):
                if delta.get(field) is not None:
                    out[field] = text_value(delta[field])
            if out:
                result.append(self.chunk(out))
            for call in delta.get("tool_calls") or []:
                if call.get("type", "function") != "function":
                    fail()
                result.append(self.tool(call.get("index", 0), object_value(call.get("function", {})), call.get("id")))
            if choice.get("finish_reason") is not None:
                result.append(self.finish(choice["finish_reason"]))
        return result

    def _anthropic(self, event):
        kind, result = event.get("type"), []
        if kind == "message_start":
            if self.started:
                fail()
            self.started = True
            self.native_usage.update(object_value(event["message"]).get("usage", {}))
            result.append(self.chunk({"role": "assistant"}))
        elif kind == "content_block_start":
            index, block = event["index"], object_value(event["content_block"])
            if type(index) is not int or not 0 <= index < 4096 or index in self.blocks or self.reason is not None:
                fail()
            self.blocks[index] = {"kind": block.get("type"), "closed": False}
            if block.get("type") == "tool_use":
                tool_index = len(self.tools)
                self.blocks[index]["tool"] = tool_index
                result.append(self.tool(tool_index, {"name": block["name"], "arguments": json_dumps(block["input"]) if block.get("input") else ""}, block["id"]))
            elif block.get("type") == "text" and block.get("text"):
                result.append(self.chunk({"content": text_value(block["text"])}))
        elif kind == "content_block_delta":
            block, delta = self.blocks[event["index"]], object_value(event["delta"])
            if block["closed"] or self.reason is not None:
                fail()
            if delta.get("type") == "text_delta":
                if block["kind"] != "text":
                    fail()
                result.append(self.chunk({"content": text_value(delta["text"])}))
            elif delta.get("type") == "input_json_delta":
                result.append(self.tool(block["tool"], {"arguments": text_value(delta["partial_json"])}))
        elif kind == "content_block_stop":
            block = self.blocks[event["index"]]
            if block["closed"]:
                fail()
            block["closed"] = True
        elif kind == "message_delta":
            self.native_usage.update(object_value(event.get("usage", {})))
            reason = object_value(event["delta"]).get("stop_reason")
            if reason:
                mapping = {"end_turn": "stop", "stop_sequence": "stop", "tool_use": "tool_calls",
                           "max_tokens": "length", "model_context_window_exceeded": "length", "refusal": "content_filter"}
                result.append(self.finish(mapping.get(reason)))
        elif kind == "message_stop":
            if self.reason is None or not self.started or any(not b["closed"] for b in self.blocks.values()):
                fail("The provider stopped before completing its response.")
            if self.native_usage:
                self.usage = token_usage(self.native_usage, "anthropic")
            self.done = True
        return result

    def _gemini(self, event):
        result = []
        if event.get("usageMetadata") is not None:
            self.usage = token_usage(event["usageMetadata"], "gemini")
        feedback = event.get("promptFeedback", {})
        if feedback.get("blockReason"):
            return [self.finish("content_filter")]
        candidates = event.get("candidates", [])
        if not isinstance(candidates, list) or len(candidates) > 1:
            fail()
        for candidate in candidates:
            if candidate.get("index", 0) != 0 or self.reason is not None:
                fail()
            for part in candidate.get("content", {}).get("parts", []):
                if part.get("text") is not None and not part.get("thought", False):
                    result.append(self.chunk({"content": text_value(part["text"])}))
                if "functionCall" in part:
                    call = object_value(part["functionCall"])
                    identity = "call_" + uuid4().hex
                    if part.get("thoughtSignature"):
                        signature = text_value(part["thoughtSignature"])
                        if len(signature) > 65536:
                            fail("The provider signature exceeded its size limit.")
                        self.signatures[identity] = signature
                        while len(self.signatures) > 512:
                            self.signatures.pop(next(iter(self.signatures)))
                    result.append(self.tool(len(self.tools), {"name": call["name"], "arguments": json_dumps(call.get("args", {}))}, identity))
            reason = candidate.get("finishReason")
            if reason:
                mapping = {"STOP": "tool_calls" if self.tools else "stop", "MAX_TOKENS": "length",
                           "SAFETY": "content_filter", "RECITATION": "content_filter", "BLOCKLIST": "content_filter",
                           "PROHIBITED_CONTENT": "content_filter", "SPII": "content_filter", "IMAGE_SAFETY": "content_filter"}
                result.append(self.finish(mapping.get(reason)))
        return result

    def _responses(self, event):
        kind, result = event.get("type"), []
        if kind in {"response.created", "response.in_progress"}:
            if not self.started:
                result.append(self.chunk({"role": "assistant"}))
                self.started = True
        elif kind in {"response.output_text.delta", "response.refusal.delta"}:
            result.append(self.chunk({"content" if kind == "response.output_text.delta" else "refusal": text_value(event["delta"])}))
        elif kind == "response.output_item.added" and event["item"].get("type") == "function_call":
            item, index = event["item"], len(self.tools)
            if item["id"] in self.response_tools:
                fail()
            self.response_tools[item["id"]] = index
            result.append(self.tool(index, {"name": item["name"], "arguments": item.get("arguments", "")}, item["call_id"]))
        elif kind == "response.function_call_arguments.delta":
            result.append(self.tool(self.response_tools[event["item_id"]], {"arguments": text_value(event["delta"])}))
        elif kind in {"response.completed", "response.incomplete"}:
            response = object_value(event["response"])
            if response.get("usage") is not None:
                self.usage = token_usage(response["usage"], "openai-responses")
            if kind == "response.incomplete":
                detail = response.get("incomplete_details", {}).get("reason")
                if detail not in {"max_output_tokens", "content_filter"}:
                    fail("The provider ended without a complete response.")
                reason = "length" if detail == "max_output_tokens" else "content_filter"
            elif any(p.get("type") == "refusal" for item in response.get("output", []) for p in item.get("content", [])):
                reason = "content_filter"
            else:
                reason = "tool_calls" if self.tools else "stop"
            result.append(self.finish(reason))
            self.done = True
        return result

    def end(self, marker=False):
        if self.protocol == "openai":
            self.done = marker
        elif self.protocol == "gemini":
            self.done = self.reason is not None
        if not self.done or self.reason is None:
            raise GatewayError("provider_incomplete", "The provider disconnected before completing its response. Try again.", status=502, retryable=True)
        return [self.chunk(usage=self.usage)] if self.usage is not None else []
