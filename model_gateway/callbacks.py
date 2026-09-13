"""Bounded loopback OAuth callbacks owned by the standalone gateway lifecycle."""
from __future__ import annotations

import asyncio
import time
from urllib.parse import parse_qs, urlsplit

from model_gateway.errors import GatewayError


class CallbackListeners:
    """Receive installed-client PKCE callbacks; device-code flows need no listener."""

    def __init__(self, auth):
        self.auth = auth
        self.servers, self.sessions, self.tasks = {}, {}, set()
        self._drains = set()
        self._maintenance_task = None
        self._cleanup_interval = 1.0
        self._closed = False
        self._lock = asyncio.Lock()

    async def watch(self, tenant, session):
        """Attach only to provider-validated loopback redirects, never public ports."""
        redirect = session.get("redirect_uri")
        if session.get("flow") != "authorization_code_pkce" or session.get("status") not in {"pending", "exchanging"} or not redirect:
            return session
        url = urlsplit(redirect)
        query = parse_qs(urlsplit(session.get("authorization_url", "")).query)
        states = query.get("state", [])
        if url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1"} or not url.port or len(states) != 1:
            return {**session, "callback_mode": "manual"}
        async with self._lock:
            await self._prune()
            if self._closed or session["expires_at"] <= time.time() or len(self.sessions) >= 128:
                return {**session, "callback_mode": "manual"}
            if url.port not in self.servers:
                try:
                    self.servers[url.port] = await asyncio.start_server(self._receive, "127.0.0.1", url.port, limit=16384)
                except OSError:
                    return {**session, "callback_mode": "manual"}
            self.sessions[states[0]] = {"tenant": tenant, "id": session["id"], "path": url.path, "port": url.port,
                "expires_at": session["expires_at"]}
            if self._maintenance_task is None:
                self._maintenance_task = asyncio.create_task(self._maintain())
        return {**session, "callback_mode": "automatic"}

    async def _maintain(self):
        """Release expired ports even when no browser polls or new sign-in arrives."""
        try:
            while True:
                await asyncio.sleep(self._cleanup_interval)
                async with self._lock:
                    await self._prune()
                    if not self.servers:
                        return
        finally:
            self._maintenance_task = None

    async def _drain(self, server):
        """Join a retired listener independently of its own callback handler."""
        try:
            await server.wait_closed()
        except OSError:
            pass

    async def _prune(self):
        self.sessions = {k: v for k, v in self.sessions.items() if v["expires_at"] > time.time()}
        used = {row["port"] for row in self.sessions.values()}
        for port in list(self.servers):
            if port not in used:
                server = self.servers.pop(port)
                server.close()
                # wait_closed can wait for this callback's own connection. Join
                # it in a separate task so expiration never blocks other ports.
                drain = asyncio.create_task(self._drain(server))
                self._drains.add(drain)
                drain.add_done_callback(self._drains.discard)

    async def forget(self, session_id):
        async with self._lock:
            self.sessions = {k: v for k, v in self.sessions.items() if v["id"] != session_id}
            await self._prune()

    async def _receive(self, reader, writer):
        task = asyncio.current_task()
        if len(self.tasks) >= 8:
            writer.close()
            await writer.wait_closed()
            return
        self.tasks.add(task)
        status, message = 400, "This sign-in link is unavailable. Return to Model Gateway and start again."
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            first = raw.split(b"\r\n", 1)[0].decode("ascii").split(" ")
            if len(first) != 3 or first[0] != "GET":
                raise ValueError()
            target = urlsplit(first[1])
            if target.scheme or target.netloc:
                raise ValueError()
            values = parse_qs(target.query, max_num_fields=16)
            if any(len(values.get(field, [])) != 1 for field in ("state", "code")):
                raise ValueError()
            state, code = values["state"][0], values["code"][0]
            row = self.sessions.get(state)
            if row is None or row["expires_at"] <= time.time() or row["path"] != target.path or row["port"] != writer.get_extra_info("sockname")[1]:
                raise ValueError()
            result = await self.auth.complete(row["tenant"], row["id"], state, code)
            if result["status"] not in {"connected", "pending"}:
                raise GatewayError("auth_incomplete", "The provider has not completed sign-in.")
            status = 200
            message = "Sign-in received. Return to Model Gateway to finish connecting your models. You can close this tab."
            await self.forget(row["id"])
        except (ValueError, UnicodeError, GatewayError, asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass
        finally:
            page = ("<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                "<title>Sturgis Model Gateway</title><body><main><h1>Sturgis Model Gateway</h1><p>" + message + "</p></main></body></html>").encode()
            try:
                writer.write(f"HTTP/1.1 {status} Result\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(page)}\r\nCache-Control: no-store\r\nContent-Security-Policy: default-src 'none'; style-src 'none'; frame-ancestors 'none'\r\nReferrer-Policy: no-referrer\r\nConnection: close\r\n\r\n".encode() + page)
                await writer.drain()
            except (ConnectionError, OSError):
                pass
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            finally:
                self.tasks.discard(task)

    async def restore(self):
        """Reattach still-authorized pending sign-ins after gateway restart."""
        with self.auth.store.transaction() as db:
            rows = [(row["tenant"], self.auth.store._row(row)) for row in db.execute("SELECT * FROM objects WHERE kind='auth_session'")]
        for tenant, row in rows:
            if row.get("status") in {"pending", "exchanging"} and row.get("expires_at", 0) > time.time():
                await self.watch(tenant, self.auth._public_session(row))

    async def close(self):
        """Fence new listeners, stop expiry work, and join handlers before drains."""
        async with self._lock:
            self._closed = True
            maintenance = self._maintenance_task
            if maintenance is not None:
                maintenance.cancel()
            self.sessions.clear()
            await self._prune()
        if maintenance is not None:
            await asyncio.gather(maintenance, return_exceptions=True)
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(*self._drains, return_exceptions=True)
