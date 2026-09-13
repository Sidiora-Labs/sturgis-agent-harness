"""Consumer composition adapter; the independent gateway owns model infrastructure."""
from __future__ import annotations

import asyncio

from agent.protocol import CoreError, new_id
from consumer.contracts import identifier
from model_gateway.api import GatewayHTTP
from model_gateway.errors import GatewayError
from model_gateway.network import NetworkPolicy
from model_gateway.service import ModelGateway


class ConsumerGateway:
    """Map authenticated consumer identities to gateway tenants and agent clients."""

    def __init__(self, control):
        self.control = control
        self.service = ModelGateway(control.settings.root / "gateway",
            network_policy=NetworkPolicy(private_origins=control.settings.private_network_origins, timeout=120),
            allow_local_servers=control.settings.mode == "self_hosted")
        self.http = GatewayHTTP(self.service, endpoint=control.origin + "/model-gateway/v1")
        self._bindings = {}

    async def management(self, request):
        principal = await self.control.principal(request, mutate=request.method != "GET")
        try:
            return await self.http.management(request, principal.id)
        except GatewayError as error:
            raise CoreError(error.code, error.message) from None

    async def select(self, request):
        principal = await self.control.principal(request, mutate=True)
        agent_id = identifier(request.path_params["agent_id"])
        await asyncio.to_thread(self.control.agents.authorize, principal, agent_id, "manage")
        body = await self.control.body(request, {"model"}, {"model"})
        async with self._bindings.setdefault(agent_id, asyncio.Lock()):
            try:
                return await self._bind(principal.id, agent_id, body["model"])
            except GatewayError as error:
                raise CoreError(error.code, error.message) from None

    async def _bind(self, tenant, agent_id, model):
        service, store = self.service, self.service.store
        available = await asyncio.to_thread(service.models, tenant)
        selected = next((m for m in available if m["id"] == model), None)
        if selected is None:
            raise GatewayError("model_unavailable", "Choose a model from your gateway’s imported models.")
        capabilities = {field: selected[field] for field in ("context_limit", "output_limit", "tools")}
        if selected.get("tools_source") == "preset_default":
            capabilities["tools"] = True
        try:
            prior = await asyncio.to_thread(store.get, tenant, "client_binding", agent_id)
        except GatewayError as error:
            if error.code != "not_found":
                raise
            prior = None
        if prior and prior["state"] == "pending" and prior["model"] != model:
            raise GatewayError("binding_pending", "Retry the previous model selection to reconcile it before selecting a different model.", 409)
        reusable = prior and prior["model"] == model
        secret = await asyncio.to_thread(store.resolve, tenant, prior["secret_ref"]) if reusable else None
        if secret:
            try:
                await asyncio.to_thread(store.authenticate, secret)
            except GatewayError:
                reusable = False
        # An explicit retry after a completed selection must reach the worker;
        # replaying its old receipt would preserve a text-only probe fallback.
        # Pending requests retain their identity to reconcile uncertain IPC.
        if reusable and prior["state"] in {"failed", "active"}:
            binding = await asyncio.to_thread(store.put, tenant, "client_binding", agent_id,
                {**prior, "state": "pending", "request_id": new_id("gateway_bind"), "capabilities": capabilities}, prior["revision"])
        elif reusable:
            binding = prior
        else:
            key = await asyncio.to_thread(service.create_key, tenant, name="Sturgis agent " + agent_id, models=[model])
            binding = await asyncio.to_thread(store.put, tenant, "client_binding", agent_id,
                {"model": model, "key_id": key["id"], "previous_key_id": prior["key_id"] if prior else None,
                 "previous_key_ids": list(dict.fromkeys([prior["key_id"], *prior.get("previous_key_ids", []),
                     *([prior["previous_key_id"]] if prior.get("previous_key_id") else [])])) if prior else [],
                 "state": "pending", "request_id": new_id("gateway_bind"), "capabilities": capabilities}, prior["revision"] if prior else 0, secret=key["key"])
            secret = key["key"]
        # The worker sees only a gateway-restricted client key. Upstream secrets
        # never cross this boundary. A retained ID reconciles uncertain IPC results.
        try:
            result = await self.control.lifecycle.command(agent_id, "gateway.bind", {
                "endpoint": self.http.endpoint + "/chat/completions", "api_key": secret, "model": model,
                **binding.get("capabilities", capabilities)},
                request_id=binding["request_id"], timeout= min(300, self.service.network.timeout * 2 + 10))
        except CoreError as error:
            if error.details.get("worker_response") and error.code != "worker_indeterminate":
                await asyncio.to_thread(store.put, tenant, "client_binding", agent_id, {**binding, "state": "failed"}, binding["revision"])
            raise
        if binding["state"] != "active":
            previous = binding.get("previous_key_ids") or ([binding["previous_key_id"]] if binding.get("previous_key_id") else [])
            for key_id in previous:
                await asyncio.to_thread(store.revoke_key, tenant, key_id)
            await asyncio.to_thread(store.put, tenant, "client_binding", agent_id, {**binding, "state": "active"}, binding["revision"])
        return result
