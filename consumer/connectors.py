"""Concrete bounded search, MCP, media and managed-browser connection adapters."""
from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
import hashlib

from agent.plugin_sdk import _check_schema, validate_schema
from agent.protocol import CoreError, canonical_json, new_id, strict_loads
from agent.tool_runner import ExecutorResult
from consumer.connections import ConnectionOperation
from consumer.contracts import identifier, object_body
from consumer.network import origin

MCP_VERSION = "2025-11-25"  # Explicit compatibility target; never negotiate silently.


def schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


def text_schema(limit=8192):
    return {"type": "string", "minLength": 1, "maxLength": limit}


def _remote(response):
    if not 200 <= response.status < 300:
        raise CoreError("remote_status", "Remote service rejected the request", status=response.status, effects="unknown")
    return response


def _options(options):
    for key in ("model", "voice"):
        if key in options and (not isinstance(options[key], str) or not 1 <= len(options[key]) <= 128):
            raise CoreError("invalid_connection", "Model and voice must be bounded text")
    if "max_results" in options and (type(options["max_results"]) is not int or not 1 <= options["max_results"] <= 20):
        raise CoreError("invalid_connection", "Search limit must be between 1 and 20")
    if "allowed_tools" in options:
        names = options["allowed_tools"]
        if not isinstance(names, list) or not 1 <= len(names) <= 32 or any(not isinstance(n, str) or not 1 <= len(n) <= 128 for n in names):
            raise CoreError("invalid_connection", "MCP requires a bounded list of exact granted tool names")


def _remote_schema(value):
    # Remote declarations aren't trusted validator programs. Keep schema checking
    # bounded and reject regular expressions, reference resolution and recursive
    # or combinatorial validation rather than running them in the inference loop.
    pending, count = [(value, 0)], 0
    denied = {"pattern", "patternProperties", "$ref", "$dynamicRef", "allOf", "anyOf", "oneOf", "not", "if", "then", "else",
        "uniqueItems", "contains", "propertyNames", "dependentSchemas", "unevaluatedProperties", "unevaluatedItems", "contentSchema"}
    while pending:
        item, depth = pending.pop(); count += 1
        if count > 512 or depth > 16:
            raise CoreError("unsupported_schema", "Remote schema exceeds the bounded validation profile")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    schemas = [value]
    while schemas:
        item = schemas.pop()
        if not isinstance(item, dict): continue
        if set(item) & denied:
            raise CoreError("unsupported_schema", "Remote schema requires unsupported executable or combinatorial validation")
        for key in ("properties", "$defs", "definitions"):
            if isinstance(item.get(key), dict): schemas.extend(item[key].values())
        for key in ("items", "additionalProperties"):
            if key in item: schemas.append(item[key])
        if isinstance(item.get("prefixItems"), list): schemas.extend(item["prefixItems"])
    _check_schema(value)


class ConnectorSuite:
    def __init__(self, connections, *, browser_backend=None):
        self.connections, self.network, self.core = connections, connections.network, connections.core
        self.browser = browser_backend
        operation = lambda args, execute, read_only=False: ConnectionOperation(args, {"type": "object"}, execute,
            read_only=read_only, timeout=self.network.timeout, output_limit=1048576)
        connections.register_adapter("search", {"query": operation(schema({"query": text_schema(2048)}, ["query"]), self.search, True)},
            probe=self._probe_search, option_fields={"max_results"}, validate_options=_options)
        connections.register_adapter("mcp", {"call": operation(schema({"name": text_schema(128), "arguments": {"type": "object"}}, ["name", "arguments"]), self.mcp_call)},
            probe=self._probe_mcp, option_fields={"allowed_tools"}, validate_options=_options)
        for kind, op, args, execute in (
            ("stt", "transcribe", schema({"artifact_id": text_schema(256)}, ["artifact_id"]), self.transcribe),
            ("tts", "speak", schema({"text": text_schema(4096)}, ["text"]), self.speak),
            ("image", "generate", schema({"prompt": text_schema(4096)}, ["prompt"]), self.generate_image)):
            connections.register_adapter(kind, {op: operation(args, execute)}, probe=self._probe_media,
                option_fields={"model", "voice"}, validate_options=_options)
        connections.register_adapter("browser", {"inspect": operation(schema({"url": text_schema()}, []), self.inspect_browser, True)},
            probe=self._probe_browser)

    async def _secret(self, record):
        return await asyncio.to_thread(self.connections.vault.resolve, self.core.scope, record["credential_ref"]) if record["credential_ref"] else None

    def _headers(self, secret):
        return {"Authorization": "Bearer " + secret} if secret else {}

    async def _request(self, record, method, suffix="", *, secret=None, headers=None, content=None, params=None):
        return _remote(await self.network.request(method, record["endpoint"].rstrip("/") + suffix,
            allowed_origins=record["options"]["allowed_origins"], headers={**self._headers(secret), **(headers or {})},
            content=content, params=params))

    async def search(self, arguments, context, record, secret):
        limit = record["options"].get("max_results", 10)
        value = (await self._request(record, "GET", secret=secret, params={"q": arguments["query"], "limit": limit})).json()
        value = object_body(value, {"results"}, ["results"])
        if not isinstance(value["results"], list) or len(value["results"]) > limit:
            raise CoreError("invalid_remote_response", "Search returned an invalid result count")
        results = []
        for item in value["results"]:
            item = object_body(item, {"title", "url", "snippet"}, ["title", "url", "snippet"])
            if any(not isinstance(item[k], str) or len(item[k]) > bound for k, bound in (("title", 1024), ("url", 8192), ("snippet", 8192))):
                raise CoreError("invalid_remote_response", "Search result fields exceed their bounds")
            if not origin(item["url"]).startswith(("http:", "https:")):
                raise CoreError("invalid_remote_response", "Search result does not identify an HTTP source")
            results.append(item)
        return ExecutorResult({"query": arguments["query"], "results": results, "trust": "external_evidence"}, effects="none")

    async def _probe_search(self, record):
        result = await self.search({"query": "Sturgis readiness"}, None, record, await self._secret(record))
        return {"ok": True, "kind": "search_query", "result_count": len(result.data["results"])}

    @asynccontextmanager
    async def _mcp(self, record, secret):
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", "MCP-Protocol-Version": MCP_VERSION}
        async def rpc(method, params=None, *, notification=False):
            request_id = None if notification else new_id("rpc")
            body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
            if request_id: body["id"] = request_id
            response = await self._request(record, "POST", secret=secret, headers=headers, content=canonical_json(body).encode())
            if notification:
                if response.status not in {202, 204}:
                    raise CoreError("invalid_mcp", "MCP notification was not accepted")
                return None
            session = response.headers.get("mcp-session-id")
            if session:
                if not 1 <= len(session) <= 512 or any(not 33 <= ord(c) <= 126 for c in session):
                    raise CoreError("invalid_mcp", "MCP returned an invalid session identity")
                if "Mcp-Session-Id" in headers and headers["Mcp-Session-Id"] != session:
                    raise CoreError("invalid_mcp", "MCP changed session identity")
                headers["Mcp-Session-Id"] = session
            if response.headers.get("content-type", "").split(";")[0].strip() == "text/event-stream":
                messages = []
                for frame in response.body.decode("utf-8").replace("\r\n", "\n").split("\n\n"):
                    data = "\n".join(line[5:].lstrip(" ") for line in frame.splitlines() if line.startswith("data:"))
                    if data: messages.append(strict_loads(data))
                if len(messages) != 1:
                    raise CoreError("unsupported_mcp", "Only a single bounded MCP response stream is supported")
                value = messages[0]
            else:
                value = response.json()
            value = object_body(value, {"jsonrpc", "id", "result", "error"}, ["jsonrpc", "id"])
            if value["jsonrpc"] != "2.0" or value["id"] != request_id or ("result" in value) == ("error" in value):
                raise CoreError("invalid_mcp", "MCP response does not match the request")
            if "error" in value:
                raise CoreError("mcp_remote_error", "MCP server returned an error", effects="unknown")
            return value["result"]
        try:
            initialized = await rpc("initialize", {"protocolVersion": MCP_VERSION, "capabilities": {}, "clientInfo": {"name": "Sturgis", "version": "0.2.0"}})
            if (not isinstance(initialized, dict) or initialized.get("protocolVersion") != MCP_VERSION
                    or not isinstance(initialized.get("capabilities"), dict) or "tools" not in initialized["capabilities"]
                    or not isinstance(initialized.get("serverInfo"), dict)):
                raise CoreError("unsupported_mcp", "Server did not negotiate the supported MCP tools protocol")
            await rpc("notifications/initialized", notification=True)
            listing = await rpc("tools/list")
            if not isinstance(listing, dict) or not isinstance(listing.get("tools"), list) or len(listing["tools"]) > 128 or listing.get("nextCursor"):
                raise CoreError("unsupported_mcp", "MCP tool catalog must be a bounded complete page")
            tools = {}
            allowed = record["options"].get("allowed_tools", [])
            if not allowed:
                raise CoreError("invalid_grant", "MCP connection must explicitly select remote tools")
            for tool in listing["tools"]:
                if not isinstance(tool, dict) or not isinstance(tool.get("name"), str) or tool["name"] in tools or not isinstance(tool.get("inputSchema"), dict):
                    raise CoreError("invalid_mcp", "MCP returned an invalid tool declaration")
                _remote_schema(tool["inputSchema"])
                tools[tool["name"]] = tool["inputSchema"]
            if not set(allowed) <= set(tools):
                raise CoreError("mcp_tool_missing", "A granted remote tool is absent from the current catalog")
            catalog = {name: tools[name] for name in sorted(allowed)}
            yield rpc, catalog
        finally:
            # No automatic tool retries. Session cleanup is best-effort bookkeeping.
            if headers.get("Mcp-Session-Id"):
                try:
                    await self.network.request("DELETE", record["endpoint"], allowed_origins=record["options"]["allowed_origins"],
                        headers={**self._headers(secret), **headers}, timeout=min(1, self.network.timeout))
                except CoreError:
                    pass

    async def _probe_mcp(self, record):
        async with self._mcp(record, await self._secret(record)) as (_, catalog):
            return {"ok": True, "kind": "mcp_catalog", "protocol_version": MCP_VERSION,
                "catalog_sha256": hashlib.sha256(canonical_json(catalog).encode()).hexdigest(), "tools": list(catalog)}

    async def mcp_call(self, arguments, context, record, secret):
        if len(canonical_json(arguments).encode()) > 65536:
            raise CoreError("request_too_large", "MCP arguments exceed their validation byte bound", effects="none")
        async with self._mcp(record, secret) as (rpc, catalog):
            if hashlib.sha256(canonical_json(catalog).encode()).hexdigest() != record["probe"]["catalog_sha256"]:
                raise CoreError("stale_remote_schema", "MCP catalog changed after readiness was granted", effects="none")
            if arguments["name"] not in catalog:
                raise CoreError("mcp_tool_denied", "Remote tool is outside the connection grant", effects="none")
            validate_schema(catalog[arguments["name"]], arguments["arguments"], label="remote tool arguments")
            result = await rpc("tools/call", arguments)
            if not isinstance(result, dict) or not isinstance(result.get("content"), list) or type(result.get("isError", False)) is not bool:
                raise CoreError("invalid_mcp", "MCP tool returned an invalid result", effects="unknown")
            return ExecutorResult({"result": result, "trust": "external_evidence"},
                outcome="target_error" if result.get("isError") else "success", effects="unknown")

    async def _probe_media(self, record):
        response = await self._request(record, "GET", "/models", secret=await self._secret(record))
        value = response.json()
        if not isinstance(value, dict) or not isinstance(value.get("data"), list):
            raise CoreError("invalid_remote_response", "Provider did not return a model catalog")
        model = record["options"].get("model")
        if not model or not any(isinstance(item, dict) and item.get("id") == model for item in value["data"]):
            raise CoreError("model_unavailable", "Configured media model is absent from the provider catalog")
        return {"ok": True, "kind": "model_catalog", "model": model, "operation_verified": False}

    async def transcribe(self, arguments, context, record, secret):
        data = await asyncio.to_thread(self.core.artifacts.get, self.core.scope, arguments["artifact_id"])
        if not data or len(data) > min(self.network.max_bytes // 2, 4194304):
            raise CoreError("invalid_audio", "Audio artifact is empty or exceeds the configured bound", effects="none")
        boundary = new_id("multipart")
        content = (f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n{record["options"]["model"]}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\nContent-Type: application/octet-stream\r\n\r\n').encode() + data + f'\r\n--{boundary}--\r\n'.encode()
        value = (await self._request(record, "POST", "/audio/transcriptions", secret=secret,
            headers={"Content-Type": "multipart/form-data; boundary=" + boundary}, content=content)).json()
        if not isinstance(value, dict) or not isinstance(value.get("text"), str) or len(value["text"]) > 65536:
            raise CoreError("invalid_remote_response", "Transcription provider returned invalid text")
        return ExecutorResult({"text": value["text"], "source_artifact_id": arguments["artifact_id"], "trust": "external_evidence"}, effects="unknown")

    async def speak(self, arguments, context, record, secret):
        content = canonical_json({"model": record["options"]["model"], "input": arguments["text"],
            "voice": record["options"].get("voice", "alloy"), "response_format": "wav"}).encode()
        response = await self._request(record, "POST", "/audio/speech", secret=secret, headers={"Content-Type": "application/json"}, content=content)
        if not response.body.startswith(b"RIFF") or response.body[8:12] != b"WAVE":
            raise CoreError("invalid_remote_response", "Speech provider did not return WAV audio")
        artifact = await asyncio.to_thread(self.core.artifacts.put, self.core.scope, response.body, media_type="audio/wav")
        return ExecutorResult({"artifact_id": artifact.id, "media_type": artifact.media_type, "size": artifact.size}, effects="unknown")

    async def generate_image(self, arguments, context, record, secret):
        response = await self._request(record, "POST", "/images/generations", secret=secret, headers={"Content-Type": "application/json"},
            content=canonical_json({"model": record["options"]["model"], "prompt": arguments["prompt"], "n": 1, "response_format": "b64_json"}).encode())
        value = response.json()
        try:
            if not isinstance(value, dict) or not isinstance(value.get("data"), list) or len(value["data"]) != 1:
                raise ValueError()
            data = base64.b64decode(value["data"][0]["b64_json"], validate=True)
            if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) > self.network.max_bytes:
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise CoreError("invalid_remote_response", "Image provider did not return one bounded PNG image") from None
        artifact = await asyncio.to_thread(self.core.artifacts.put, self.core.scope, data, media_type="image/png")
        return ExecutorResult({"artifact_id": artifact.id, "media_type": artifact.media_type, "size": artifact.size}, effects="unknown")

    async def _probe_browser(self, record):
        if self.browser is None:
            raise CoreError("browser_unavailable", "An operator-managed isolated Chromium backend is required; uncontained remote CDP is unsupported")
        return await self.browser.probe()

    async def inspect_browser(self, arguments, context, record, secret):
        if self.browser is None:
            raise CoreError("browser_unavailable", "No contained Chromium backend is configured", effects="none")
        result = await self.browser.inspect(arguments.get("url", record["endpoint"]), network=self.network,
            allowed_origins=record["options"]["allowed_origins"], artifact_store=self.core.artifacts, scope=self.core.scope)
        return ExecutorResult(result, effects="none")


def install_connectors(connections, *, browser_backend=None):
    return ConnectorSuite(connections, browser_backend=browser_backend)
