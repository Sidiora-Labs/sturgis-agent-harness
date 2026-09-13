"""Native subscription inference and live catalogs behind account bindings.

Gemini Code Assist wraps the public Gemini message schema. Kiro uses a bounded
AWS EventStream with both CRCs verified before any payload is interpreted. A
clean HTTP/EventStream EOF is a native Kiro completion boundary; partial frames,
unfinished tool JSON and explicit failures never become successful responses.
"""
from __future__ import annotations

import asyncio
from contextlib import aclosing
import struct
import math
from urllib.parse import urlencode
from uuid import uuid4
import zlib

from .errors import GatewayError
from .network import origin
from .translation import StreamTranslator, _parts, _arguments, translate_request, object_value, text_value, token_usage, fail
from .transports import GatewayTransport, MAX_CATALOG_MODELS, _model, _next_or_cancel, _sse, provider_failure
from .wire import json_dumps, json_loads

MAX_FRAME_BYTES = 1048576


def kiro_request(payload, profile_arn):
    """Preserve text, inline images, function calls and results in Kiro's native history."""
    body = translate_request("openai", payload, {})
    if body.get("response_format") or body.get("reasoning_effort") or body.get("tool_choice", "auto") not in ("auto", "none"):
        raise GatewayError("unsupported_capability", "This Kiro connection does not support the requested structured output, reasoning override or forced tool choice.")
    history, instructions, known_calls, answered = [], [], set(), set()
    for message in body["messages"]:
        role = message["role"]
        parts = _parts(message.get("content"), "gemini")
        content = "\n".join(p["text"] for p in parts if "text" in p)
        images = [{"format": p["inlineData"]["mimeType"].split("/")[-1], "source": {"bytes": p["inlineData"]["data"]}} for p in parts if "inlineData" in p]
        if role in {"system", "developer"}:
            if images:
                raise GatewayError("unsupported_content", "Kiro instructions must be text.")
            instructions.append(content)
            continue
        if role == "assistant":
            item = {"content": content}
            if images:
                raise GatewayError("unsupported_content", "Kiro assistant history must be text or function calls.")
            uses = []
            for call in message.get("tool_calls", []):
                call, function = object_value(call), object_value(call.get("function"))
                identity = text_value(call.get("id"))
                if not identity or identity in known_calls:
                    raise GatewayError("invalid_request", "Function call identifiers must be unique.")
                known_calls.add(identity)
                uses.append({"toolUseId": identity, "name": text_value(function.get("name")), "input": _arguments(function.get("arguments"))})
            if uses:
                item["toolUses"] = uses
            key = "assistantResponseMessage"
        else:
            key, item = "userInputMessage", {"content": content, "modelId": body["model"], "origin": "AI_EDITOR"}
            if images:
                item["images"] = images
            if role == "tool":
                identity = message.get("tool_call_id")
                if identity not in known_calls or identity in answered:
                    raise GatewayError("invalid_request", "A function result must match one unanswered function call.")
                answered.add(identity)
                item["content"] = ""
                item["userInputMessageContext"] = {"toolResults": [{"toolUseId": identity, "status": "error" if message.get("is_error") else "success", "content": [{"text": content}]}]}
        if history and key in history[-1]:
            prior = history[-1][key]
            prior["content"] = "\n\n".join(s for s in (prior["content"], item["content"]) if s)
            for field in ("images", "toolUses"):
                if item.get(field):
                    prior.setdefault(field, []).extend(item[field])
            if item.get("userInputMessageContext"):
                prior.setdefault("userInputMessageContext", {}).setdefault("toolResults", []).extend(item["userInputMessageContext"]["toolResults"])
        else:
            history.append({key: item})
    if not history or "userInputMessage" not in history[-1] or known_calls != answered:
        raise GatewayError("invalid_request", "Kiro needs a final user message or all outstanding function results.")
    if instructions:
        first = next((m["userInputMessage"] for m in history if "userInputMessage" in m), None)
        first["content"] = "<instructions>\n" + "\n\n".join(instructions) + "\n</instructions>\n\n" + first["content"]
    current = history.pop()
    specs = []
    for tool in body.get("tools", []) if body.get("tool_choice") != "none" else []:
        function = object_value(object_value(tool).get("function"))
        specs.append({"toolSpecification": {"name": text_value(function.get("name")), "description": text_value(function.get("description", "")),
                      "inputSchema": {"json": object_value(function.get("parameters", {"type": "object", "properties": {}}))}}})
    if specs:
        current["userInputMessage"].setdefault("userInputMessageContext", {})["tools"] = specs
    result = {"conversationState": {"chatTriggerType": "MANUAL", "conversationId": str(uuid4()), "currentMessage": current, "history": history},
              "profileArn": profile_arn, "inferenceConfig": {"maxTokens": body.get("max_completion_tokens", body.get("max_tokens", 4096))}}
    for key, target in (("temperature", "temperature"), ("top_p", "topP")):
        if key in body:
            result["inferenceConfig"][target] = body[key]
    return result


def _event_headers(data):
    """Decode bounded AWS typed headers; recognized routing headers must be strings."""
    result, offset = {}, 0
    sizes = {0: 0, 1: 0, 2: 1, 3: 2, 4: 4, 5: 8, 8: 8, 9: 16}
    while offset < len(data):
        length = data[offset]
        offset += 1
        if not length or offset + length + 1 > len(data):
            fail("Kiro returned an invalid EventStream header.")
        name = data[offset:offset + length].decode("utf-8")
        offset += length
        type_id = data[offset]
        offset += 1
        if type_id in (6, 7):
            if offset + 2 > len(data):
                fail()
            length = int.from_bytes(data[offset:offset + 2], "big")
            offset += 2
        elif type_id in sizes:
            length = sizes[type_id]
        else:
            fail("Kiro returned an unknown EventStream header type.")
        if offset + length > len(data) or name in result:
            fail("Kiro returned an invalid EventStream header.")
        value = data[offset:offset + length]
        offset += length
        if name in {":event-type", ":message-type", ":content-type"} and type_id != 7:
            fail()
        result[name] = value.decode("utf-8") if type_id == 7 else value
    return result


async def aws_events(response):
    """Yield verified frames without ever buffering more than one bounded frame."""
    buffer = bytearray()
    async for chunk in response.aiter_bytes():
        # The network layer also caps the aggregate stream. Chunk slicing avoids
        # allocating an unbounded intermediate buffer before frame validation.
        for offset in range(0, len(chunk), 65536):
            buffer.extend(chunk[offset:offset + 65536])
            while len(buffer) >= 12:
                total, headers_length, prelude_crc = struct.unpack("!III", buffer[:12])
                if zlib.crc32(buffer[:8]) != prelude_crc or not 16 <= total <= MAX_FRAME_BYTES or headers_length > total - 16:
                    fail("Kiro returned an invalid EventStream prelude or frame size.")
                if len(buffer) < total:
                    break
                frame = bytes(buffer[:total])
                del buffer[:total]
                if zlib.crc32(frame[:-4]) != int.from_bytes(frame[-4:], "big"):
                    fail("Kiro returned an EventStream checksum mismatch.")
                headers = _event_headers(frame[12:12 + headers_length])
                if headers.get(":message-type") in {"error", "exception"}:
                    raise GatewayError("provider_error", "Kiro could not complete this request.", 502, retryable=True)
                if headers.get(":message-type") != "event" or not headers.get(":event-type"):
                    fail("Kiro returned an invalid EventStream event.")
                yield headers[":event-type"], json_loads(frame[12 + headers_length:-4])
    if buffer:
        raise GatewayError("provider_incomplete", "Kiro disconnected during an EventStream frame.", 502, retryable=True)


class AuthTransport:
    """Server-only provider bindings are never accepted directly from clients."""

    def __init__(self, network_policy):
        self.network_policy = network_policy
        self.transport = GatewayTransport(network_policy)
        self._signatures = {}

    async def discover(self, binding):
        """Fetch the account's actual model catalog; never substitute a static list."""
        try:
            return await self._discover(binding)
        except GatewayError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
            raise GatewayError("invalid_catalog", "The account returned an invalid model catalog.", 502) from None

    async def _discover(self, binding):
        provider, base = binding["provider_id"], binding["endpoint"].rstrip("/")
        method, body = "GET", None
        if provider == "gemini-cli":
            url, method, body = base + ":fetchAvailableModels", "POST", {"project": binding["project_id"]}
        elif provider == "kiro":
            url = f"https://q.{binding['region']}.amazonaws.com/ListAvailableModels?" + urlencode({"origin": "AI_EDITOR", "profileArn": binding["profile_arn"]})
        else:
            url = base + ("/models?client_version=0.144.6" if provider == "codex" else "/models")
        url = binding.get("models_endpoint") or url
        headers = {**binding.get("headers", {}), "Authorization": "Bearer " + binding["credential"], "Accept": "application/json", "Content-Type": "application/json"}
        response = await self.network_policy.request(method, url, allowed_origins=(origin(url),), headers=headers,
                    content=json_dumps(body).encode() if body is not None else None, max_bytes=2097152, follow_redirects=False)
        if response.status != 200:
            raise provider_failure(response.status)
        data = object_value(response.json())
        rows = data.get("models", data.get("data"))
        if provider == "gemini-cli" and isinstance(rows, dict):
            rows = [{**object_value(info), "id": id} for id, info in rows.items() if isinstance(info, dict) and not info.get("isInternal")]
        if not isinstance(rows, list) or len(rows) > MAX_CATALOG_MODELS:
            raise GatewayError("invalid_catalog", "The account returned an invalid or oversized model catalog.", 502)
        result, seen = [], set()
        for item in rows:
            item = object_value(item)
            if item.get("isInternal") or item.get("type") == "image":
                continue
            identity = item.get("id", item.get("slug", item.get("modelId", item.get("model", item.get("name")))))
            protocol = binding["protocol"]
            capabilities = item.get("capabilities", {})
            if provider == "github-copilot":
                capabilities = object_value(capabilities)
                if capabilities.get("type") != "chat" or object_value(item.get("policy", {})).get("state") == "disabled":
                    continue
                endpoints = item.get("supported_endpoints", [])
                protocol = "anthropic" if isinstance(identity, str) and identity.startswith("claude") else ("openai-responses" if "/responses" in endpoints or "codex" in str(identity) else "openai")
            row = _model({**item, "id": identity, "name": item.get("display_name", item.get("displayName", item.get("modelName", item.get("name", identity)))),
                          "context_length": item.get("context_length", item.get("context_window", object_value(capabilities).get("limits", {}).get("max_context_window_tokens"))),
                          "max_output_tokens": item.get("max_output_tokens", object_value(capabilities).get("limits", {}).get("max_output_tokens"))}, protocol, provider)
            if row is not None and row["id"] not in seen:
                support = capabilities.get("supports", {}) if isinstance(capabilities, dict) else {}
                if isinstance(support, dict) and type(support.get("tool_calls")) is bool:
                    row["tools"] = support["tool_calls"]
                result.append(row)
                seen.add(row["id"])
        return result

    async def stream(self, binding, payload, cancel=None, request_timeout=None):
        """Stream with cancellation while one task owns each upstream HTTP context."""
        if request_timeout is not None and (type(request_timeout) not in (int, float) or not 0 < request_timeout <= self.network_policy.timeout or not math.isfinite(request_timeout)):
            raise GatewayError("invalid_limits", "The request deadline exceeds the gateway network authority.")
        if binding["provider_id"] in {"codex", "github-copilot"}:
            protocol = binding.get("model_protocol") or binding["protocol"]
            # Explicit manual imports have no discovery metadata. These native
            # Copilot model families require their dedicated API routes.
            if binding["provider_id"] == "github-copilot":
                if payload["model"].startswith("claude"):
                    protocol = "anthropic"
                elif "codex" in payload["model"]:
                    protocol = "openai-responses"
            endpoint = binding["endpoint"]
            if binding["provider_id"] == "github-copilot" and protocol == "anthropic":
                endpoint += "/v1"
                # Copilot's native Anthropic route uses OAuth bearer auth.
                credential = None
                headers = {**binding["headers"], "Authorization": "Bearer " + binding["credential"]}
            else:
                credential, headers = binding["credential"], binding["headers"]
            async with aclosing(self.transport.stream(protocol, endpoint, payload, credential, preset_id=binding["provider_id"], cancel=cancel, extra_headers=headers, request_timeout=request_timeout)) as source:
                async for chunk in source:
                    yield chunk
            return
        queue = asyncio.Queue(maxsize=1)
        async def produce():
            try:
                async with asyncio.timeout(request_timeout):
                    async with aclosing(self._native(binding, payload)) as source:
                        async for event in source:
                            await queue.put((True, event))
            except TimeoutError:
                await queue.put((False, GatewayError("timeout", "The model request exceeded its configured deadline.", 504, retryable=True)))
            except Exception as error:
                await queue.put((False, error))
            else:
                await queue.put((False, None))
        producer = asyncio.create_task(produce())
        try:
            while True:
                success, event = await _next_or_cancel(queue, cancel)
                if not success:
                    if event is not None:
                        raise event
                    return
                yield event
        finally:
            producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)

    async def _native(self, binding, payload):
        try:
            provider, base = binding["provider_id"], binding["endpoint"].rstrip("/")
            headers = {**binding["headers"], "Authorization": "Bearer " + binding["credential"], "Content-Type": "application/json"}
            if provider == "gemini-cli":
                body = {"project": binding["project_id"], "model": payload["model"], "request": translate_request("gemini", payload, self._signatures)}
                url, headers["Accept"] = base + ":streamGenerateContent?alt=sse", "text/event-stream"
            elif provider == "kiro":
                body = kiro_request(payload, binding["profile_arn"])
                url, headers["Accept"] = base + "/generateAssistantResponse", "application/vnd.amazon.eventstream"
            else:
                raise GatewayError("unsupported_auth_provider", "This account has no native inference transport.")
            async with self.network_policy.stream("POST", url, allowed_origins=(origin(base),), headers=headers, content=json_dumps(body).encode(), follow_redirects=False) as response:
                if response.status != 200:
                    raise provider_failure(response.status)
                if provider == "gemini-cli":
                    translator = StreamTranslator("gemini", payload["model"], self._signatures)
                    async for data in _sse(response):
                        wrapper = object_value(json_loads(data))
                        for event in translator.feed(wrapper.get("response", wrapper)):
                            yield event
                    for event in translator.end():
                        yield event
                else:
                    async for event in self._kiro(response, payload["model"]):
                        yield event
        except GatewayError as error:
            if error.code == "invalid_json":
                raise GatewayError("provider_protocol", "The provider returned invalid native stream JSON.", 502) from None
            raise
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError):
            raise GatewayError("provider_protocol", "The provider's native response could not be read.", 502) from None

    async def _kiro(self, response, model):
        translator, identities, reason, output = StreamTranslator("openai", model, {}), {}, None, False
        async for kind, data in aws_events(response):
            data = object_value(data)
            data = object_value(data.get(kind, data))
            if kind in {"assistantResponseEvent", "codeEvent"}:
                if reason is not None:
                    fail("Kiro continued generating after its stop event.")
                content = text_value(data.get("content", ""))
                if content:
                    output = True
                    yield translator.chunk({"content": content})
            elif kind == "toolUseEvent":
                if reason is not None:
                    fail()
                identity, name = text_value(data.get("toolUseId")), text_value(data.get("name", ""))
                first = identity not in identities
                if first:
                    identities[identity] = len(identities)
                elif name and translator.tools[identities[identity]]["name"] != name:
                    fail("Kiro changed a function name between fragments.")
                value = data.get("input", "")
                arguments = json_dumps(value) if isinstance(value, dict) else text_value(value)
                output = True
                yield translator.tool(identities[identity], {"name": name if first else "", "arguments": arguments}, identity)
            elif kind in {"messageStopEvent", "metadataEvent", "MetadataEvent"}:
                declared = data.get("stopReason", data.get("stop_reason"))
                if declared is not None or kind == "messageStopEvent":
                    mapping = {"end_turn": "stop", "stop": "stop", "stop_sequence": "stop", "tool_use": "tool_calls", "max_tokens": "length", "model_context_window_exceeded": "length", "content_filter": "content_filter"}
                    current = mapping.get(declared) if declared is not None else ("tool_calls" if identities else "stop")
                    if current is None or (reason is not None and reason != current):
                        fail("Kiro returned an unsupported or inconsistent stop reason.")
                    reason = current
            elif kind in {"usageEvent", "metricsEvent"}:
                if kind == "metricsEvent":
                    data = {"input_tokens": data.get("inputTokens"), "output_tokens": data.get("outputTokens")}
                translator.usage = token_usage(data, "openai-responses")
            # Reasoning, quota percentages and billing credits are not token
            # counts or answer text; they are not invented as either here.
        if not output and reason is None:
            raise GatewayError("provider_incomplete", "Kiro completed without output or a stop event.", 502, retryable=True)
        yield translator.finish(reason or ("tool_calls" if identities else "stop"))
        if translator.usage is not None:
            yield translator.chunk(usage=translator.usage)
