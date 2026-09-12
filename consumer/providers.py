"""Revision-bound provider readiness and privileged, policy-pinned model I/O."""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from agent.protocol import CoreError, ToolCall, canonical_json, new_id
from agent.provider_adapters import HTTPProvider
from consumer.contracts import identifier, public_record
from consumer.network import NetworkPolicy, origin

CONFIGS = "consumer.providers"
PROBES = "consumer.provider_probes"
SELECTION = "consumer.provider_selection"
PROBE_TTL = 300


class _PolicyClient:
    def __init__(self, policy, endpoint, timeout):
        self.policy, self.allowed, self.timeout = policy, (origin(endpoint),), timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    @asynccontextmanager
    async def stream(self, method, url, *, json, headers):
        async with self.policy.stream(method, url, allowed_origins=self.allowed,
                headers={**headers, "Content-Type": "application/json"},
                content=canonical_json(json).encode(), timeout=self.timeout) as response:
            yield _Response(response)


class _Response:
    def __init__(self, response):
        self.status_code, self._response = response.status, response

    def aiter_bytes(self):
        return self._response.aiter_bytes()


class ProviderSettings:
    def __init__(self, core, vault, network_policy=None):
        self.core, self.vault = core, vault
        self.journal, self.scope = core.journal, core.scope
        self.network_policy = network_policy or NetworkPolicy()
        self._refresh_lock = asyncio.Lock()

    def _config(self, provider_id):
        identifier(provider_id)
        row = self.journal.get(CONFIGS, provider_id, self.scope)
        if row is None:
            raise CoreError("not_found", "Provider configuration is unavailable")
        return row

    @staticmethod
    def _expected(row, expected_revision):
        if type(expected_revision) is not int or row.revision != expected_revision:
            raise CoreError("conflict", "Provider configuration revision changed", current_revision=row.revision)

    def _credential_current(self, row):
        reference = row.value["credential_ref"]
        return reference is None or self.vault.current(self.scope, reference)

    def configure(self, provider_id, *, endpoint, model, context_limit, output_limit,
                  tools=False, credential_ref=None, expected_revision=0):
        identifier(provider_id)
        origin(endpoint)
        url = urlsplit(endpoint)
        # Credentials in query strings cannot be safely projected or audited.
        if (url.scheme not in {"http", "https"} or url.query
                or not isinstance(model, str) or not model or len(model) > 256 or any(ord(c) < 32 for c in model)
                or type(context_limit) is not int or type(output_limit) is not int
                or not 0 < output_limit < context_limit <= 2_000_000 or type(tools) is not bool
                or type(expected_revision) is not int or expected_revision < 0):
            raise CoreError("invalid_provider", "Provider endpoint, model and limits must be explicit and bounded")
        if credential_ref is not None and not self.vault.current(self.scope, credential_ref):
            raise CoreError("credential_unavailable", "Credential reference is unavailable or has changed")
        body = {"endpoint": endpoint, "model": model, "context_limit": context_limit,
                "output_limit": output_limit, "tools": tools, "credential_ref": credential_ref,
                "limits_source": "user_declared", "authentication": "bearer" if credential_ref else "none"}
        with self.journal.transaction():
            row = self.journal.put(CONFIGS, provider_id, self.scope, body, expected_revision)
            static = self.journal.get("providers", provider_id, self.scope)
            self.journal.put("providers", provider_id, self.scope, {"endpoint": endpoint,
                "transport": "chat_sse", "authentication": body["authentication"]}, static.revision if static else 0)
            capability = self.journal.get("provider_capabilities", provider_id, self.scope)
            if capability:
                self.journal.delete("provider_capabilities", provider_id, self.scope, capability.revision)
            self.journal.append(self.scope, "provider.configured", {"provider_id": row.id,
                "config_revision": row.revision}, source="consumer.providers")
        return self.inspect(provider_id)

    def _ready(self, row):
        probe = self.journal.get(PROBES, row.id, self.scope)
        if (probe is None or probe.value["config_revision"] != row.revision
                or probe.value["status"] != "ready" or probe.value["expires_at"] <= time.time()
                or not self._credential_current(row)):
            raise CoreError("provider_unavailable", "Provider requires a successful probe of its current configuration")
        return probe

    def inspect(self, provider_id=None):
        if provider_id is None:
            return [self.inspect(row.id) for row in self.journal.list(CONFIGS, self.scope)]
        row = self._config(provider_id)
        probe = self.journal.get(PROBES, row.id, self.scope)
        selected = self.journal.get(SELECTION, "active", self.scope)
        try:
            self._ready(row)
            ready = True
        except CoreError:
            ready = False
        return {**public_record(row), "ready": ready,
                "active": bool(selected and selected.value == {"provider_id": row.id, "config_revision": row.revision}),
                "credential_current": self._credential_current(row),
                "probe": public_record(probe) if probe else None}

    def _adapter(self, endpoint):
        return HTTPProvider(timeout=min(30, self.network_policy.timeout), client_factory=lambda **kwargs:
            _PolicyClient(self.network_policy, endpoint, kwargs["timeout"]))

    def _probe_begin(self, provider_id, expected_revision):
        with self.journal.transaction():
            row = self._config(provider_id)
            self._expected(row, expected_revision)
            if not self._credential_current(row):
                raise CoreError("credential_unavailable", "Credential reference is unavailable or has changed")
            work = self.core.scheduler.submit(self.scope, "provider_probe", {"provider_id": row.id,
                "config_revision": row.revision}, resources={"tokens": 4096})
            work = self.core.scheduler.admit(self.scope, work.id, worker="consumer.provider_probe", eligible_ids=[work.id])
            if work is None:
                # The transaction rolls back the queued probe when capacity is unavailable.
                raise CoreError("busy", "Provider probe capacity is unavailable")
            claim_id = new_id("probe")
            self.core.scheduler.authorize(self.scope, {"work_id": work.id,
                "generation": work.value["generation"], "claim_id": claim_id,
                "required_capabilities": [], "resources": {"tokens": 4096}},
                ToolCall("model.invoke", {"provider_id": row.id, "probe": True}))
            self.journal.append(self.scope, "provider.probe_started", {"provider_id": row.id,
                "config_revision": row.revision, "work_id": work.id, "claim_id": claim_id}, source="consumer.providers")
        return row, work, claim_id

    def _probe_finish(self, row, work, claim_id, *, status, error, tokens):
        # Factual consumption survives concurrent config rotation and probe rejection.
        if tokens is not None:
            self.core.scheduler.record_usage(self.scope, work.id, claim_id + ":tokens", {"tokens": tokens}, claim_id=claim_id)
        current_work = self.core.scheduler.get(self.scope, work.id)
        if current_work.value["state"] == "running":
            self.core.scheduler.finish(self.scope, work.id, current_work.value["generation"], state="completed" if status == "ready" else "failed")
        if current_work.value["budget_overrun"]:
            status, error = "failed", {"code": "budget_exceeded", "message": "Probe exceeded its token allocation"}
        self.journal.append(self.scope, "provider.probe_finished", {"provider_id": row.id,
            "config_revision": row.revision, "work_id": work.id, "claim_id": claim_id,
            "status": status, "error": error, "usage_reported": tokens is not None}, source="consumer.providers")
        with self.journal.transaction():
            current = self._config(row.id)
            self._expected(current, row.revision)
            if not self._credential_current(current):
                raise CoreError("credential_unavailable", "Credential changed during provider probe")
            prior = self.journal.get(PROBES, row.id, self.scope)
            self.journal.put(PROBES, row.id, self.scope, {"config_revision": row.revision,
                "credential_ref": row.value["credential_ref"], "status": status,
                "observed_at": time.time(), "expires_at": time.time() + PROBE_TTL,
                "work_id": work.id, "claim_id": claim_id, "usage_reported": tokens is not None,
                "error": error}, prior.revision if prior else 0)
        return self.inspect(row.id)

    async def probe(self, provider_id, expected_revision):
        row, work, claim_id = await asyncio.to_thread(self._probe_begin, provider_id, expected_revision)
        descriptor = {**row.value, "output_limit": min(row.value["output_limit"], 128)}
        tokens, terminal, proposal, error = None, None, False, None
        try:
            credential = await asyncio.to_thread(self.vault.resolve, self.scope, row.value["credential_ref"]) if row.value["credential_ref"] else None
            call = ToolCall("consumer.probe", {}).as_dict()
            messages = [{"role": "user", "content": "Reply with OK." if not row.value["tools"] else
                "Call invoke exactly once with these arguments: " + canonical_json(call)}]
            tools = [{"type": "function", "function": {"name": "invoke", "description": "Validate tool transport without executing an action", "parameters": {"type": "object"}}}] if row.value["tools"] else None
            async for event in self._adapter(row.value["endpoint"]).stream(descriptor, messages, tools=tools, credential=credential):
                if event.kind == "usage":
                    tokens = event.data["total_tokens"]
                elif event.kind == "terminal":
                    terminal = event.data["status"]
                elif event.kind == "tool":
                    proposal = event.data["call"] == call
            if terminal != "completed" or row.value["tools"] and not proposal:
                raise CoreError("probe_incompatible", "Provider did not demonstrate the configured response capability")
        except asyncio.CancelledError:
            await asyncio.shield(asyncio.to_thread(self._probe_finish, row, work, claim_id,
                status="failed", error={"code": "cancelled", "message": "Provider probe was cancelled"}, tokens=tokens))
            raise
        except CoreError as exc:
            error = {"code": exc.code, "message": "Provider probe failed"}
        except Exception:
            error = {"code": "provider_error", "message": "Provider probe failed"}
        return await asyncio.to_thread(self._probe_finish, row, work, claim_id,
            status="failed" if error else "ready", error=error, tokens=tokens)

    def activate(self, provider_id, expected_revision):
        with self.journal.transaction():
            row = self._config(provider_id)
            self._expected(row, expected_revision)
            probe = self._ready(row)
            self.core.catalog.observe(self.scope, row.id, model=row.value["model"],
                context_limit=row.value["context_limit"], output_limit=row.value["output_limit"],
                tools=row.value["tools"], ttl=max(.001, probe.value["expires_at"] - time.time()))
            selection = self.journal.get(SELECTION, "active", self.scope)
            self.journal.put(SELECTION, "active", self.scope, {"provider_id": row.id,
                "config_revision": row.revision}, selection.revision if selection else 0)
            self.core.provider_id = row.id
        return self.inspect(row.id)

    def install(self):
        self.core.provider, self.core.credential = self, None
        selected = self.journal.get(SELECTION, "active", self.scope)
        if selected:
            self.core.provider_id = selected.value["provider_id"]

    def _selected(self):
        selected = self.journal.get(SELECTION, "active", self.scope)
        if selected is None:
            raise CoreError("provider_unavailable", "Select a successfully probed provider first")
        row = self._config(selected.value["provider_id"])
        self._expected(row, selected.value["config_revision"])
        if not self._credential_current(row):
            raise CoreError("credential_unavailable", "Selected provider credential has changed")
        return row

    async def ensure_selected_ready(self):
        """Refresh unchanged selection before model work consumes a scheduler slot."""
        async with self._refresh_lock:
            row = await asyncio.to_thread(self._selected)
            inspected = await asyncio.to_thread(self.inspect, row.id)
            if not inspected["ready"]:
                await self.probe(row.id, row.revision)
                # Concurrent selection/config changes are authority changes, not refresh permission.
                return await asyncio.to_thread(self._refresh_activate, row)
            return inspected

    def _refresh_activate(self, row):
        with self.journal.transaction():
            current = self._selected()
            if (current.id, current.revision) != (row.id, row.revision):
                raise CoreError("conflict", "Selected provider changed during readiness refresh")
            return self.activate(row.id, row.revision)

    def _dispatch(self, descriptor):
        with self.journal.transaction():
            row = self._config(descriptor["id"])
            self._ready(row)
            selection = self.journal.get(SELECTION, "active", self.scope)
            static = self.journal.get("providers", row.id, self.scope)
            if (selection is None or selection.value != {"provider_id": row.id, "config_revision": row.revision}
                    or descriptor.get("static_revision") != static.revision
                    or any(descriptor.get(key) != row.value[key] for key in
                           ("endpoint", "model", "context_limit", "output_limit", "tools", "authentication"))):
                raise CoreError("provider_unavailable", "Provider configuration changed before dispatch")
            credential = self.vault.resolve(self.scope, row.value["credential_ref"]) if row.value["credential_ref"] else None
            return credential

    async def stream(self, descriptor, messages, *, tools=None, credential=None, cancel=None):
        secret = await asyncio.to_thread(self._dispatch, descriptor)
        async for event in self._adapter(descriptor["endpoint"]).stream(descriptor, messages,
                tools=tools, credential=secret, cancel=cancel):
            yield event
