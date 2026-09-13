"""Real loopback listener expiry, callback validation, and shutdown ownership."""
import asyncio
from contextlib import contextmanager
import socket
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from model_gateway.callbacks import CallbackListeners
from model_gateway.errors import GatewayError


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def session(listen_port, **extra):
    return {"id": "session", "tenant": "alice", "flow": "authorization_code_pkce", "status": "pending",
            "redirect_uri": f"http://localhost:{listen_port}/auth/callback",
            "authorization_url": "https://provider.example/login?state=state", "expires_at": time.time() + 900, **extra}


def listeners():
    auth = SimpleNamespace(complete=AsyncMock(return_value={"status": "connected"}))
    result = CallbackListeners(auth)
    result._cleanup_interval = .01
    return result


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(.005)


async def request(listen_port, target="/auth/callback?state=state&code=private-code", method="GET"):
    reader, writer = await asyncio.open_connection("127.0.0.1", listen_port)
    writer.write(f"{method} {target} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
    await writer.drain()
    try:
        return await asyncio.wait_for(reader.read(), 2)
    finally:
        writer.close()
        await writer.wait_closed()


async def test_expiration_releases_port_without_new_watch_and_retires_maintenance():
    callbacks = listeners(); listen_port = port()
    try:
        observed = await callbacks.watch("alice", session(listen_port, expires_at=time.time() + .04))
        assert observed["callback_mode"] == "automatic"
        await until(lambda: not callbacks.servers and callbacks._maintenance_task is None and not callbacks._drains)
        assert not callbacks.sessions and not callbacks.tasks
        # The listener is truly gone, not merely absent from an in-memory map.
        rebound = await asyncio.start_server(lambda reader, writer: writer.close(), "127.0.0.1", listen_port)
        rebound.close(); await rebound.wait_closed()
        assert callbacks.auth.complete.await_count == 0
    finally:
        await callbacks.close()


async def test_callback_closes_its_own_listener_without_deadlock_or_secret_echo():
    callbacks = listeners(); listen_port = port()
    try:
        await callbacks.watch("alice", session(listen_port))
        response = await request(listen_port)
        assert response.startswith(b"HTTP/1.1 200") and b"Return to Model Gateway" in response
        assert b"private-code" not in response and b"Cache-Control: no-store" in response
        callbacks.auth.complete.assert_awaited_once_with("alice", "session", "state", "private-code")
        await until(lambda: not callbacks.tasks and not callbacks._drains and callbacks._maintenance_task is None)
        assert not callbacks.servers and not callbacks.sessions
    finally:
        await callbacks.close()


async def test_one_expired_listener_does_not_block_another_inflight_callback():
    callbacks = listeners(); active_port, expiring_port = port(), port()
    entered, release = asyncio.Event(), asyncio.Event()
    async def complete(*args):
        entered.set(); await release.wait(); return {"status": "connected"}
    callbacks.auth.complete.side_effect = complete
    client = None
    try:
        await callbacks.watch("alice", session(active_port))
        await callbacks.watch("bob", session(expiring_port, id="expired", authorization_url="https://provider.example/?state=second", expires_at=time.time() + .08))
        client = asyncio.create_task(request(active_port))
        await asyncio.wait_for(entered.wait(), 1)
        await callbacks.forget("session")
        await until(lambda: expiring_port not in callbacks.servers)
        assert callbacks.tasks and not client.done()
        release.set()
        assert (await client).startswith(b"HTTP/1.1 200")
        await until(lambda: not callbacks._drains and not callbacks.tasks)
    finally:
        release.set()
        if client is not None: await asyncio.gather(client, return_exceptions=True)
        await callbacks.close()


async def test_shutdown_cancels_active_exchange_and_fences_new_listeners():
    callbacks = listeners(); listen_port = port(); entered = asyncio.Event()
    async def complete(*args):
        entered.set(); await asyncio.Event().wait()
    callbacks.auth.complete.side_effect = complete
    await callbacks.watch("alice", session(listen_port))
    client = asyncio.create_task(request(listen_port))
    await asyncio.wait_for(entered.wait(), 1)
    await asyncio.wait_for(callbacks.close(), 1)
    await client
    assert not callbacks.servers and not callbacks.sessions and not callbacks.tasks and not callbacks._drains
    assert callbacks._maintenance_task is None
    assert (await callbacks.watch("alice", session(listen_port)))["callback_mode"] == "manual"
    await callbacks.close()


@pytest.mark.parametrize("extra", [
    {"flow": "device_code"}, {"status": "connected"}, {"redirect_uri": None},
])
async def test_nonpending_or_device_flows_create_no_listener(extra):
    callbacks = listeners(); value = session(port(), **extra)
    assert await callbacks.watch("alice", value) == value
    assert not callbacks.servers and callbacks._maintenance_task is None
    await callbacks.close()


@pytest.mark.parametrize("extra", [
    {"redirect_uri": "https://localhost:1455/auth/callback"},
    {"redirect_uri": "http://provider.example:1455/auth/callback"},
    {"redirect_uri": "http://localhost/auth/callback"},
    {"authorization_url": "https://provider.example/?state=a&state=b"},
    {"authorization_url": "https://provider.example/"},
    {"expires_at": 0},
])
async def test_unsupported_or_expired_callback_falls_back_to_manual(extra):
    callbacks = listeners()
    assert (await callbacks.watch("alice", session(port(), **extra)))["callback_mode"] == "manual"
    assert not callbacks.servers
    await callbacks.close()


async def test_busy_port_and_session_capacity_do_not_claim_automatic_callback():
    callbacks = listeners(); listen_port = port()
    occupied = await asyncio.start_server(lambda reader, writer: writer.close(), "127.0.0.1", listen_port)
    try:
        assert (await callbacks.watch("alice", session(listen_port)))["callback_mode"] == "manual"
        callbacks.sessions = {str(i): {"expires_at": time.time() + 900, "port": listen_port} for i in range(128)}
        assert (await callbacks.watch("alice", session(port())))["callback_mode"] == "manual"
        assert not callbacks.servers
    finally:
        occupied.close(); await occupied.wait_closed(); await callbacks.close()


@pytest.mark.parametrize("target,method", [
    ("/auth/callback?state=state&code=one", "POST"),
    ("http://evil.example/auth/callback?state=state&code=one", "GET"),
    ("/auth/callback?state=state", "GET"),
    ("/auth/callback?state=state&code=one&code=two", "GET"),
    ("/auth/callback?state=unknown&code=one", "GET"),
    ("/different?state=state&code=one", "GET"),
])
async def test_callback_rejects_ambiguous_and_mismatched_requests(target, method):
    callbacks = listeners(); listen_port = port()
    try:
        await callbacks.watch("alice", session(listen_port))
        response = await request(listen_port, target, method)
        assert response.startswith(b"HTTP/1.1 400")
        callbacks.auth.complete.assert_not_awaited()
    finally:
        await callbacks.close()


@pytest.mark.parametrize("result", [{"status": "failed"}, GatewayError("denied", "Private upstream error")])
async def test_callback_errors_remain_sanitized(result):
    callbacks = listeners(); listen_port = port()
    if isinstance(result, Exception): callbacks.auth.complete.side_effect = result
    else: callbacks.auth.complete.return_value = result
    try:
        await callbacks.watch("alice", session(listen_port))
        response = await request(listen_port)
        assert response.startswith(b"HTTP/1.1 400") and b"Private upstream error" not in response
    finally:
        await callbacks.close()


async def test_restore_watches_only_unexpired_pending_sessions():
    callbacks = listeners(); listen_port = port()
    rows = [session(listen_port), session(port(), id="expired", expires_at=0), session(port(), id="cancelled", status="cancelled")]
    @contextmanager
    def transaction():
        yield SimpleNamespace(execute=lambda sql: rows)
    callbacks.auth.store = SimpleNamespace(transaction=transaction, _row=lambda row: row)
    callbacks.auth._public_session = lambda row: row
    try:
        await callbacks.restore()
        assert set(callbacks.servers) == {listen_port} and len(callbacks.sessions) == 1
        await callbacks.watch("alice", rows[0])
        assert len(callbacks.servers) == 1
    finally:
        await callbacks.close()


async def test_saturated_callback_handlers_close_new_connections():
    callbacks = listeners(); listen_port = port()
    blockers = [asyncio.create_task(asyncio.Event().wait()) for _ in range(8)]
    callbacks.tasks.update(blockers)
    try:
        await callbacks.watch("alice", session(listen_port))
        reader, writer = await asyncio.open_connection("127.0.0.1", listen_port)
        assert await asyncio.wait_for(reader.read(), 1) == b""
        writer.close(); await writer.wait_closed()
        callbacks.auth.complete.assert_not_awaited()
    finally:
        await callbacks.close()
        callbacks.tasks.clear()
