"""Standalone HTTP management and OpenAI-compatible inference boundaries."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from model_gateway.errors import GatewayError
from model_gateway.network import exact_origin
from model_gateway.service import ModelGateway
from model_gateway.wire import json_dumps, json_loads

VERSION = "gateway/1"


async def body(request, allowed=None, required=()):
    """Bound ingress before parsing and reject unknown control-plane fields."""
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
        raise GatewayError("invalid_request", "Use application/json for this request.")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > 4 * 1024 * 1024:
            raise GatewayError("body_too_large", "The request exceeds the 4 MiB limit.", 413)
        chunks.append(chunk)
    value = json_loads(b"".join(chunks))
    if not isinstance(value, dict) or allowed is not None and set(value) - set(allowed) or set(required) - set(value):
        raise GatewayError("invalid_request", "Check the required fields and remove unknown fields.")
    return value


def bearer(request, *, native=False):
    values = request.headers.getlist("authorization")
    native_keys = request.headers.getlist("x-api-key") if native else []
    if native_keys:
        if len(native_keys) != 1 or values:
            raise GatewayError("unauthenticated", "Send exactly one gateway API key.", 401)
        return native_keys[0]
    if len(values) != 1 or not values[0].startswith("Bearer "):
        raise GatewayError("unauthenticated", "Use a gateway API key in the Authorization header.", 401)
    return values[0][7:]


async def while_connected(request, operation):
    """Own work until its result or disconnect, after the full body is consumed.

    StreamingResponse takes over disconnect monitoring after the first event.
    Cancelling and joining here releases upstream sockets before returning.
    """
    async def disconnected():
        while True:
            if (await request.receive())["type"] == "http.disconnect":
                return
    work = asyncio.ensure_future(operation)
    watcher = asyncio.create_task(disconnected())
    try:
        done, _ = await asyncio.wait({work, watcher}, return_when=asyncio.FIRST_COMPLETED)
        if watcher in done:
            raise GatewayError("client_disconnected", "The client disconnected.", 499)
        return await work
    finally:
        for task in (work, watcher):
            if not task.done():
                task.cancel()
        await asyncio.gather(work, watcher, return_exceptions=True)


class GatewayHTTP:
    """HTTP adapter accepts an injected management authorizer; inference keys stay local."""

    def __init__(self, gateway, *, endpoint, authorize=None):
        self.gateway, self.endpoint, self.authorize = gateway, endpoint, authorize

    async def management(self, request, tenant=None):
        if tenant is None:
            if self.authorize is None:
                raise GatewayError("unauthenticated", "Gateway management authentication is required.", 401)
            tenant = await self.authorize(request, mutate=request.method != "GET")
        resource = request.path_params.get("resource", "").strip("/")
        method = request.method
        gateway, store = self.gateway, self.gateway.store
        if method == "GET":
            reads = {"presets": gateway.presets, "connections": lambda: gateway.connections(tenant),
                     "models": lambda: gateway.models(tenant), "routes": lambda: store.list(tenant, "route"),
                     "keys": lambda: store.keys(tenant), "usage": lambda: store.usage(tenant),
                     "status": lambda: {"endpoint": self.endpoint, "connections": len(gateway.connections(tenant)),
                                        "models": len(gateway.models(tenant)), "version": VERSION}}
            reads["auth/providers"] = gateway.auth.providers
            reads["auth/accounts"] = lambda: gateway.auth.accounts(tenant)
            reads["settings"] = lambda: gateway.controls.settings(tenant)
            reads["export"] = lambda: gateway.controls.export(tenant)
            if resource in reads:
                return await asyncio.to_thread(reads[resource])
        if resource == "settings" and method == "POST":
            return await asyncio.to_thread(gateway.controls.save_settings, tenant, **await body(request,
                {"expected_revision", "request_timeout", "max_concurrency", "requests_per_minute"},
                {"expected_revision", "request_timeout", "max_concurrency", "requests_per_minute"}))
        if resource == "import" and method == "POST":
            return await asyncio.to_thread(gateway.controls.import_configuration, tenant, (await body(request, {"archive"}, {"archive"}))["archive"])
        if resource == "discover" and method == "POST":
            return await gateway.discover(tenant, **await body(request, {"preset_id", "endpoint", "api_key", "connection_id", "auth_account_id"}, {"preset_id"}))
        fields = {"preset_id", "name", "models", "endpoint", "api_key", "selected_model", "expected_revision", "auth_account_id", "catalog_mode"}
        if resource == "auth/start" and method == "POST":
            session = await gateway.auth.start(tenant, **await body(request, {"provider_id", "redirect_uri", "name", "options"}, {"provider_id"}))
            return await gateway.callbacks.watch(tenant, session)
        if resource.startswith("auth/"):
            parts = resource.split("/")
            if len(parts) == 3 and parts[1] == "accounts" and method == "DELETE":
                values = await body(request, {"expected_revision"}, {"expected_revision"})
                return await asyncio.to_thread(gateway.auth.disconnect, tenant, parts[2], values["expected_revision"])
            if len(parts) == 3 and parts[2] == "poll" and method == "POST":
                await body(request, set())
                session = await gateway.auth.poll(tenant, parts[1])
                return await gateway.callbacks.watch(tenant, session)
            if len(parts) == 3 and parts[2] == "complete" and method == "POST":
                result = await gateway.auth.complete(tenant, parts[1], **await body(request, {"state", "code"}, {"state", "code"}))
                await gateway.callbacks.forget(parts[1])
                return result
            if len(parts) == 2 and method == "DELETE":
                result = await asyncio.to_thread(gateway.auth.cancel, tenant, parts[1])
                await gateway.callbacks.forget(parts[1])
                return result
        if resource == "connections" and method == "POST":
            return await gateway.connect(tenant, **await body(request, fields, {"preset_id", "name", "models"}))
        if resource.startswith("connections/"):
            parts = resource.split("/")
            if len(parts) == 3 and parts[2] == "refresh" and method == "POST":
                value = await body(request, {"expected_revision"}, {"expected_revision"})
                return await gateway.refresh(tenant, parts[1], value["expected_revision"])
            if len(parts) == 2 and method == "PATCH":
                return await gateway.connect(tenant, connection_id=parts[1], **await body(request, fields, {"preset_id", "name", "models", "expected_revision"}))
            if len(parts) == 2 and method == "DELETE":
                value = await body(request, {"expected_revision"}, {"expected_revision"})
                return await asyncio.to_thread(store.delete_connection, tenant, parts[1], value["expected_revision"])
        if resource == "routes" and method == "POST":
            return await asyncio.to_thread(gateway.save_route, tenant, **await body(request, {"id", "name", "models", "strategy", "expected_revision"}, {"id", "name", "models"}))
        if resource.startswith("routes/") and len(resource.split("/")) == 2 and method == "DELETE":
            values = await body(request, {"expected_revision"}, {"expected_revision"})
            return await asyncio.to_thread(store.delete_route, tenant, resource.split("/")[1], values["expected_revision"])
        if resource == "keys" and method == "POST":
            return await asyncio.to_thread(gateway.create_key, tenant, **await body(request, {"name", "models"}, {"name"}))
        if resource.startswith("keys/") and len(resource.split("/")) == 2 and method == "DELETE":
            return await asyncio.to_thread(store.revoke_key, tenant, resource.split("/")[1])
        raise GatewayError("not_found", "This gateway operation is unavailable.", 404)

    async def models(self, request):
        principal = await asyncio.to_thread(self.gateway.store.authenticate, bearer(request))
        models = await asyncio.to_thread(self.gateway.models, principal["tenant"])
        return JSONResponse({"object": "list", "data": [{"id": m["id"], "object": "model", "created": 0,
            "owned_by": m["provider_name"], "context_length": m["context_limit"], "max_output_tokens": m["output_limit"],
            "supported_parameters": ["tools"] if m["tools"] else []} for m in models if not principal["models"] or m["id"] in principal["models"]]})

    async def chat(self, request):
        key, payload = bearer(request), await body(request)
        if not payload.get("stream", False):
            return JSONResponse(await while_connected(request, self.gateway.complete(key, payload)))
        iterator = self.gateway.stream(key, payload).__aiter__()
        # Fail initial authorization/routing/upstream errors as real HTTP errors.
        try:
            first = await while_connected(request, anext(iterator))
        except StopAsyncIteration:
            raise GatewayError("provider_incomplete", "The provider returned no completion.", 502) from None
        except BaseException:
            await iterator.aclose()
            raise

        async def events():
            try:
                yield "data: " + json_dumps(first) + "\n\n"
                async for chunk in iterator:
                    yield "data: " + json_dumps(chunk) + "\n\n"
                yield "data: [DONE]\n\n"
            except GatewayError as error:
                yield "data: " + json_dumps({"error": error.as_dict()}) + "\n\n"
            finally:
                await iterator.aclose()
        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    def inference_routes(self, prefix="/v1"):
        async def wrap(request, handler):
            try:
                return await handler(request)
            except GatewayError as error:
                return JSONResponse({"error": {**error.as_dict(), "type": "gateway_error"}}, status_code=error.status)
            except Exception:
                return JSONResponse({"error": {"code": "internal_error", "message": "The gateway could not complete this request."}}, status_code=500)
        async def models(request):
            return await wrap(request, self.models)
        async def chat(request):
            return await wrap(request, self.chat)
        async def native(request):
            return await wrap(request, self.native)
        return [Route(prefix + "/models", models, methods=["GET"]), Route(prefix + "/chat/completions", chat, methods=["POST"]),
                Route(prefix + "/messages", native, methods=["POST"]), Route(prefix + "/responses", native, methods=["POST"])]

    async def native(self, request):
        """Native client formats preserve provider signatures on matching routes."""
        from model_gateway.ingress import collect_native
        protocol = "anthropic" if request.url.path.endswith("/messages") else "openai-responses"
        payload = await body(request)
        iterator = self.gateway.stream_native(bearer(request, native=protocol == "anthropic"), payload, protocol, extra_headers=dict(request.headers)).__aiter__()
        if not payload.get("stream", False):
            try:
                return JSONResponse(await while_connected(request, collect_native(protocol, iterator)))
            finally:
                await iterator.aclose()
        try:
            first = await while_connected(request, anext(iterator))
        except StopAsyncIteration:
            raise GatewayError("provider_incomplete", "The provider returned no completion.", 502) from None
        except BaseException:
            await iterator.aclose()
            raise
        def frame(record):
            return ("event: " + record["event"] + "\n" if record.get("event") else "") + "data: " + json_dumps(record["data"]) + "\n\n"
        async def events():
            try:
                yield frame(first)
                async for record in iterator:
                    yield frame(record)
            except GatewayError as error:
                yield frame({"event": "error", "data": {"type": "error", "error": error.as_dict()}})
            finally:
                await iterator.aclose()
        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


class StandaloneAuth:
    """Private bootstrap key exchanges for bounded, CSRF-protected admin sessions."""

    def __init__(self, store, public_origin):
        self.store, self.origin = store, exact_origin(public_origin)
        self.authority = urlsplit(self.origin).netloc
        self.key_path = store.root / "admin.key"
        self._admin_digest = hashlib.sha256(store._private_file(self.key_path, secrets.token_urlsafe(48).encode())).digest()
        self.sessions, self.attempts = {}, {}

    def security(self, request):
        if request.headers.getlist("host") != [self.authority]:
            raise GatewayError("host_denied", "Use the configured gateway URL.", 403)
        values = request.headers.getlist("origin")
        if values and values != [self.origin]:
            raise GatewayError("origin_denied", "The request origin does not match this gateway.", 403)

    async def authorize(self, request, *, mutate=False):
        self.security(request)
        authorization = request.headers.getlist("authorization")
        if authorization:
            key = bearer(request)
            if hmac.compare_digest(hashlib.sha256(key.encode()).digest(), self._admin_digest):
                return "local"
            raise GatewayError("unauthenticated", "The gateway management key is invalid.", 401)
        session = self.sessions.get(hashlib.sha256(request.cookies.get("sturgis_gateway", "").encode()).hexdigest())
        if not session or session["expires_at"] <= time.time():
            raise GatewayError("unauthenticated", "Sign in to manage the gateway.", 401)
        if mutate and (request.headers.get("origin") != self.origin or request.headers.getlist("x-csrf-token") != [session["csrf"]]):
            raise GatewayError("csrf_failed", "Refresh this page before trying again.", 401)
        return "local"

    async def session(self, request):
        self.security(request)
        if request.method == "GET":
            try:
                await self.authorize(request)
            except GatewayError:
                return {"authenticated": False}
            return {"authenticated": True}
        if request.method == "DELETE":
            await self.authorize(request, mutate=True)
            self.sessions.pop(hashlib.sha256(request.cookies.get("sturgis_gateway", "").encode()).hexdigest(), None)
            response = envelope({"authenticated": False})
            response.delete_cookie("sturgis_gateway")
            response.delete_cookie("sturgis_gateway_csrf")
            return response
        if request.headers.get("origin") != self.origin:
            raise GatewayError("origin_denied", "Sign in from the gateway page.", 403)
        peer = request.client.host if request.client else "local"
        window, count = self.attempts.get(peer, (time.monotonic(), 0))
        if time.monotonic() - window > 60:
            window, count = time.monotonic(), 0
        self.attempts[peer] = window, count + 1
        if count >= 10:
            raise GatewayError("rate_limited", "Wait a minute before trying another management key.", 429)
        value = await body(request, {"admin_key"}, {"admin_key"})
        key = value["admin_key"]
        if not isinstance(key, str) or len(key) > 256 or not hmac.compare_digest(hashlib.sha256(key.encode()).digest(), self._admin_digest):
            raise GatewayError("unauthenticated", "The gateway management key is invalid.", 401)
        self.sessions = {k: v for k, v in self.sessions.items() if v["expires_at"] > time.time()}
        if len(self.sessions) >= 256:
            raise GatewayError("busy", "Too many active management sessions.", 429)
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.sessions[hashlib.sha256(token.encode()).hexdigest()] = {"csrf": csrf, "expires_at": time.time() + 28800}
        response = envelope({"authenticated": True})
        response.set_cookie("sturgis_gateway", token, max_age=28800, httponly=True, secure=self.origin.startswith("https:"), samesite="strict")
        response.set_cookie("sturgis_gateway_csrf", csrf, max_age=28800, secure=self.origin.startswith("https:"), samesite="strict")
        return response


def envelope(data=None, error=None):
    return JSONResponse({"version": VERSION, "data": data, "error": error.as_dict() if error else None},
                        status_code=error.status if error else 200, headers={"Cache-Control": "no-store"})


def create_app(root, *, public_origin="http://127.0.0.1:8790", network_policy=None, allow_local_servers=True, web_root=None, auth_configs=None):
    """Compose a runnable independent gateway with zero agent imports or workers."""
    gateway = ModelGateway(root, network_policy=network_policy, allow_local_servers=allow_local_servers, auth_configs=auth_configs)
    auth = StandaloneAuth(gateway.store, public_origin)
    http = GatewayHTTP(gateway, endpoint=public_origin + "/v1", authorize=auth.authorize)
    async def management(request):
        try:
            result = await auth.session(request) if request.path_params.get("resource") == "session" else await http.management(request)
            return result if isinstance(result, Response) else envelope(result)
        except GatewayError as error:
            return envelope(error=error)
        except Exception:
            return envelope(error=GatewayError("internal_error", "The gateway could not complete this operation.", 500))
    async def health(request):
        return JSONResponse({"status": "ok", "version": VERSION})
    routes = [Route("/health", health, methods=["GET"]),
              Route("/api/v1/gateway/{resource:path}", management, methods=["GET", "POST", "PATCH", "DELETE"]),
              *http.inference_routes()]
    if web_root:
        web_root = Path(web_root)
        async def index(request):
            auth.security(request)
            return FileResponse(web_root / "gateway.html")
        routes += [Route("/", index), Mount("/assets", StaticFiles(directory=web_root / "assets"))]
    @asynccontextmanager
    async def lifespan(app):
        await gateway.start()
        try:
            yield
        finally:
            await gateway.close()
    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.gateway, app.state.auth = gateway, auth
    return app
