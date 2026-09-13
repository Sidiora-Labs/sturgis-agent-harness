"""Safe provider diagnostics over real HTTP, including bounded rejected responses."""
import asyncio
from contextlib import asynccontextmanager
import json
import time

import pytest

from agent.protocol import CoreError
from agent.provider_adapters import HTTPProvider


SECRET = "sk-test-provider-secret-should-never-be-returned"


@asynccontextmanager
async def error_wire(status, chunks, *, hold_open=False, declared_length=None):
    """Expose actual socket closure so cancellation/size checks prove cleanup."""
    accepted, disconnected = asyncio.Event(), asyncio.Event()
    handlers, requests = set(), []

    async def serve(reader, writer):
        handlers.add(asyncio.current_task())
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            length = next(int(line.split(b":", 1)[1]) for line in head.split(b"\r\n")
                          if line.lower().startswith(b"content-length:"))
            requests.append((head, await reader.readexactly(length)))
            header = f"HTTP/1.1 {status} Response\r\nContent-Type: application/json\r\nConnection: close\r\n"
            if declared_length is not None:
                header += f"Content-Length: {declared_length}\r\n"
            writer.write(header.encode() + b"\r\n")
            await writer.drain()
            accepted.set()
            for chunk in chunks:
                writer.write(chunk)
                await writer.drain()
                await asyncio.sleep(0)
            if hold_open:
                await reader.read()
                disconnected.set()
        except (ConnectionError, asyncio.IncompleteReadError):
            disconnected.set()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            handlers.discard(asyncio.current_task())

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    endpoint = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/chat"
    try:
        yield endpoint, accepted, disconnected, requests
    finally:
        server.close()
        await server.wait_closed()
        for task in list(handlers):
            task.cancel()
        await asyncio.gather(*handlers, return_exceptions=True)


async def collect(endpoint, *, cancel=None, **limits):
    descriptor = {"endpoint": endpoint, "model": "diagnostic-test", "authentication": "bearer",
                  "tools": False, "output_limit": 64}
    return [item async for item in HTTPProvider(**limits).stream(
        descriptor, [{"role": "user", "content": "Hello"}], credential=SECRET, cancel=cancel)]


def body(error):
    return json.dumps({"error": error, "request_id": SECRET}).encode()


def assert_redacted(error):
    assert SECRET not in json.dumps(error.as_dict())
    assert "upstream message" not in str(error)
    assert error.__cause__ is None


@pytest.mark.parametrize("status,code", [(401, "invalid_api_key"), (429, "rate_limited"),
                                         (502, "provider_protocol"), (503, "provider_incomplete")])
async def test_rejected_http_preserves_only_public_provider_code(status, code):
    raw = body({"code": code, "message": "upstream message " + SECRET, "details": {"secret": SECRET}})
    async with error_wire(status, [raw[:17], raw[17:]]) as (endpoint, _, _, requests):
        with pytest.raises(CoreError) as caught:
            await collect(endpoint)
    assert caught.value.code == "provider_http"
    assert caught.value.details == {"status": status, "retryable": status != 401, "provider_code": code}
    assert b"Authorization: Bearer " + SECRET.encode() in requests[0][0]
    assert_redacted(caught.value)


@pytest.mark.parametrize("remote,expected", [
    ({"code": None, "type": "insufficient_quota"}, "insufficient_quota"),
    ({"code": SECRET, "type": "authentication_error"}, "authentication_error"),
    ({"code": "provider_protocol", "type": "invalid_request_error"}, "provider_protocol"),
    ({"code": 401}, "authentication_error"), ({"code": 429}, "rate_limited"),
    ({"code": 503}, "overloaded_error"),
    ({"code": "provider_auth"}, "provider_auth"),
    ({"code": "provider_rate_limit"}, "provider_rate_limit"),
    ({"code": "provider_not_found"}, "provider_not_found"),
    ({"code": "provider_request"}, "provider_request"),
    ({"type": "permission_error"}, "permission_error"),
    ({"type": "rate_limit_error"}, "rate_limit_error"),
])
@pytest.mark.parametrize("sse", [False, True])
async def test_safe_code_type_and_numeric_categories_are_normalized(remote, expected, sse):
    remote["message"] = SECRET
    raw = body(remote)
    if sse:
        raw = b"data: " + raw + b"\n\n"
    async with error_wire(200 if sse else 400, [raw]) as (endpoint, *_):
        with pytest.raises(CoreError) as caught:
            await collect(endpoint)
    assert caught.value.code == ("provider_error" if sse else "provider_http")
    assert caught.value.details.get("provider_code") == expected
    assert_redacted(caught.value)


@pytest.mark.parametrize("remote", [
    SECRET, None, [], {}, {"message": SECRET}, {"code": SECRET},
    {"code": "unknown_but_syntactically_safe_secret"}, {"code": "provider_protocol "},
    {"code": "provider_protocol\n"}, {"code": "PROVIDER_PROTOCOL"},
    {"code": ["provider_protocol"]}, {"code": {"value": "provider_protocol"}},
    {"code": True}, {"code": 429.0}, {"code": 987654321},
    {"type": SECRET}, {"details": {"code": "provider_protocol"}},
])
@pytest.mark.parametrize("sse", [False, True])
async def test_remote_strings_and_unknown_shapes_never_become_diagnostics(remote, sse):
    raw = body(remote)
    if sse:
        raw = b"data: " + raw + b"\n\n"
    async with error_wire(200 if sse else 400, [raw]) as (endpoint, *_):
        with pytest.raises(CoreError) as caught:
            await collect(endpoint)
    assert caught.value.code == ("provider_error" if sse else "provider_http")
    assert "provider_code" not in caught.value.details
    assert_redacted(caught.value)


@pytest.mark.parametrize("raw", [
    b"", b"<html>private upstream message</html>", b"[1,2]", b"null", b"{", b"\xff",
    b'{"error":{"code":"provider_protocol","code":"invalid_api_key"}}',
    b'{"error":{"code":"provider_protocol"},"unexpected":NaN}',
    b'{"error":{"code":"provider_protocol"}} trailing secret',
    b"[" * 2000 + b"0" + b"]" * 2000,
])
async def test_malformed_non_200_body_keeps_original_http_status(raw):
    async with error_wire(403, [raw]) as (endpoint, *_):
        with pytest.raises(CoreError) as caught:
            await collect(endpoint)
    assert caught.value.code == "provider_http"
    assert caught.value.details == {"status": 403, "retryable": False}
    assert_redacted(caught.value)


async def test_exact_byte_bound_accepts_complete_body_and_oversize_closes_socket():
    raw = body({"code": "provider_protocol", "message": SECRET})
    async with error_wire(502, [raw]) as (endpoint, *_):
        with pytest.raises(CoreError) as caught:
            await collect(endpoint, max_event_bytes=len(raw))
    assert caught.value.details["provider_code"] == "provider_protocol"
    async with error_wire(502, [raw, b" "], hold_open=True) as (endpoint, _, closed, _):
        with pytest.raises(CoreError) as caught:
            await asyncio.wait_for(collect(endpoint, max_event_bytes=len(raw)), 1)
        await asyncio.wait_for(closed.wait(), 1)
    assert caught.value.details == {"status": 502, "retryable": True}
    assert_redacted(caught.value)


async def test_incomplete_http_error_body_preserves_status_without_transport_details():
    async with error_wire(502, [b'{"error":'], declared_length=500) as (endpoint, *_):
        with pytest.raises(CoreError) as caught:
            await collect(endpoint)
    assert caught.value.code == "provider_http"
    assert caught.value.details == {"status": 502, "retryable": True}
    assert_redacted(caught.value)


@pytest.mark.parametrize("cancel_kind", ["event", "task"])
async def test_non_200_stalled_body_is_cancellable_and_releases_socket(cancel_kind):
    cancel = asyncio.Event()
    async with error_wire(429, [b'{"error":'], hold_open=True) as (endpoint, accepted, closed, _):
        pending = asyncio.create_task(collect(endpoint, cancel=cancel))
        await asyncio.wait_for(accepted.wait(), 1)
        if cancel_kind == "event":
            cancel.set()
            with pytest.raises(CoreError) as caught:
                await asyncio.wait_for(pending, 1)
            assert caught.value.code == "cancelled"
        else:
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        await asyncio.wait_for(closed.wait(), 1)


async def test_non_200_body_read_has_total_deadline_and_preserves_status():
    async with error_wire(503, [], hold_open=True) as (endpoint, _, closed, _):
        started = time.monotonic()
        with pytest.raises(CoreError) as caught:
            await collect(endpoint, timeout=0.3)
        assert time.monotonic() - started < 1
        await asyncio.wait_for(closed.wait(), 1)
    assert caught.value.code == "provider_http"
    assert caught.value.details == {"status": 503, "retryable": True}
    assert_redacted(caught.value)


async def test_sse_error_after_text_retains_visible_output_but_not_remote_message():
    text = b'data: {"choices":[{"delta":{"content":"visible answer"}}]}\n\n'
    raw = b"data: " + body({"code": "account_reauth_required", "message": SECRET}) + b"\n\n"
    output = []
    async with error_wire(200, [text, raw]) as (endpoint, *_):
        descriptor = {"endpoint": endpoint, "model": "test", "authentication": "none", "output_limit": 64}
        with pytest.raises(CoreError) as caught:
            async for item in HTTPProvider().stream(descriptor, []):
                output.append(item)
    assert [(item.kind, item.data) for item in output] == [("text", {"text": "visible answer"})]
    assert caught.value.code == "provider_error"
    assert caught.value.details == {"provider_code": "account_reauth_required"}
    assert_redacted(caught.value)
