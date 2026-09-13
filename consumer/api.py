"""One authenticated HTTP boundary for portable clients and private agent workers."""
from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
from dataclasses import asdict
import hashlib
import os
from pathlib import Path
import secrets
import stat
import time
from urllib.parse import quote, urlsplit

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from agent.protocol import CoreError, canonical_json, new_id, strict_loads
from agent.state import Journal
from consumer.config import Settings
from consumer.contracts import MAX_BODY, VERSION, failure, identifier, object_body, success
from consumer.identity import Accounts, AgentDirectory
from consumer.lifecycle import Lifecycle
from consumer.portability import Portability
from consumer.workers import WorkerManager
from consumer.gateway import ConsumerGateway

READ_COMMANDS = frozenset("""overview conversations.list conversations.get messages.events attachments.list attachments.get attachments.read
files.list files.read knowledge.list knowledge.get memory.query memory.get providers.list providers.inspect providers.credentials.list
connections.list connections.get skills.list usage.summary artifacts.read work.overview work.list work.get goals.list commitments.list schedules.list
workflows.list workflows.get children.list children.get inbox.list permissions.list permissions.pending permissions.request devices.list devices.get
plugins.list plugins.inspect plugins.health diagnostics.get""".split())
WRITE_COMMANDS = frozenset("""conversations.create conversations.update messages.send messages.cancel attachments.upload files.write files.delete
knowledge.add knowledge.delete memory.correct memory.retract providers.configure providers.probe providers.activate providers.credentials.put providers.credentials.revoke
connections.configure connections.grant connections.probe connections.revoke skills.install skills.enable skills.rollback work.cancel goals.create goals.edit goals.transition goals.archive
commitments.create commitments.transition commitments.progress commitments.wait commitments.resume schedules.create schedules.state workflows.create workflows.revise workflows.run workflows.state workflows.step
children.cancel children.delegate inbox.decide initiative.propose attention.policy permissions.set permissions.approve permissions.deny devices.pair devices.revoke
plugins.activate plugins.disable plugins.rollback plugins.uninstall""".split())
SECRET_COMMANDS = {"providers.credentials.put", "devices.pair"}
COOKIE = "sturgis_session"
CSRF_COOKIE = "sturgis_csrf"


def _bootstrap(root, supplied):
    if supplied:
        return supplied
    path = root / "bootstrap.token"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            meta = os.fstat(stream.fileno())
            if not stat.S_ISREG(meta.st_mode) or meta.st_mode & 0o077:
                raise CoreError("unsafe_configuration", "Bootstrap token must be a private regular file")
            token = stream.read(256).decode("ascii")
        if not 32 <= len(token) <= 128:
            raise CoreError("unsafe_configuration", "Bootstrap token file is invalid")
        return token
    token = secrets.token_urlsafe(32)
    with os.fdopen(fd, "w") as stream:
        stream.write(token); stream.flush(); os.fsync(stream.fileno())
    return token


def _status(error):
    code = error.code if isinstance(error, CoreError) else "internal_error"
    if code in {"unauthenticated", "csrf_failed"}: return 401
    if code in {"forbidden", "origin_denied", "host_denied", "scope_denied", "webhook_signature", "device_signature"}: return 403
    if code in {"not_found", "connection_missing", "unknown_command"}: return 404
    if code in {"conflict", "revision_conflict", "dedupe_conflict", "stale_connection", "agent_archived", "runtime_archived", "runtime_busy", "busy", "bootstrap_closed"}: return 409
    if code == "body_too_large": return 413
    if code == "rate_limited": return 429
    if code in {"worker_unavailable", "worker_timeout", "runtime_unavailable", "provider_unavailable", "isolation_unavailable", "browser_unavailable"}: return 503
    return 500 if code == "internal_error" else 400


class ConsumerControl:
    def __init__(self, settings):
        self.settings = settings
        settings.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(settings.root, 0o700)
        if settings.public_origin:
            self.origin = settings.public_origin
        else:
            if settings.host in {"0.0.0.0", "::"}:
                raise CoreError("invalid_config", "Wildcard listeners require an explicit public origin")
            host = f"[{settings.host}]" if ":" in settings.host else settings.host
            self.origin = f"http://{host}" + (f":{settings.port}" if settings.port != 80 else "")
        self.authority = urlsplit(self.origin).netloc.casefold()
        self.secure = self.origin.startswith("https://")
        self.journal = Journal(settings.root / "control.sqlite")
        try:
            self.accounts = Accounts(self.journal, bootstrap_token=_bootstrap(settings.root, settings.bootstrap_token), allow_signup=settings.allow_signup)
            self.agents = AgentDirectory(self.journal)
            self.manager = WorkerManager(settings)
            self.lifecycle = Lifecycle(self.journal, self.manager)
            self.portability = Portability(self.journal, self.lifecycle, self.manager)
            self.gateway = ConsumerGateway(self)
        except BaseException:
            self.journal.close()
            raise

    async def start(self):
        await self.gateway.service.start()
        await self.portability.recover()
        await self.lifecycle.start()

    async def close(self):
        await self.lifecycle.close()
        await self.gateway.service.close()
        await asyncio.to_thread(self.journal.close)

    def security(self, request):
        hosts = request.headers.getlist("host")
        if len(hosts) != 1 or hosts[0].casefold() != self.authority:
            raise CoreError("host_denied", "Request host does not match this installation")
        origins = request.headers.getlist("origin")
        if origins and (len(origins) != 1 or origins[0] != self.origin):
            raise CoreError("origin_denied", "Request origin does not match this installation")

    async def raw_body(self, request):
        if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
            raise CoreError("invalid_request", "Use application/json for consumer requests")
        chunks, count = [], 0
        async for chunk in request.stream():
            count += len(chunk)
            if count > MAX_BODY:
                raise CoreError("body_too_large", "Request exceeds the byte limit")
            chunks.append(chunk)
        return b"".join(chunks)

    async def body(self, request, allowed=None, required=()):
        value = strict_loads(await self.raw_body(request))
        return object_body(value, value.keys() if allowed is None and isinstance(value, dict) else allowed or (), required)

    async def principal(self, request, *, mutate=False):
        if len(request.headers.getlist("authorization")) > 1 or len(request.headers.getlist("x-csrf-token")) > 1:
            raise CoreError("unauthenticated", "Ambiguous authentication headers")
        cookie_names = [part.split("=", 1)[0].strip() for header in request.headers.getlist("cookie") for part in header.split(";") if "=" in part]
        if any(cookie_names.count(name) > 1 for name in (COOKIE, CSRF_COOKIE)):
            raise CoreError("unauthenticated", "Ambiguous session cookies")
        authorization = request.headers.get("authorization", "")
        if authorization:
            if not authorization.startswith("Bearer "):
                raise CoreError("unauthenticated", "Use bearer authentication")
            return await asyncio.to_thread(self.accounts.authenticate, authorization[7:])
        principal = await asyncio.to_thread(self.accounts.authenticate, request.cookies.get(COOKIE))
        if mutate:
            if request.headers.get("origin") != self.origin:
                raise CoreError("origin_denied", "Browser mutations require the installation origin")
            await asyncio.to_thread(self.accounts.validate_csrf, principal, request.headers.get("x-csrf-token"))
        return principal

    def session_response(self, data, request_id):
        response = JSONResponse(success(data, request_id))
        lifetime = max(0, int(data["expires_at"] - time.time()))
        response.set_cookie(COOKIE, data["token"], max_age=lifetime, httponly=True, secure=self.secure, samesite="strict")
        response.set_cookie(CSRF_COOKIE, data["csrf_token"], max_age=lifetime, httponly=False, secure=self.secure, samesite="strict")
        return response

    async def public(self, request):
        rid = request.state.request_id
        action = request.path_params["action"]
        if action == "health" and request.method == "GET":
            return {"status": "ok", "version": VERSION, "mode": self.settings.mode}
        if action == "bootstrap" and request.method == "GET":
            return await asyncio.to_thread(self.accounts.status)
        if action == "bootstrap" and request.method == "POST":
            body = await self.body(request, {"email", "password", "token"}, {"email", "password", "token"})
            return self.session_response(await asyncio.to_thread(self.accounts.bootstrap, **body), rid)
        if action in {"login", "signup"} and request.method == "POST":
            body = await self.body(request, {"email", "password"}, {"email", "password"})
            fn = self.accounts.login if action == "login" else self.accounts.signup
            return self.session_response(await asyncio.to_thread(fn, **body), rid)
        raise CoreError("not_found", "Consumer route is unavailable")

    async def identity(self, request):
        action = request.path_params["action"]
        principal = await self.principal(request, mutate=request.method != "GET")
        if action == "me" and request.method == "GET":
            return asdict(principal)
        if action == "logout" and request.method == "POST":
            await self.body(request, set())
            data = await asyncio.to_thread(self.accounts.logout, principal)
            response = JSONResponse(success(data, request.state.request_id))
            response.delete_cookie(COOKIE); response.delete_cookie(CSRF_COOKIE)
            return response
        raise CoreError("not_found", "Identity route is unavailable")

    async def agent_collection(self, request):
        principal = await self.principal(request, mutate=request.method == "POST")
        if request.method == "GET":
            include = request.query_params.get("include_archived", "false")
            if include not in {"true", "false"}:
                raise CoreError("invalid_request", "Archive filter must be true or false")
            return {"items": await asyncio.to_thread(self.agents.list, principal, include_archived=include == "true")}
        body = await self.body(request, {"name"}, {"name"})
        agent = await asyncio.to_thread(self.agents.create, principal, body["name"])
        await self.lifecycle.create(agent["id"])
        return agent

    async def agent_item(self, request):
        principal = await self.principal(request, mutate=request.method != "GET")
        id = identifier(request.path_params["agent_id"])
        agent = await asyncio.to_thread(self.agents.authorize, principal, id, "read" if request.method == "GET" else "manage")
        if request.method == "GET":
            return {**agent, "runtime": await self.lifecycle.status(id)}
        body = await self.body(request, {"name", "archived", "expected_revision"}, {"expected_revision"})
        if "name" in body and "archived" not in body:
            return await asyncio.to_thread(self.agents.rename, principal, id, body["name"], body["expected_revision"])
        if body.get("archived") is True and "name" not in body:
            if type(body["expected_revision"]) is not int or body["expected_revision"] != agent["revision"]:
                raise CoreError("conflict", "Agent revision changed")
            await self.lifecycle.archive(id, finalize=lambda: self.agents.archive(principal, id, body["expected_revision"]))
            return await asyncio.to_thread(self.agents.authorize, principal, id)
        raise CoreError("invalid_request", "Choose one supported agent metadata change")

    async def backup(self, request):
        principal = await self.principal(request, mutate=True)
        id = identifier(request.path_params["agent_id"])
        await self.body(request, set())
        return await self.portability.backup(principal, id)

    async def restore(self, request):
        principal = await self.principal(request, mutate=True)
        body = await self.body(request, {"name", "archive_base64", "sha256", "request_id"}, {"name", "archive_base64", "sha256"})
        if "request_id" in body: identifier(body["request_id"])
        return await self.portability.restore(principal, **body)

    async def runtime_action(self, request):
        principal = await self.principal(request, mutate=True)
        id = identifier(request.path_params["agent_id"])
        await asyncio.to_thread(self.agents.authorize, principal, id, "manage")
        await self.body(request, set())
        actions = {"start": self.lifecycle.start_agent, "status": self.lifecycle.status,
                   "suspend": self.lifecycle.suspend, "recover": self.lifecycle.recover}
        action = actions.get(request.path_params["action"])
        if action is None:
            raise CoreError("unknown_command", "Unknown lifecycle action")
        return await action(id)

    def _external_route(self, agent_id, result):
        if not isinstance(result, dict) or result.get("kind") not in {"webhook", "device"}:
            return
        key = agent_id + ":" + result["id"]
        row = self.journal.get("consumer.external_routes", key, "control")
        self.journal.put("consumer.external_routes", key, "control", {
            "agent_id": agent_id, "connection_id": result["id"], "kind": result["kind"],
            "active": result["active"], "revision": result["revision"]}, row.revision if row else 0)

    async def command(self, request):
        method = request.path_params["method"]
        if method not in READ_COMMANDS | WRITE_COMMANDS:
            raise CoreError("unknown_command", "Unsupported consumer command")
        principal = await self.principal(request, mutate=True)
        id = identifier(request.path_params["agent_id"])
        await asyncio.to_thread(self.agents.authorize, principal, id, "read" if method in READ_COMMANDS else "write")
        body = await self.body(request, {"params", "request_id"}, {"params", "request_id"})
        identifier(body["request_id"])
        result = await self.lifecycle.command(id, method, body["params"], request_id=body["request_id"])
        if method.startswith("connections.") or method == "devices.revoke":
            await asyncio.to_thread(self._external_route, id, result)
        return result

    async def events(self, request):
        principal = await self.principal(request)
        id = identifier(request.path_params["agent_id"])
        await asyncio.to_thread(self.agents.authorize, principal, id)
        try:
            after = int(request.query_params.get("after", "0"))
            wait = float(request.query_params.get("wait", "0"))
        except ValueError:
            raise CoreError("invalid_cursor", "Invalid event cursor or wait") from None
        if after < 0 or not 0 <= wait <= 25:
            raise CoreError("invalid_cursor", "Invalid event cursor or wait")
        conversation = request.query_params.get("conversation_id")
        params = {"after": after, "limit": 500}
        if conversation:
            params["conversation_id"] = identifier(conversation)
        async def page():
            await asyncio.to_thread(self.agents.authorize, principal, id)
            return await self.lifecycle.command(id, "messages.events", params, request_id=new_id("events"))
        if request.headers.get("accept") == "text/event-stream":
            async def stream():
                deadline = time.monotonic() + (wait or 25)
                while time.monotonic() < deadline and not await request.is_disconnected():
                    try:
                        data = await page()
                        params["after"] = data["cursor"]
                        yield f"id: {data['cursor']}\nevent: page\ndata: {canonical_json(data)}\n\n"
                    except CoreError as error:
                        yield "event: error\ndata: " + canonical_json(failure(error)) + "\n\n"
                        return
                    await asyncio.sleep(.1)
            return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})
        deadline = time.monotonic() + wait
        while True:
            data = await page()
            params["after"] = data["cursor"]
            if data["items"] or time.monotonic() >= deadline:
                return data
            await asyncio.sleep(.05)

    async def attachment(self, request):
        principal = await self.principal(request)
        id = identifier(request.path_params["agent_id"])
        attachment = identifier(request.path_params["attachment_id"])
        await asyncio.to_thread(self.agents.authorize, principal, id)
        meta = await self.lifecycle.command(id, "attachments.get", {"id": attachment}, request_id=new_id("download"))
        data = await self.lifecycle.command(id, "attachments.read", {"id": attachment, "limit": 4 * 1024 * 1024}, request_id=new_id("download"))
        body = base64.b64decode(data["data_base64"], validate=True)
        if not data["complete"] or hashlib.sha256(body).hexdigest() != meta["sha256"]:
            raise CoreError("integrity", "Attachment download is incomplete or changed")
        return Response(body, media_type="application/octet-stream", headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(meta["name"], safe=""),
            "Content-Security-Policy": "sandbox; default-src 'none'"})

    async def artifact(self, request):
        principal = await self.principal(request)
        id = identifier(request.path_params["agent_id"])
        artifact = identifier(request.path_params["artifact_id"])
        await asyncio.to_thread(self.agents.authorize, principal, id)
        data = await self.lifecycle.command(id, "artifacts.read", {"id": artifact, "limit": 4 * 1024 * 1024}, request_id=new_id("download"))
        body = base64.b64decode(data["data_base64"], validate=True)
        if not data["complete"]:
            raise CoreError("body_too_large", "Use bounded artifact ranges for this body")
        if hashlib.sha256(body).hexdigest() != data["sha256"]:
            raise CoreError("integrity", "Artifact download changed")
        return Response(body, media_type="application/octet-stream", headers={
            "Content-Disposition": "attachment; filename=" + artifact,
            "Content-Security-Policy": "sandbox; default-src 'none'"})

    async def external(self, request):
        # This identity is cryptographic external evidence; it never impersonates an owner.
        agent_id = identifier(request.path_params["agent_id"])
        connection_id = identifier(request.path_params["connection_id"])
        kind, action = request.path_params["kind"], request.path_params["action"]
        route = await asyncio.to_thread(self.journal.get, "consumer.external_routes", agent_id + ":" + connection_id, "control")
        agent = await asyncio.to_thread(self.journal.get, "consumer.agents", agent_id, "control")
        if not route or not route.value["active"] or route.value["kind"] != kind or not agent or agent.value["archived"]:
            raise CoreError("not_found", "External route is unavailable")
        def limit():
            key = agent_id + ":" + connection_id
            with self.journal.transaction():
                row = self.journal.get("consumer.external_limits", key, "control")
                now = int(time.time() // 60)
                count = row.value["count"] if row and row.value["window"] == now else 0
                if count >= 120:
                    raise CoreError("rate_limited", "External route request limit reached")
                self.journal.put("consumer.external_limits", key, "control", {"window": now, "count": count + 1}, row.revision if row else 0)
        await asyncio.to_thread(limit)
        raw = await self.raw_body(request)
        body = object_body(strict_loads(raw), {"event_id", "text", "data"}) if kind == "webhook" else strict_loads(raw)
        if kind == "webhook" and action == "receive":
            method, params = "ingress.webhook", {"connection_id": connection_id, "body_base64": base64.b64encode(raw).decode()}
        elif kind == "device" and action in {"claim", "poll", "respond", "sync"}:
            method, params = "ingress.device." + action, {"connection_id": connection_id}
            if action == "claim":
                params.update(object_body(body, {"pairing_token", "device_id"}, {"pairing_token", "device_id"}))
            elif action == "respond":
                params["body_base64"] = base64.b64encode(raw).decode()
            else:
                object_body(body, set())
        else:
            raise CoreError("not_found", "External operation is unavailable")
        if action != "claim":
            try:
                params.update(timestamp=int(request.headers.get("x-sturgis-timestamp", "")),
                    nonce=request.headers.get("x-sturgis-nonce"), signature=request.headers.get("x-sturgis-signature"))
            except ValueError:
                raise CoreError("invalid_signature", "External signature headers are required") from None
        # Raw secrets and signed bodies are never persisted in control-plane command records.
        return await self.lifecycle.command(agent_id, method, params, request_id=new_id("external"))


def create_app(settings=None):
    settings = settings or Settings.from_env()
    control = ConsumerControl(settings)
    async def endpoint(request, handler):
        request.state.request_id = new_id("http")
        try:
            control.security(request)
            result = await handler(request)
            return result if isinstance(result, Response) else JSONResponse(success(result, request.state.request_id))
        except Exception as error:
            return JSONResponse(failure(error, request.state.request_id), status_code=_status(error))
    def route(path, handler, methods):
        async def bound(request):
            return await endpoint(request, handler)
        return Route(path, bound, methods=methods)
    async def public(request):
        request.path_params["action"] = request.url.path.rsplit("/", 1)[-1]
        return await control.public(request)
    async def identity(request):
        request.path_params["action"] = request.url.path.rsplit("/", 1)[-1]
        return await control.identity(request)
    async def index(request):
        control.security(request)
        path = Path(__file__).parent / "web/index.html"
        if not path.is_file():
            return JSONResponse(failure(CoreError("not_found", "Web assets are not installed")), status_code=404)
        return FileResponse(path)
    routes = [route("/api/v1/health", public, ["GET"]), route("/api/v1/bootstrap", public, ["GET", "POST"]),
        route("/api/v1/auth/login", public, ["POST"]), route("/api/v1/auth/signup", public, ["POST"]),
        route("/api/v1/auth/me", identity, ["GET"]), route("/api/v1/auth/logout", identity, ["POST"]),
        route("/api/v1/gateway/{resource:path}", control.gateway.management, ["GET", "POST", "PATCH", "DELETE"]),
        route("/api/v1/agents/{agent_id}/gateway-model", control.gateway.select, ["POST"]),
        *control.gateway.http.inference_routes("/model-gateway/v1"),
        route("/api/v1/agents", control.agent_collection, ["GET", "POST"]),
        route("/api/v1/agents/restore", control.restore, ["POST"]),
        route("/api/v1/agents/{agent_id}/backup", control.backup, ["POST"]),
        route("/api/v1/agents/{agent_id}", control.agent_item, ["GET", "PATCH"]),
        route("/api/v1/agents/{agent_id}/lifecycle/{action}", control.runtime_action, ["POST"]),
        route("/api/v1/agents/{agent_id}/commands/{method}", control.command, ["POST"]),
        route("/api/v1/agents/{agent_id}/events", control.events, ["GET"]),
        route("/api/v1/agents/{agent_id}/attachments/{attachment_id}", control.attachment, ["GET"]),
        route("/api/v1/agents/{agent_id}/artifacts/{artifact_id}", control.artifact, ["GET"]),
        route("/api/v1/external/{agent_id}/{kind}/{connection_id}/{action}", control.external, ["POST"]),
        route("/", index, ["GET"]),
        Mount("/assets", StaticFiles(directory=Path(__file__).parent / "web/assets", check_dir=False), name="assets")]
    @asynccontextmanager
    async def lifespan(app):
        await control.start()
        try:
            yield
        finally:
            await control.close()
    async def http_error(request, error):
        return JSONResponse(failure(CoreError("not_found" if error.status_code == 404 else "method_not_allowed", "Consumer route is unavailable")), status_code=error.status_code)
    app = Starlette(routes=routes, lifespan=lifespan, exception_handlers={HTTPException: http_error})
    app.state.control = control
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
    app.add_middleware(SecurityHeaders, control=control)
    return app


class SecurityHeaders:
    def __init__(self, app, control):
        self.app, self.control = app, control

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        async def secured(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                names = {name.lower() for name, _ in headers}
                values = {b"x-content-type-options": b"nosniff", b"referrer-policy": b"no-referrer",
                    b"cache-control": b"public, max-age=31536000, immutable" if scope.get("path", "").startswith("/assets/") and message["status"] == 200 else b"no-store", b"content-security-policy": b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; connect-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"}
                headers.extend((k, v) for k, v in values.items() if k not in names)
                message["headers"] = headers
            await send(message)
        try:
            self.control.security(Request(scope))
        except CoreError as error:
            return await JSONResponse(failure(error), status_code=_status(error))(scope, receive, secured)
        return await self.app(scope, receive, secured)


def serve(settings=None):
    import uvicorn
    settings = settings or Settings.from_env()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, proxy_headers=False,
                access_log=False, limit_concurrency=128, timeout_keep_alive=5)
