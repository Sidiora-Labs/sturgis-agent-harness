"""Policy-pinned provider execution and bounded live model discovery."""
from __future__ import annotations

import asyncio
import re
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4

from .errors import GatewayError
from .network import origin
from .translation import PROTOCOLS, StreamTranslator, translate_request
from .wire import json_dumps, json_loads

MAX_EVENT_BYTES = 1048576
MAX_CATALOG_MODELS = 2000
MAX_CATALOG_PAGES = 5


def provider_failure(status):
    """Map upstream status to safe actionable text, without returning its body."""
    if status in {401, 403}:
        return GatewayError("provider_auth", "The provider rejected this API key or account. Check its key and access permissions.", status=502)
    if status == 429:
        return GatewayError("provider_rate_limit", "This provider has reached its rate or quota limit. Wait a moment or choose another provider.", status=503, retryable=True)
    if status == 404:
        return GatewayError("provider_not_found", "The model or API endpoint was not found. Refresh models or check the endpoint.", status=502)
    if status in {400, 422}:
        return GatewayError("provider_request", "The provider does not accept this request or model capability. Check the selected model and protocol.", status=502)
    return GatewayError("provider_unavailable", "The provider is temporarily unavailable. Try again or choose another provider.", status=502, retryable=status >= 500)


def _base(protocol, endpoint, preset_id):
    if protocol not in PROTOCOLS:
        raise GatewayError("unsupported_protocol", "Choose a supported provider protocol.")
    origin(endpoint)
    parsed = urlsplit(endpoint)
    if parsed.query or parsed.fragment:
        raise GatewayError("invalid_endpoint", "The endpoint must not contain a query string or fragment.")
    path = parsed.path.rstrip("/")
    for suffix in ("/chat/completions", "/messages", "/responses", "/models"):
        if path.endswith(suffix):
            path = path[:-len(suffix)]
            break
    if not path:
        path = "/zen/v1" if preset_id == "opencode" else ""
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _headers(protocol, credential, preset_id, *, stream=False):
    headers = {"Accept": "text/event-stream" if stream else "application/json", "Content-Type": "application/json"}
    if credential is not None and (not isinstance(credential, str) or not credential or len(credential) > 8192
                                  or any(ord(c) < 33 or ord(c) > 126 for c in credential)):
        raise GatewayError("invalid_credential", "The API key must be nonempty text without spaces or control characters.")
    if protocol == "anthropic":
        headers["anthropic-version"] = "2023-06-01"
        if credential:
            headers["x-api-key"] = credential
    elif protocol == "gemini":
        if credential:
            headers["x-goog-api-key"] = credential
    elif credential:
        headers["Authorization"] = "Bearer " + credential
    if preset_id == "opencode":
        headers.update({"Authorization": "Bearer " + (credential or "public"), "User-Agent": "opencode",
                        "x-opencode-client": "desktop", "x-opencode-session": "ses_" + uuid4().hex,
                        "x-opencode-request": "msg_" + uuid4().hex, "x-opencode-project": "global"})
    return headers


async def _sse(response):
    """Decode bounded UTF-8 SSE records, including multiline data and CRLF."""
    buffer, lines, size = b"", [], 0
    async for chunk in response.aiter_bytes():
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            line = line.rstrip(b"\r")
            size += len(line) + 1
            if size > MAX_EVENT_BYTES:
                raise GatewayError("provider_bounds", "A provider stream event exceeded the size limit.", status=502)
            if not line:
                if lines:
                    yield b"\n".join(lines)
                lines, size = [], 0
            elif line.startswith(b"data:"):
                lines.append(line[5:].lstrip(b" "))
        if len(buffer) + size > MAX_EVENT_BYTES:
            raise GatewayError("provider_bounds", "A provider stream event exceeded the size limit.", status=502)
    if buffer.strip() or lines:
        raise GatewayError("provider_incomplete", "The provider disconnected in the middle of a stream event.", status=502, retryable=True)


async def _next_or_cancel(queue, cancel):
    if cancel is None:
        return await queue.get()
    if cancel.is_set():
        raise GatewayError("cancelled", "The model request was cancelled.", status=499)
    read, stop = asyncio.create_task(queue.get()), asyncio.create_task(cancel.wait())
    try:
        done, _ = await asyncio.wait({read, stop}, return_when=asyncio.FIRST_COMPLETED)
        if stop in done and stop.result():
            raise GatewayError("cancelled", "The model request was cancelled.", status=499)
        return await read
    finally:
        for task in (read, stop):
            if not task.done():
                task.cancel()
        await asyncio.gather(read, stop, return_exceptions=True)


class GatewayTransport:
    """Standalone upstream client; the caller owns routing and downstream SSE."""

    def __init__(self, network_policy):
        self.network_policy = network_policy
        self._signatures = {}

    async def stream(self, protocol, endpoint, payload, credential=None, *, preset_id=None, cancel=None, extra_headers=None):
        if cancel is not None and cancel.is_set():
            raise GatewayError("cancelled", "The model request was cancelled.", status=499)
        queue = asyncio.Queue(maxsize=1)

        async def produce():
            # One task owns the entire HTTP context, preserving its deadline and
            # cancellation scope across every yielded chunk and backpressure wait.
            try:
                async for event in self._stream(protocol, endpoint, payload, credential, preset_id, extra_headers):
                    await queue.put((True, event))
            except Exception as exc:
                await queue.put((False, exc))
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

    async def _stream(self, protocol, endpoint, payload, credential, preset_id, extra_headers=None):
        try:
            base = _base(protocol, endpoint, preset_id)
            body = translate_request(protocol, payload, self._signatures)
            if preset_id == "codex":
                body["store"] = False
                body.setdefault("instructions", "")
                # The subscription endpoint rejects these Chat/Responses options;
                # the gateway still bounds time, bytes, concurrency and tools.
                for key in ("max_output_tokens", "temperature", "top_p"):
                    body.pop(key, None)
            headers = {**(extra_headers or {}), **_headers(protocol, credential, preset_id, stream=True)}
            if protocol == "gemini":
                model = payload["model"].removeprefix("models/")
                url = base + "/models/" + quote(model, safe="") + ":streamGenerateContent?alt=sse"
            else:
                url = base + {"openai": "/chat/completions", "anthropic": "/messages", "openai-responses": "/responses"}[protocol]
            translator, marker = StreamTranslator(protocol, payload["model"], self._signatures), False
            async with self.network_policy.stream("POST", url, allowed_origins=(origin(base),),
                    headers=headers, content=json_dumps(body).encode(),
                    follow_redirects=False) as response:
                if response.status != 200:
                    raise provider_failure(response.status)
                async for data in _sse(response):
                    if data == b"[DONE]":
                        marker = True
                        break
                    event = json_loads(data)
                    for chunk in translator.feed(event):
                        yield chunk
                for chunk in translator.end(marker):
                    yield chunk
        except GatewayError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError):
            raise GatewayError("provider_protocol", "The provider response or request could not be read. Check the selected protocol.", status=502) from None


def _model(row, protocol, preset_id):
    if not isinstance(row, dict):
        raise GatewayError("invalid_catalog", "The provider returned an invalid model list.", status=502)
    # Positive provider metadata wins. Known specialized model families cannot
    # be used with a chat endpoint and must not appear as connectable chat models.
    identity_hint = row.get("id", row.get("name", ""))
    if row.get("supports_chat") is False or (isinstance(identity_hint, str) and re.search(
            r"(?:^|/)(?:text-embedding|embedding|whisper|tts-|dall-e|gpt-image|omni-moderation|rerank)|(?:^|[-/])(?:realtime|transcribe)(?:$|-)", identity_hint, re.I)):
        return None
    architecture = row.get("architecture", {})
    if isinstance(architecture, dict):
        outputs = architecture.get("output_modalities", row.get("output_modalities"))
        if isinstance(outputs, list) and outputs and "text" not in outputs:
            return None
    if protocol == "gemini":
        if "generateContent" not in row.get("supportedGenerationMethods", []):
            return None
        identity = row.get("name", "").removeprefix("models/")
        name, context, output = row.get("displayName", identity), row.get("inputTokenLimit"), row.get("outputTokenLimit")
    else:
        identity = row.get("id")
        name = row.get("display_name", row.get("name", identity))
        context = row.get("context_length", row.get("context_window", row.get("max_input_tokens")))
        output = row.get("max_output_tokens", row.get("top_provider", {}).get("max_completion_tokens"))
    if (not isinstance(identity, str) or not identity or len(identity) > 256 or any(ord(c) < 32 for c in identity)
            or not isinstance(name, str) or not name or len(name) > 512):
        raise GatewayError("invalid_catalog", "The provider returned an invalid model name.", status=502)
    if preset_id == "opencode" and (identity == "deepseek-v4-flash-free" or not (identity.endswith("-free") or identity == "big-pickle")):
        return None
    result = {"id": identity, "name": name, "protocol": protocol}
    for key, limit in (("context_limit", context), ("output_limit", output)):
        if type(limit) is int and 0 < limit <= 2000000:
            result[key] = limit
    supported = row.get("supported_parameters")
    if isinstance(supported, list):
        result["tools"] = "tools" in supported
    elif type(row.get("supports_tools")) is bool:
        result["tools"] = row["supports_tools"]
    if preset_id == "opencode":
        result["protocol"] = "openai-responses" if identity.startswith("muse-spark-") else "openai"
        result["free"] = True
    pricing = row.get("pricing")
    if isinstance(pricing, dict) and "prompt" in pricing and "completion" in pricing:
        result["free"] = str(pricing["prompt"]) == "0" and str(pricing["completion"]) == "0"
    return result


async def discover_models(protocol, endpoint, credential, network_policy, *, preset_id=None):
    """Fetch at most five pages and 2,000 models without trusting remote next URLs."""
    base = _base(protocol, endpoint, preset_id)
    result, seen, cursors, cursor, consumed = [], set(), set(), None, 0
    try:
        for page in range(MAX_CATALOG_PAGES):
            params = ({"pageSize": 1000} if protocol == "gemini" else {"limit": 1000} if protocol == "anthropic" else {})
            if cursor:
                params["pageToken" if protocol == "gemini" else "after_id"] = cursor
            remaining = network_policy.max_bytes - consumed
            if remaining <= 0:
                raise GatewayError("catalog_bounds", "The model catalog exceeded the size limit.", status=502)
            response = await network_policy.request("GET", base + "/models", allowed_origins=(origin(base),),
                headers=_headers(protocol, credential, preset_id), params=params, follow_redirects=False,
                max_bytes=min(remaining, 2097152))
            if response.status != 200:
                raise provider_failure(response.status)
            consumed += len(response.body)
            body = json_loads(response.body)
            rows = body.get("models" if protocol == "gemini" else "data")
            if not isinstance(rows, list) or len(rows) > MAX_CATALOG_MODELS:
                raise GatewayError("invalid_catalog", "The provider returned an invalid or oversized model list.", status=502)
            for row in rows:
                model = _model(row, protocol, preset_id)
                if model and model["id"] not in seen:
                    result.append(model)
                    seen.add(model["id"])
                    if len(result) > MAX_CATALOG_MODELS:
                        raise GatewayError("catalog_bounds", "The provider model list exceeded the model limit.", status=502)
            cursor = body.get("nextPageToken") if protocol == "gemini" else body.get("last_id") if protocol == "anthropic" and body.get("has_more") else None
            if not cursor:
                if protocol == "anthropic" and body.get("has_more"):
                    raise GatewayError("invalid_catalog", "The provider omitted its next model-page token.", status=502)
                return result
            if not isinstance(cursor, str) or len(cursor) > 4096 or cursor in cursors or page == MAX_CATALOG_PAGES - 1:
                raise GatewayError("catalog_bounds", "The provider model pagination exceeded its limit.", status=502)
            cursors.add(cursor)
    except GatewayError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        raise GatewayError("invalid_catalog", "The provider model list could not be read. Check the endpoint and protocol.", status=502) from None
