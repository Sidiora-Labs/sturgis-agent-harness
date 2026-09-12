"""Bounded HTTP with DNS decisions pinned to the actual socket destination."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import ipaddress
import math
import socket
import ssl
from urllib.parse import urljoin

import httpx

from agent.protocol import CoreError, strict_loads


def origin(value: str) -> str:
    """Return a canonical origin, rejecting credentials and ambiguous URL syntax."""
    try:
        if not isinstance(value, str) or not value or len(value) > 8192 or any(
                ord(c) <= 32 or c == "\\" for c in value):
            raise ValueError()
        url = httpx.URL(value)
        if url.scheme not in {"http", "https", "ws", "wss"} or not url.host or url.userinfo or url.fragment or "%" in url.host:
            raise ValueError()
        return str(url.copy_with(path="", query=None, fragment=None)).rstrip("/")
    except (ValueError, TypeError, httpx.InvalidURL):
        raise CoreError("invalid_url", "Expected an absolute HTTP or websocket URL without credentials", effects="none") from None


def exact_origin(value: str) -> str:
    result = origin(value)
    url = httpx.URL(value)
    if url.path not in {"", "/"} or url.query:
        raise CoreError("invalid_origin", "An origin cannot include a path or query", effects="none")
    return result


@dataclass(frozen=True)
class HTTPResult:
    status: int
    url: str
    headers: dict
    body: bytes
    redirects: tuple[str, ...]

    def json(self):
        try:
            return strict_loads(self.body.decode("utf-8"))
        except (UnicodeError, CoreError):
            raise CoreError("invalid_remote_response", "Remote response is not strict UTF-8 JSON") from None


class HTTPStream:
    def __init__(self, response, url, redirects, limit):
        self.status, self.url = response.status_code, url
        self.headers, self.redirects = dict(response.headers), tuple(redirects)
        self._response, self._limit, self._consumed = response, limit, False

    async def aiter_bytes(self):
        if self._consumed:
            raise CoreError("stream_consumed", "Response stream can only be consumed once")
        self._consumed, total = True, 0
        async for chunk in self._response.aiter_bytes():
            total += len(chunk)
            if total > self._limit:
                raise CoreError("response_too_large", "Remote response exceeded the byte limit", effects="unknown")
            yield chunk

    async def read(self):
        return b"".join([chunk async for chunk in self.aiter_bytes()])


class NetworkPolicy:
    def __init__(self, *, private_origins=(), max_bytes=8388608, timeout=30,
                 max_redirects=3, max_concurrency=4, ssl_context: ssl.SSLContext | None = None):
        if (type(max_bytes) is not int or not 1 <= max_bytes <= 67108864
                or type(max_redirects) is not int or not 0 <= max_redirects <= 10
                or type(max_concurrency) is not int or not 1 <= max_concurrency <= 64
                or isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or not 0 < timeout <= 300):
            raise CoreError("invalid_network_policy", "Invalid operator network limits")
        self.private_origins = frozenset(exact_origin(item) for item in private_origins)
        self.max_bytes, self.timeout, self.max_redirects = max_bytes, timeout, max_redirects
        self._slots = asyncio.Semaphore(max_concurrency)
        self._ssl = ssl_context if ssl_context is not None else True

    async def _destination(self, url, allowed):
        target = origin(str(url))
        if url.scheme not in {"http", "https"} or target not in allowed:
            raise CoreError("network_origin_denied", "Destination origin is not granted", effects="none")
        try:
            addresses = [ipaddress.ip_address(url.host)]
        except ValueError:
            try:
                rows = await asyncio.get_running_loop().getaddrinfo(url.host, url.port or (443 if url.scheme == "https" else 80),
                    type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
                addresses = list(dict.fromkeys(ipaddress.ip_address(row[4][0]) for row in rows))
            except (OSError, ValueError):
                raise CoreError("dns_failed", "Destination could not be resolved", effects="none") from None
        if not addresses:
            raise CoreError("dns_failed", "Destination resolved to no addresses", effects="none")
        for address in addresses:
            address = getattr(address, "ipv4_mapped", None) or address
            if address.is_unspecified or address.is_multicast or (not address.is_global and target not in self.private_origins):
                raise CoreError("network_address_denied", "Destination address is outside operator policy", effects="none")
        # No resolver is consulted again: HTTP transport receives only this literal IP.
        return url.copy_with(host=str(sorted(addresses, key=lambda item: (item.version, str(item)))[0]))

    @asynccontextmanager
    async def stream(self, method, url, *, allowed_origins, headers=None, content=None,
                     params=None, max_bytes=None, timeout=None, follow_redirects=True):
        limit = self.max_bytes if max_bytes is None else max_bytes
        deadline = self.timeout if timeout is None else timeout
        if (type(follow_redirects) is not bool or type(limit) is not int or not 0 < limit <= self.max_bytes
                or isinstance(deadline, bool) or not isinstance(deadline, (int, float))
                or not math.isfinite(deadline) or not 0 < deadline <= self.timeout):
            raise CoreError("invalid_network_limits", "Request cannot exceed operator network limits", effects="none")
        if method not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}:
            raise CoreError("invalid_method", "Unsupported HTTP method", effects="none")
        if content is not None and (not isinstance(content, bytes) or len(content) > self.max_bytes):
            raise CoreError("request_too_large", "Request body must be bounded bytes", effects="none")
        if not isinstance(allowed_origins, (list, tuple, set, frozenset)) or not 1 <= len(allowed_origins) <= 32:
            raise CoreError("invalid_origin", "One to 32 explicit origins are required", effects="none")
        allowed = frozenset(exact_origin(item) for item in allowed_origins)
        origin(url)
        current = httpx.URL(url, params=params) if params is not None else httpx.URL(url)
        outgoing = dict(headers or {})
        if (len(outgoing) > 64 or any(not isinstance(k, str) or not isinstance(v, str)
                or not k or len(k) > 128 or len(v) > 8192
                or any(ord(c) < 32 or ord(c) > 126 for c in k + v)
                or k.lower() in {"host", "connection", "content-length", "transfer-encoding", "proxy-authorization"}
                for k, v in outgoing.items())):
            raise CoreError("invalid_headers", "Invalid or transport-owned HTTP headers", effects="none")
        redirects = []
        try:
            async with asyncio.timeout(deadline), self._slots:
                while True:
                    pinned = await self._destination(current, allowed)
                    hop_headers = {"Accept-Encoding": "identity", **outgoing, "Host": current.netloc.decode("ascii")}
                    # A new pool for each hop prevents TLS sessions being reused for
                    # another original hostname that happens to share the same IP.
                    async with httpx.AsyncClient(verify=self._ssl, trust_env=False, timeout=deadline,
                                                  follow_redirects=False) as client:
                        async with client.stream(method, pinned, headers=hop_headers, content=content,
                                extensions={"sni_hostname": current.host}) as response:
                            if follow_redirects and response.status_code in {301, 302, 303, 307, 308}:
                                if len(redirects) >= self.max_redirects or method not in {"GET", "HEAD"}:
                                    raise CoreError("redirect_denied", "Redirect limit or method policy rejected the response", effects="unknown")
                                location = response.headers.get("location")
                                if not location:
                                    raise CoreError("invalid_redirect", "Redirect has no destination", effects="unknown")
                                next_url = urljoin(str(current), location)
                                next_origin = origin(next_url)
                                if next_origin != origin(str(current)):
                                    outgoing = {k: v for k, v in outgoing.items() if k.lower() in {"accept", "user-agent"}}
                                redirects.append(str(current))
                                current = httpx.URL(next_url)
                                continue
                            length = response.headers.get("content-length")
                            if response.headers.get("content-encoding", "identity").lower() != "identity":
                                raise CoreError("unsupported_encoding", "Compressed HTTP transfer bodies are not accepted", effects="unknown")
                            if length and (not length.isdigit() or int(length) > limit):
                                raise CoreError("response_too_large", "Remote response exceeds the byte limit", effects="unknown")
                            yield HTTPStream(response, str(current), redirects, limit)
                            return
        except (TimeoutError, httpx.TimeoutException):
            raise CoreError("timeout", "Network operation exceeded its deadline", effects="unknown") from None
        except httpx.HTTPError:
            raise CoreError("network_transport", "Network transport failed", effects="unknown") from None

    async def request(self, method, url, **kwargs):
        async with self.stream(method, url, **kwargs) as response:
            return HTTPResult(response.status, response.url, response.headers, await response.read(), response.redirects)
