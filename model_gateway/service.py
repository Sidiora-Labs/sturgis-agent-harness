"""Independent connection management, client authority and bounded model routing."""
from __future__ import annotations

import asyncio
import ipaddress
import secrets
import time
from urllib.parse import urlsplit

from model_gateway.catalog import normalize_endpoint, provider_preset, provider_presets
from model_gateway.errors import GatewayError
from model_gateway.network import NetworkPolicy, origin
from model_gateway.store import GatewayStore, text
from model_gateway.transports import GatewayTransport, discover_models
from model_gateway.wire import json_dumps


class ModelGateway:
    """Usable by any HTTP model client; contains no agent or conversation state."""

    def __init__(self, root, *, network_policy=None, allow_local_servers=True, max_concurrency=16):
        self.store = GatewayStore(root)
        self.network = network_policy or NetworkPolicy()
        self.transport = GatewayTransport(self.network)
        self.allow_local_servers = allow_local_servers
        if type(max_concurrency) is not int or not 1 <= max_concurrency <= 128:
            raise GatewayError("invalid_config", "Gateway concurrency must be between 1 and 128.")
        self._slots = asyncio.Semaphore(max_concurrency)
        self._cooldowns, self._rotations = {}, {}

    def presets(self):
        return provider_presets()

    def _preset(self, preset_id):
        preset = provider_preset(preset_id)
        if not preset:
            raise GatewayError("invalid_provider", "Choose a provider from the catalog or a custom connection.")
        return preset

    def _endpoint(self, preset, endpoint):
        endpoint = normalize_endpoint(endpoint or preset["endpoint"], preset["protocol"])
        host = urlsplit(endpoint).hostname
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == "localhost"
        if loopback and self.allow_local_servers:
            # Only the gateway gains this destination. Agent tool egress is untouched.
            self.network.private_origins = self.network.private_origins | {origin(endpoint)}
        return endpoint

    @staticmethod
    def _models(raw, preset):
        if not isinstance(raw, list) or len(raw) > 4096:
            raise GatewayError("invalid_catalog", "The provider returned an oversized model catalog.", 502)
        result, seen = [], set()
        defaults = preset["defaults"]
        for item in raw:
            if not isinstance(item, dict):
                raise GatewayError("invalid_catalog", "The provider returned an invalid model entry.", 502)
            id = text(item.get("id"), "model ID", 256)
            if id in seen:
                continue
            seen.add(id)
            context = item.get("context_limit", defaults["context_limit"])
            output = item.get("output_limit", defaults["output_limit"])
            if type(context) is not int or not 128 <= context <= 2_000_000:
                context = defaults["context_limit"]
            if type(output) is not int or not 1 <= output < context:
                output = min(defaults["output_limit"], context // 4)
            result.append({"id": id, "name": text(item.get("name") or id, "model name", 512),
                "context_limit": context, "output_limit": output,
                "tools": item.get("tools") if type(item.get("tools")) is bool else defaults["tools"],
                "protocol": item.get("protocol", preset["protocol"]),
                "limits_source": "provider_reported" if "context_limit" in item else "preset_default"})
        return result

    @staticmethod
    def _key(api_key):
        if api_key is not None and (not isinstance(api_key, str) or len(api_key) > 8192 or any(ord(c) < 32 or ord(c) > 126 for c in api_key)):
            raise GatewayError("invalid_key", "Paste a valid API key without line breaks.")
        return api_key.strip() if api_key else None

    async def discover(self, tenant, *, preset_id, endpoint=None, api_key=None, connection_id=None):
        preset = self._preset(preset_id)
        secret = self._key(api_key)
        if connection_id:
            current = await asyncio.to_thread(self.store.get, tenant, "connection", connection_id)
            endpoint = endpoint or current["endpoint"]
            candidate = normalize_endpoint(endpoint, preset["protocol"])
            if not secret and current.get("secret_ref"):
                if (current["preset_id"], current["endpoint"]) != (preset_id, candidate):
                    raise GatewayError("key_required", "Enter the API key again when changing the provider or endpoint.")
                secret = await asyncio.to_thread(self.store.resolve, tenant, current.get("secret_ref"))
        endpoint = self._endpoint(preset, endpoint)
        if preset["auth"] == "api_key" and not secret:
            raise GatewayError("key_required", "Paste this provider’s API key, then load its models.")
        models = await discover_models(preset["protocol"], endpoint, secret, self.network, preset_id=preset_id)
        return {"models": self._models(models, preset), "source": "live"}

    async def connect(self, tenant, *, preset_id, name, models, endpoint=None, api_key=None,
                      selected_model=None, connection_id=None, expected_revision=0):
        preset, name = self._preset(preset_id), text(name, "provider name", 120)
        self.store._revision(expected_revision)
        endpoint = self._endpoint(preset, endpoint)
        if not isinstance(models, list) or not 1 <= len(models) <= 256 or len(set(map(str, models))) != len(models):
            raise GatewayError("model_required", "Choose between 1 and 256 models to import.")
        for item in models:
            text(item, "model ID", 256)
        prior = await asyncio.to_thread(self.store.get, tenant, "connection", connection_id) if connection_id else None
        if prior and prior["revision"] != expected_revision:
            raise GatewayError("conflict", "This connection changed. Refresh it and try again.", 409)
        discovered = await self.discover(tenant, preset_id=preset_id, endpoint=endpoint, api_key=api_key, connection_id=connection_id)
        catalog = {m["id"]: m for m in discovered["models"]}
        if any(id not in catalog for id in models):
            raise GatewayError("model_unavailable", "A selected model is no longer advertised. Load the models again.")
        if selected_model and selected_model not in models:
            raise GatewayError("invalid_model", "Select a model from the imported models.")
        body = {"name": name, "preset_id": preset_id, "endpoint": endpoint, "protocol": preset["protocol"],
            "models": [catalog[id] for id in models], "selected_model": selected_model or models[0],
            "secret_ref": prior.get("secret_ref") if prior else None, "status": "connected", "error": None,
            "discovered_at": time.time()}
        secret = self._key(api_key)
        row = await asyncio.to_thread(self.store.put, tenant, "connection", connection_id or "conn_" + secrets.token_hex(8),
            body, expected_revision, secret=secret if secret else None)
        return self.store.public_connection(row)

    def connections(self, tenant):
        return [self.store.public_connection(row) for row in self.store.list(tenant, "connection")]

    async def refresh(self, tenant, connection_id, expected_revision):
        row = await asyncio.to_thread(self.store.get, tenant, "connection", connection_id)
        if row["revision"] != expected_revision:
            raise GatewayError("conflict", "This connection changed. Refresh the page first.", 409)
        result = await self.discover(tenant, preset_id=row["preset_id"], endpoint=row["endpoint"], connection_id=connection_id)
        # Retain the owner's import choices, remove withdrawn models, advertise newly
        # discovered models separately so refresh never silently changes a route.
        imported = {m["id"] for m in row["models"]}
        models = [m for m in result["models"] if m["id"] in imported]
        body = {**row, "models": models, "discovered_at": time.time(), "status": "connected" if models else "error",
                "error": None if models else {"code": "models_withdrawn", "message": "The provider no longer advertises your imported models. Edit this connection to import others."}}
        saved = await asyncio.to_thread(self.store.put, tenant, "connection", connection_id, body, expected_revision)
        return self.store.public_connection(saved)

    def models(self, tenant, *, include_routes=True):
        models = [{**model, "id": row["id"] + "/" + model["id"], "connection_id": row["id"], "provider_name": row["name"]}
                  for row in self.store.list(tenant, "connection") for model in row["models"]]
        if include_routes:
            by_id = {m["id"]: m for m in models}
            for route in self.store.list(tenant, "route"):
                targets = [by_id[id] for id in route["models"] if id in by_id]
                if len(targets) == len(route["models"]) and targets:
                    models.append({"id": route["id"], "name": route["name"], "connection_id": None, "provider_name": "Gateway route",
                        "context_limit": min(m["context_limit"] for m in targets), "output_limit": min(m["output_limit"] for m in targets),
                        "tools": all(m["tools"] for m in targets)})
        return models

    def save_route(self, tenant, *, id, name, models, strategy="priority", expected_revision=0):
        id = text(id, "route name", 128)
        if "/" in id or id.startswith("conn_") or strategy not in {"priority", "round_robin"}:
            raise GatewayError("invalid_route", "Use a route name without slashes and a supported routing strategy.")
        available = {m["id"] for m in self.models(tenant, include_routes=False)}
        if not isinstance(models, list) or not 1 <= len(models) <= 16 or any(not isinstance(m, str) or m not in available for m in models) or len(set(models)) != len(models):
            raise GatewayError("invalid_route", "Choose 1 to 16 distinct imported models for this route.")
        return self.store.put(tenant, "route", id, {"name": text(name, "route label", 120), "models": models, "strategy": strategy}, expected_revision)

    def create_key(self, tenant, *, name, models=()):
        available = {m["id"] for m in self.models(tenant)}
        if not isinstance(models, (list, tuple)) or any(not isinstance(m, str) or m not in available for m in models):
            raise GatewayError("invalid_model", "Choose imported models or named routes for this key.")
        return self.store.create_key(tenant, name, models)

    def _targets(self, principal, model):
        tenant = principal["tenant"]
        if principal["models"] and model not in principal["models"]:
            raise GatewayError("model_denied", "This gateway key cannot use the requested model.", 403)
        if "/" in model:
            ids = [model]
        else:
            route = self.store.get(tenant, "route", model)
            ids = route["models"][:]
            if route["strategy"] == "round_robin":
                key = (tenant, model)
                offset = self._rotations.get(key, 0) % len(ids)
                self._rotations[key] = offset + 1
                ids = ids[offset:] + ids[:offset]
        result = []
        for alias in ids:
            connection_id, model_id = alias.split("/", 1)
            try:
                row = self.store.get(tenant, "connection", connection_id)
                capability = next(m for m in row["models"] if m["id"] == model_id)
            except (GatewayError, StopIteration):
                continue
            result.append((alias, row, capability))
        if not result:
            raise GatewayError("model_unavailable", "This model is unavailable. Refresh the gateway model list.", 404)
        return result

    @staticmethod
    def _payload(payload):
        if not isinstance(payload, dict) or len(json_dumps(payload).encode()) > 4 * 1024 * 1024:
            raise GatewayError("invalid_request", "Use a bounded JSON chat request.")
        text(payload.get("model"), "model name", 512)
        messages = payload.get("messages")
        if not isinstance(messages, list) or not 1 <= len(messages) <= 2048 or any(not isinstance(m, dict) or m.get("role") not in {"system", "developer", "user", "assistant", "tool"} for m in messages):
            raise GatewayError("invalid_request", "Provide a conversation using supported message roles.")
        if "stream" in payload and type(payload["stream"]) is not bool:
            raise GatewayError("invalid_request", "The stream field must be true or false.")
        for key in ("max_tokens", "max_completion_tokens"):
            if key in payload and (type(payload[key]) is not int or not 1 <= payload[key] <= 262144):
                raise GatewayError("invalid_request", "The output token limit must be a positive bounded integer.")
        if payload.get("n", 1) != 1:
            raise GatewayError("unsupported_parameter", "The gateway supports one completion per request.")
        if "tools" in payload and (not isinstance(payload["tools"], list) or len(payload["tools"]) > 128):
            raise GatewayError("invalid_tools", "Provide at most 128 tool definitions.")

    async def stream(self, key, payload, *, cancel=None):
        """Authenticate every request; fallback ends permanently at the first chunk."""
        self._payload(payload)
        principal = await asyncio.to_thread(self.store.authenticate, key)
        targets = await asyncio.to_thread(self._targets, principal, payload["model"])
        try:
            await asyncio.wait_for(self._slots.acquire(), timeout=10)
        except TimeoutError:
            raise GatewayError("busy", "The gateway is busy. Try again shortly.", 429, retryable=True) from None
        try:
            last = None
            for alias, row, capability in targets:
                if cancel is not None and cancel.is_set():
                    raise GatewayError("cancelled", "The model request was cancelled.", 499)
                cooldown_key = (principal["tenant"], row["id"], capability["id"])
                if self._cooldowns.get(cooldown_key, 0) > time.monotonic():
                    last = GatewayError("cooldown", "This provider is temporarily cooling down. Try another route or wait a moment.", 429, retryable=True)
                    continue
                # Revocation and credential rotation are checked again at dispatch.
                await asyncio.to_thread(self.store.authenticate, key)
                current = await asyncio.to_thread(self.store.get, principal["tenant"], "connection", row["id"])
                if current["revision"] != row["revision"]:
                    raise GatewayError("conflict", "The provider connection changed before dispatch. Retry your request.", 409)
                secret = await asyncio.to_thread(self.store.resolve, principal["tenant"], row.get("secret_ref"))
                outgoing = {**payload, "model": capability["id"], "stream": True}
                if "max_tokens" not in outgoing and "max_completion_tokens" not in outgoing:
                    outgoing["max_tokens"] = capability["output_limit"]
                else:
                    for parameter in ("max_tokens", "max_completion_tokens"):
                        if parameter in outgoing and outgoing[parameter] > capability["output_limit"]:
                            raise GatewayError("output_limit", "The requested output exceeds this imported model’s limit.")
                status, usage, emitted = "failed", None, False
                try:
                    async for chunk in self.transport.stream(capability.get("protocol", row["protocol"]), row["endpoint"], outgoing,
                            secret, preset_id=row["preset_id"], cancel=cancel):
                        if chunk.get("usage"):
                            usage = chunk["usage"]
                        emitted = True
                        yield {**chunk, "model": payload["model"]}
                    status = "completed"
                    self._cooldowns.pop(cooldown_key, None)
                    return
                except GatewayError as error:
                    last = error
                    if error.retryable:
                        self._cooldowns[cooldown_key] = time.monotonic() + 30
                    if emitted or not error.retryable:
                        raise
                except (asyncio.CancelledError, GeneratorExit):
                    status = "cancelled"
                    raise
                finally:
                    await asyncio.shield(asyncio.to_thread(self.store.record_usage, principal["tenant"], principal["id"],
                        alias, row["id"], status, usage))
            raise last or GatewayError("provider_unavailable", "No route destination is currently available.", 503, retryable=True)
        finally:
            self._slots.release()

    async def complete(self, key, payload):
        """Aggregate the identical streaming path for standard nonstreaming clients."""
        content, refusal, tool_calls, usage = [], [], {}, None
        completion_id, finish_reason, created = "chatcmpl_" + secrets.token_hex(12), None, int(time.time())
        async for chunk in self.stream(key, payload):
            completion_id = chunk.get("id", completion_id)
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices", []):
                delta = choice.get("delta", {})
                if delta.get("content"):
                    content.append(delta["content"])
                if delta.get("refusal"):
                    refusal.append(delta["refusal"])
                for part in delta.get("tool_calls", []):
                    item = tool_calls.setdefault(part["index"], {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    item["id"] += part.get("id", "")
                    for field in ("name", "arguments"):
                        item["function"][field] += part.get("function", {}).get(field, "")
                finish_reason = choice.get("finish_reason") or finish_reason
        message = {"role": "assistant", "content": "".join(content) or None}
        if refusal:
            message["refusal"] = "".join(refusal)
        if tool_calls:
            message["tool_calls"] = list(tool_calls.values())
        result = {"id": completion_id, "object": "chat.completion", "created": created, "model": payload["model"],
                  "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}]}
        if usage is not None:
            result["usage"] = usage
        return result
