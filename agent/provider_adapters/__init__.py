"""Real asynchronous chat/SSE provider I/O with canonical stream events."""
import asyncio
from dataclasses import dataclass

import httpx

from agent.protocol import CoreError, ToolCall, canonical_json, strict_loads


# These public categories are safe to persist; arbitrary vendor strings are not.
_PUBLIC_ERROR_CODES = frozenset("""
authentication_error invalid_api_key unauthorized unauthenticated permission_denied permission_error forbidden
insufficient_quota quota_exceeded billing_hard_limit_reached rate_limit_exceeded rate_limit_error rate_limited
overloaded_error server_error internal_error timeout busy cooldown
model_not_found invalid_model model_required model_unavailable model_denied provider_unavailable
context_length_exceeded output_limit invalid_request invalid_request_error unsupported_parameter
unsupported_capability unsupported_request unsupported_content invalid_tools content_filter
provider_error provider_http provider_protocol provider_incomplete provider_bounds
provider_auth provider_rate_limit provider_not_found provider_request
account_disconnected account_reauth_required account_unavailable auth_rejected auth_unavailable
credential_unavailable invalid_key key_required sign_in_required reconnect_required
network_transport network_origin_denied network_address_denied dns_failed
""".split())
_HTTP_ERROR_CODES = {401: "authentication_error", 402: "insufficient_quota", 403: "permission_denied",
                     404: "model_not_found", 408: "timeout", 413: "context_length_exceeded",
                     429: "rate_limited", 500: "internal_error", 502: "provider_error",
                     503: "overloaded_error", 504: "timeout"}


def _provider_error_details(payload):
    """Admit only fixed public categories, never free-form remote diagnostics."""
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        for value in (error.get("code"), error.get("type")):
            if type(value) is int:
                value = _HTTP_ERROR_CODES.get(value)
            if isinstance(value, str) and value in _PUBLIC_ERROR_CODES:
                return {"provider_code": value}
    return {}


async def _next_chunk(iterator, cancel):
    """Join each pending read when cancellation interrupts provider I/O."""
    read = asyncio.create_task(anext(iterator))
    stop = asyncio.create_task(cancel.wait()) if cancel is not None else None
    try:
        if stop:
            done, _ = await asyncio.wait({read, stop}, return_when=asyncio.FIRST_COMPLETED)
            if stop in done and stop.result():
                raise CoreError("cancelled", "Provider stream cancelled")
        return await read
    finally:
        for task in (read, stop):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*[task for task in (read, stop) if task is not None], return_exceptions=True)


async def _http_error_details(response, limit, timeout, cancel):
    """Keep a bounded optional error body; an HTTP failure stays authoritative."""
    body = bytearray()
    try:
        async with asyncio.timeout(timeout):
            iterator = response.aiter_bytes().__aiter__()
            while True:
                try:
                    chunk = await _next_chunk(iterator, cancel)
                except StopAsyncIteration:
                    break
                if len(chunk) > limit - len(body):
                    return {}
                body.extend(chunk)
            return _provider_error_details(strict_loads(bytes(body)))
    except CoreError as error:
        if error.code == "cancelled":
            raise
    except (httpx.HTTPError, TimeoutError, UnicodeError, TypeError, ValueError, RecursionError):
        pass
    return {}


@dataclass(frozen=True)
class ProviderEvent:
    kind: str
    data: dict


def _object(value, label, fields):
    if not isinstance(value, dict) or set(value) - fields:
        raise CoreError("provider_protocol", f"Unsupported {label} shape")
    return value


def _usage(value):
    value = _object(value, "usage", {"total_tokens", "prompt_tokens", "completion_tokens",
                                    "prompt_tokens_details", "completion_tokens_details"})
    if "total_tokens" not in value:
        raise CoreError("provider_protocol", "Provider usage requires total_tokens")
    for key, count in value.items():
        if key.endswith("_details"):
            if count is None:
                continue
            if not isinstance(count, dict) or len(count) > 32:
                raise CoreError("provider_protocol", "Token details must be a bounded object")
            counts = count.values()
        else:
            counts = (count,)
        if any(type(number) is not int or not 0 <= number < 2 ** 63 for number in counts):
            raise CoreError("provider_protocol", "Token usage must contain nonnegative integer counts")
    if ("prompt_tokens" in value and "completion_tokens" in value
            and value["total_tokens"] != value["prompt_tokens"] + value["completion_tokens"]):
        raise CoreError("provider_protocol", "Provider token counts disagree")
    return value


class HTTPProvider:
    def __init__(self, *, timeout=30, max_event_bytes=1048576, max_tool_bytes=None,
                 max_tool_parts=4096, max_tool_calls=128, client_factory=None):
        if max_tool_bytes is None:
            max_tool_bytes = max_event_bytes
        if (type(timeout) not in {int, float} or not 0 < timeout < float("inf")
                or any(type(limit) is not int or limit < 1
                       for limit in (max_event_bytes, max_tool_bytes, max_tool_parts, max_tool_calls))
                or max_tool_calls > 128):
            raise CoreError("invalid_limits", "Provider limits must be positive and bounded")
        self.timeout, self.max_event_bytes = timeout, max_event_bytes
        self.max_tool_bytes, self.max_tool_parts, self.max_tool_calls = max_tool_bytes, max_tool_parts, max_tool_calls
        self.client_factory = client_factory or httpx.AsyncClient

    async def stream(self, descriptor, messages, *, tools=None, credential=None, cancel=None):
        if cancel is not None and cancel.is_set():
            raise CoreError("cancelled", "Model request cancelled before dispatch")
        if any(not isinstance(m, dict) or m.get("role") not in {"system", "developer", "user", "assistant", "tool"} for m in messages):
            raise CoreError("invalid_context", "Provider messages must preserve valid roles")
        if tools and not descriptor.get("tools"):
            raise CoreError("incompatible", "Required tools unsupported")
        headers = {"Accept": "text/event-stream"}
        if descriptor["authentication"] == "bearer":
            if not credential:
                raise CoreError("credential_unavailable", "Provider credential required")
            headers["Authorization"] = "Bearer " + credential
        payload = {"model": descriptor["model"], "messages": strict_loads(canonical_json(messages)),
                   "stream": True, "max_tokens": descriptor["output_limit"]}
        if tools:
            payload["tools"] = tools
        call_parts, finished, finish_reason, refusal = {}, False, None, False
        tool_bytes, tool_parts, usage_seen = 0, 0, None
        try:
            async with self.client_factory(timeout=self.timeout, follow_redirects=False, trust_env=False) as client:
                async with client.stream("POST", descriptor["endpoint"], json=payload, headers=headers) as response:
                    if response.status_code != 200:
                        details = await _http_error_details(response, self.max_event_bytes, self.timeout, cancel)
                        raise CoreError("provider_http", "Provider rejected request", status=response.status_code,
                                        retryable=response.status_code in {429, 500, 502, 503, 504}, **details)
                    iterator = response.aiter_bytes().__aiter__()
                    buffer = b""
                    while True:
                        try:
                            chunk = await _next_chunk(iterator, cancel)
                        except StopAsyncIteration:
                            break
                        buffer += chunk
                        while b"\n" in buffer:
                            line, buffer = buffer.split(b"\n", 1)
                            if len(line) > self.max_event_bytes:
                                raise CoreError("provider_bounds", "Provider event exceeds limit")
                            line = line.strip()
                            if not line.startswith(b"data:"):
                                continue
                            value = line[5:].strip()
                            if value == b"[DONE]":
                                finished = True
                                break
                            parsed = strict_loads(value)
                            if not isinstance(parsed, dict):
                                raise CoreError("provider_protocol", "Provider event must be an object")
                            if "error" in parsed:
                                raise CoreError("provider_error", "Provider returned an error event",
                                                **_provider_error_details(parsed))
                            choices = parsed.get("choices")
                            if not isinstance(choices, list) or len(choices) > 1:
                                raise CoreError("provider_protocol", "Provider must stream at most one choice")
                            if parsed.get("usage") is not None:
                                usage = _usage(parsed["usage"])
                                if usage_seen is not None and usage != usage_seen:
                                    raise CoreError("provider_protocol", "Provider usage changed after its final report")
                                if usage_seen is None:
                                    usage_seen = usage
                                    yield ProviderEvent("usage", usage)
                            for choice in choices:
                                choice = _object(choice, "choice", {"index", "delta", "finish_reason", "logprobs"})
                                if type(choice.get("index", 0)) is not int or choice.get("index", 0) != 0:
                                    raise CoreError("provider_protocol", "Unexpected provider choice index")
                                delta = _object(choice.get("delta"), "delta", {"role", "content", "refusal", "tool_calls"})
                                if delta.get("role") not in {None, "assistant"}:
                                    raise CoreError("provider_protocol", "Provider delta role must be assistant")
                                if finish_reason is not None:
                                    raise CoreError("provider_protocol", "Provider emitted a choice after its finish reason")
                                for field in ("content", "refusal"):
                                    if delta.get(field) is not None:
                                        if not isinstance(delta[field], str):
                                            raise CoreError("provider_protocol", "Visible provider content must be text")
                                        if field == "refusal":
                                            refusal = True
                                        if delta[field]:
                                            yield ProviderEvent("text", {"text": delta[field]})
                                parts = delta.get("tool_calls", [])
                                if not isinstance(parts, list):
                                    raise CoreError("provider_protocol", "Tool deltas must be an array")
                                for part in parts:
                                    part = _object(part, "tool delta", {"index", "id", "type", "function"})
                                    index = part.get("index", 0)
                                    if type(index) is not int or not 0 <= index < self.max_tool_calls:
                                        raise CoreError("provider_bounds", "Tool call index exceeds limit")
                                    if part.get("type", "function") != "function":
                                        raise CoreError("provider_protocol", "Only function tool calls are supported")
                                    tool_parts += 1
                                    tool_bytes += len(canonical_json(part).encode())
                                    if tool_parts > self.max_tool_parts or tool_bytes > self.max_tool_bytes:
                                        raise CoreError("provider_bounds", "Aggregate tool fragments exceed limit")
                                    item = call_parts.setdefault(index, {"id": None, "name": "", "arguments": ""})
                                    if "id" in part:
                                        identity = part["id"]
                                        if not isinstance(identity, str) or not identity or len(identity.encode()) > 256:
                                            raise CoreError("provider_protocol", "Tool call ID must be bounded nonempty text")
                                        if item["id"] not in {None, identity}:
                                            raise CoreError("provider_protocol", "Tool call ID changed during streaming")
                                        item["id"] = identity
                                    function = _object(part.get("function", {}), "tool function", {"name", "arguments"})
                                    for field in ("name", "arguments"):
                                        fragment = function.get(field, "")
                                        if not isinstance(fragment, str):
                                            raise CoreError("provider_protocol", "Tool function fragments must be text")
                                        item[field] += fragment
                                    if len(item["name"].encode()) > 256:
                                        raise CoreError("provider_bounds", "Tool function name exceeds limit")
                                reason = choice.get("finish_reason")
                                if reason is not None:
                                    if reason not in {"stop", "tool_calls", "length", "content_filter"}:
                                        raise CoreError("provider_protocol", "Unknown provider finish reason")
                                    finish_reason = reason
                        if len(buffer) > self.max_event_bytes:
                            raise CoreError("provider_bounds", "Unterminated provider event exceeds limit")
                        if finished:
                            break
                    if not finished:
                        raise CoreError("provider_incomplete", "Provider stream ended without terminal marker")
                    if refusal or finish_reason == "content_filter":
                        status, reason = "refused", "refusal"
                    elif finish_reason == "length":
                        status, reason = "incomplete", "max_tokens"
                    else:
                        status = "completed"
                        reason = {None: "unspecified", "stop": "end_turn", "tool_calls": "tool_calls"}[finish_reason]
                        if (finish_reason == "tool_calls" and not call_parts
                                or finish_reason == "stop" and call_parts):
                            raise CoreError("provider_protocol", "Finish reason disagrees with tool proposals")
                        proposals, identities = [], set()
                        for item in call_parts.values():
                            if item["name"] != "invoke" or not item["id"] or item["id"] in identities:
                                raise CoreError("provider_protocol", "Tool proposals require invoke and distinct provider IDs")
                            identities.add(item["id"])
                            call = ToolCall.from_dict(strict_loads(item["arguments"]))
                            proposals.append(ProviderEvent("tool", {"id": item["id"], "call": call.as_dict()}))
                        for proposal in proposals:
                            yield proposal
                    yield ProviderEvent("terminal", {"status": status, "stop_reason": reason,
                                                     "provider_finish_reason": finish_reason})
        except httpx.HTTPError as exc:
            raise CoreError("provider_transport", "Provider transport failed", type=type(exc).__name__) from exc
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            raise CoreError("provider_protocol", "Malformed provider event or request") from exc
