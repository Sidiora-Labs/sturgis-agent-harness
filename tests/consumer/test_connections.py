"""Actual HTTP/TLS, encrypted credentials and durable scheduler/ingress boundaries."""
import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import socket
import ssl
import time

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from agent.protocol import CoreError, ToolCall, canonical_json
from agent.runtime import CoreRuntime
from consumer.connections import ConnectionOperation, ConnectionService
from consumer.network import HTTPResult, NetworkPolicy, exact_origin, origin
from consumer.vault import CredentialVault
from consumer.webhooks import WebhookIngress, signing_bytes


@asynccontextmanager
async def wire(handler=None, *, tls=None):
    requests, tasks = [], set()
    async def serve(reader, writer):
        tasks.add(asyncio.current_task())
        try:
            raw = await reader.readuntil(b"\r\n\r\n")
            lines = raw.decode().split("\r\n")
            method, path, _ = lines[0].split(" ")
            headers = {key.lower(): value.strip() for key, value in (line.split(":", 1) for line in lines[1:] if line)}
            body = await reader.readexactly(int(headers.get("content-length", 0)))
            requests.append({"method": method, "path": path, "headers": headers, "body": body})
            if handler:
                await handler(writer, requests[-1])
            else:
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 11\r\nConnection: close\r\n\r\n{"ok":true}')
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            try: await asyncio.wait_for(writer.wait_closed(), .5)
            except (ConnectionError, ssl.SSLError, TimeoutError): pass
            tasks.discard(asyncio.current_task())
    server = await asyncio.start_server(serve, "127.0.0.1", 0, ssl=tls)
    url = ("https" if tls else "http") + "://127.0.0.1:" + str(server.sockets[0].getsockname()[1])
    try:
        yield url, requests
    finally:
        server.close()
        for task in list(tasks): task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await server.wait_closed()


@asynccontextmanager
async def services(path, policy):
    (path / "workspace").mkdir(exist_ok=True)
    core = CoreRuntime(path / "runtime", scope="agent_test", workspace=path / "workspace")
    vault = CredentialVault(path / "vault")
    try:
        yield core, vault, ConnectionService(core, vault, policy)
    finally:
        await core.close()


def admit_work(core, caps, **kwargs):
    work = core.scheduler.submit(core.scope, "connection-test", {}, capabilities=caps,
        resources={"tool_calls": 10, "elapsed_ms": 300000}, **kwargs)
    return core.scheduler.admit(core.scope, work.id)


async def ready(service, endpoint, *, kind="http", reference=None, connection_id="remote"):
    row = service.configure(connection_id, kind=kind, endpoint=endpoint, credential_ref=reference)
    row = service.grant(connection_id, ["receive" if kind == "webhook" else "fetch"], expected_revision=row["revision"])
    return await service.probe(connection_id, expected_revision=row["revision"])


@pytest.mark.parametrize("url", [None, "", "file:///etc/passwd", "http://a@localhost", "http://localhost/#x", "http://local\\host", "http://localhost/\n", "http://[fe80::1%25eth0]"])
def test_urls_fail_closed(url):
    with pytest.raises(CoreError): origin(url)


def test_origin_and_strict_json():
    assert origin("HTTPS://Example.COM:443/path?q=x") == "https://example.com"
    assert exact_origin("http://example.com/") == "http://example.com"
    with pytest.raises(CoreError): exact_origin("http://example.com/path")
    assert HTTPResult(200, "", {}, b'{"a":1}', ()).json() == {"a": 1}
    for body in [b"\xff", b'{"a":1,"a":2}']:
        with pytest.raises(CoreError, match="strict UTF-8 JSON"): HTTPResult(200, "", {}, body, ()).json()


@pytest.mark.parametrize("options", [{"max_bytes": 0}, {"timeout": float("nan")}, {"max_concurrency": 0}, {"max_redirects": 11}])
def test_operator_limits(options):
    with pytest.raises(CoreError): NetworkPolicy(**options)


@pytest.mark.parametrize("kwargs", [{"max_bytes": 0}, {"timeout": 301}, {"method": "CONNECT"}, {"content": "bad"},
    {"allowed_origins": []}, {"headers": {"Host": "evil"}}, {"headers": {"X-Test": "bad\nvalue"}}])
async def test_request_shape_fails_before_network(kwargs):
    args = {"method": "GET", "url": "http://127.0.0.1", "allowed_origins": ["http://127.0.0.1"], **kwargs}
    with pytest.raises(CoreError): await NetworkPolicy().request(**args)


async def test_actual_private_origin_and_dns_pin(monkeypatch):
    async with wire() as (url, requests):
        with pytest.raises(CoreError, match="outside operator policy"):
            await NetworkPolicy().request("GET", url, allowed_origins=[url])
        assert requests == []
        hostname = url.replace("127.0.0.1", "localhost")
        policy = NetworkPolicy(private_origins=[hostname])
        loop = asyncio.get_running_loop()
        original = loop.getaddrinfo
        calls = []
        async def audited(host, port, **kwargs):
            calls.append(host)
            assert len(calls) == 1, "Transport re-resolved an already validated hostname"
            return await original(host, port, **kwargs)
        monkeypatch.setattr(loop, "getaddrinfo", audited)
        result = await policy.request("GET", hostname, allowed_origins=[hostname], params={"q": "a b"})
        assert result.json() == {"ok": True}
        assert calls == ["localhost"] and requests[0]["headers"]["host"] == hostname.removeprefix("http://")
        assert requests[0]["path"] == "/?q=a+b"
        assert requests[0]["headers"]["accept-encoding"] == "identity"


async def test_dns_errors_and_mixed_addresses(monkeypatch):
    loop = asyncio.get_running_loop()
    async def no_dns(*args, **kwargs): raise socket.gaierror()
    monkeypatch.setattr(loop, "getaddrinfo", no_dns)
    with pytest.raises(CoreError, match="could not be resolved"):
        await NetworkPolicy().request("GET", "https://example.test", allowed_origins=["https://example.test"])
    async def empty(*args, **kwargs): return []
    monkeypatch.setattr(loop, "getaddrinfo", empty)
    with pytest.raises(CoreError, match="no addresses"):
        await NetworkPolicy().request("GET", "https://example.test", allowed_origins=["https://example.test"])
    async def mixed(*args, **kwargs):
        return [(socket.AF_INET, 1, 6, "", (ip, 443)) for ip in ["1.1.1.1", "127.0.0.1"]]
    monkeypatch.setattr(loop, "getaddrinfo", mixed)
    with pytest.raises(CoreError, match="outside operator policy"):
        await NetworkPolicy().request("GET", "https://example.test", allowed_origins=["https://example.test"])


async def test_actual_tls_keeps_hostname_verification_and_sni(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False).sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert_path, key_path)
    names = []
    server.set_servername_callback(lambda sock, hostname, context: names.append(hostname))
    trust = ssl.create_default_context(cafile=str(cert_path))
    async with wire(tls=server) as (url, requests):
        hostname = url.replace("127.0.0.1", "localhost")
        policy = NetworkPolicy(private_origins=[hostname, url], ssl_context=trust)
        assert (await policy.request("GET", hostname, allowed_origins=[hostname])).status == 200
        assert names == ["localhost"] and requests[0]["headers"]["host"].startswith("localhost:")
        with pytest.raises(CoreError, match="transport failed"):
            await policy.request("GET", url, allowed_origins=[url])


async def test_redirects_revalidate_destinations_and_do_not_forward_secrets():
    async with wire() as (target, received):
        async def redirect(writer, request):
            writer.write(("HTTP/1.1 302 Found\r\nLocation: " + target + "/result\r\nContent-Length: 0\r\n\r\n").encode())
            await writer.drain()
        async with wire(redirect) as (start, _):
            policy = NetworkPolicy(private_origins=[start, target])
            with pytest.raises(CoreError, match="not granted"):
                await policy.request("GET", start, allowed_origins=[start])
            assert received == []
            result = await policy.request("GET", start, allowed_origins=[start, target],
                headers={"Authorization": "Bearer secret", "X-API-Key": "secret", "Cookie": "private", "Accept": "application/json"})
            assert result.redirects == (start,) and result.url == target + "/result"
            assert not {"authorization", "x-api-key", "cookie"} & received[0]["headers"].keys()
            assert received[0]["headers"]["accept"] == "application/json"
            with pytest.raises(CoreError, match="Redirect limit"):
                await policy.request("POST", start, allowed_origins=[start, target], content=b"change")
            with pytest.raises(CoreError, match="Redirect limit"):
                await NetworkPolicy(private_origins=[start], max_redirects=0).request("GET", start, allowed_origins=[start])


@pytest.mark.parametrize("response,code", [
    (b"HTTP/1.1 302 Found\r\nContent-Length: 0\r\n\r\n", "invalid_redirect"),
    (b"HTTP/1.1 200 OK\r\nContent-Length: 9999\r\n\r\n", "response_too_large"),
    (b"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nContent-Length: 0\r\n\r\n", "unsupported_encoding"),
    (b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n8\r\n12345678\r\n8\r\n12345678\r\n0\r\n\r\n", "response_too_large")])
async def test_wire_response_bounds(response, code):
    async def reply(writer, request): writer.write(response); await writer.drain()
    async with wire(reply) as (url, _):
        with pytest.raises(CoreError) as error:
            await NetworkPolicy(private_origins=[url]).request("GET", url, allowed_origins=[url], max_bytes=12)
        assert error.value.code == code


async def test_stream_deltas_and_deadline_include_queued_wait():
    sent, release = asyncio.Event(), asyncio.Event()
    async def streaming(writer, request):
        writer.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1\r\nx\r\n")
        await writer.drain()
        sent.set()
        await release.wait()
        writer.write(b"0\r\n\r\n")
        await writer.drain()
    async with wire(streaming) as (url, requests):
        policy = NetworkPolicy(private_origins=[url], max_concurrency=1, timeout=2)
        async with policy.stream("GET", url, allowed_origins=[url]) as response:
            chunks = response.aiter_bytes()
            assert await asyncio.wait_for(anext(chunks), .5) == b"x"
            with pytest.raises(CoreError, match="deadline"):
                await policy.request("GET", url, allowed_origins=[url], timeout=.05)
            assert len(requests) == 1
            release.set()
            assert [part async for part in chunks] == []
            with pytest.raises(CoreError, match="only be consumed once"): await response.read()
        release.clear()
        with pytest.raises(CoreError, match="deadline"):
            await policy.request("GET", url, allowed_origins=[url], timeout=.1)


async def test_connection_lifecycle_exact_grants_scheduler_and_rotation(tmp_path):
    async with wire() as (url, requests), services(tmp_path, NetworkPolicy(private_origins=[url])) as (core, vault, service):
        credential = vault.put(core.scope, "remote", "wire-secret")
        row = await ready(service, url, reference=credential["reference"])
        assert row["status"] == "ready" and row["probe"] == {"ok": True, "kind": "http_response", "status": 200, "revision": 3}
        assert core.capabilities == () and service.capabilities() == ("connection:remote:fetch",)
        assert requests[0]["headers"]["authorization"] == "Bearer wire-secret"
        denied = admit_work(core, [])
        call = ToolCall("connection.remote.fetch", {}, str(row["revision"]))
        result = await core.tools.invoke(call, scope=core.scope, work_id=denied.id, generation=1)
        assert result.state == "rejected" and len(requests) == 1
        work = admit_work(core, service.capabilities())
        result = await core.tools.invoke(call, scope=core.scope, work_id=work.id, generation=1, request_id="fetch-once")
        assert result.outcome == "success" and base64.b64decode(result.data["body"]) == b'{"ok":true}'
        repeated = await core.tools.invoke(call, scope=core.scope, work_id=work.id, generation=1, request_id="fetch-once")
        assert repeated.action_id == result.action_id and len(requests) == 2
        assert len(core.journal.list("work_claims", core.scope)) == 1
        assert "wire-secret" not in canonical_json(service.list())
        rotated = vault.put(core.scope, "remote", "next-secret", expected_revision=1)
        assert service.get("remote")["status"] == "stale_credential" and service.capabilities() == ()
        stale = await core.tools.invoke(call, scope=core.scope, work_id=work.id, generation=1)
        assert stale.state == "rejected" and stale.error["code"] == "stale_connection" and len(requests) == 2
        with pytest.raises(CoreError, match="credential has changed"):
            await service.probe("remote", expected_revision=3)
        updated = service.configure("remote", kind="http", endpoint=url, credential_ref=rotated["reference"], expected_revision=3)
        assert updated["status"] == "ungranted"
        assert service.get("remote")["revision"] == 4
        with pytest.raises(CoreError): service.grant("remote", ["fetch"], expected_revision=3)
        service.grant("remote", ["fetch"], expected_revision=4)
        refreshed = await service.probe("remote", expected_revision=5)
        assert refreshed["status"] == "ready"
        assert service.revoke("remote", expected_revision=6)["status"] == "revoked"
        assert service.capabilities() == () and not any(d["name"].startswith("connection.") for d in core.tools.descriptors())


async def test_delegated_workspace_cannot_use_unconstrained_network(tmp_path):
    async with wire() as (url, requests), services(tmp_path, NetworkPolicy(private_origins=[url])) as (core, _, service):
        row = await ready(service, url)
        work = admit_work(core, service.capabilities(), constraints={"workspace_root": str(tmp_path / "workspace"), "delegated": True})
        result = await core.tools.invoke(ToolCall("connection.remote.fetch", {}, str(row["revision"])), scope=core.scope, work_id=work.id, generation=1)
        assert result.error["code"] == "workspace_incompatible" and len(requests) == 1
        assert core.journal.list("work_claims", core.scope) == []


@pytest.mark.parametrize("options", [{"kind": "http", "endpoint": "http://example.test/?token=secret"},
    {"kind": "http", "endpoint": "ws://example.test"}, {"kind": "http", "endpoint": "http://example.test", "credential_ref": "missing"},
    {"kind": "http", "endpoint": "http://example.test", "options": {"allowed_origins": []}},
    {"kind": "http", "endpoint": "http://example.test", "options": {"allowed_origins": ["https://example.test"]}},
    {"kind": "webhook", "endpoint": "http://example.test", "options": {"max_skew": 901}},
    {"kind": "http", "endpoint": "http://example.test", "options": []}])
async def test_connection_configuration_validation(tmp_path, options):
    async with services(tmp_path, NetworkPolicy()) as (_, _, service):
        with pytest.raises(CoreError): service.configure("remote", **options)
        assert service.list() == []


async def test_unavailable_and_failed_probes(tmp_path):
    async with services(tmp_path, NetworkPolicy()) as (_, _, service):
        with pytest.raises(CoreError, match="does not exist"): service.get("missing")
        service.configure("unknown", kind="uninstalled", endpoint="https://example.test")
        with pytest.raises(CoreError, match="No executable adapter"): await service.probe("unknown", expected_revision=1)
        with pytest.raises(CoreError, match="operations provided"): service.grant("unknown", ["fake"], expected_revision=1)
        service.configure("local", kind="http", endpoint="http://127.0.0.1")
        service.grant("local", ["fetch"], expected_revision=1)
        row = await service.probe("local", expected_revision=2)
        assert row["status"] == "unavailable" and row["probe"]["error"]["code"] == "network_address_denied"
        with pytest.raises(CoreError, match="current active"): await service.probe("local", expected_revision=2)
        service.configure("hook", kind="webhook", endpoint="https://example.test")
        service.grant("hook", ["receive"], expected_revision=1)
        assert (await service.probe("hook", expected_revision=2))["status"] == "unavailable"


async def test_probe_race_cannot_admit_old_config_or_credential(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    async def delayed(writer, request):
        entered.set(); await release.wait()
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"); await writer.drain()
    async with wire(delayed) as (url, _), services(tmp_path, NetworkPolicy(private_origins=[url])) as (core, vault, service):
        cred = vault.put(core.scope, "secret", "first")
        service.configure("remote", kind="http", endpoint=url, credential_ref=cred["reference"])
        service.grant("remote", ["fetch"], expected_revision=1)
        probe = asyncio.create_task(service.probe("remote", expected_revision=2))
        await entered.wait()
        vault.put(core.scope, "secret", "second", expected_revision=1)
        release.set()
        with pytest.raises(CoreError, match="changed during"): await probe
        assert service.get("remote")["revision"] == 2


def signature(row, body, timestamp, nonce, secret="shared-secret"):
    return hmac.new(secret.encode(), signing_bytes(row["id"], row["revision"], timestamp, nonce, body), hashlib.sha256).hexdigest()


async def test_webhook_durable_dedupe_identity_and_no_owner_instruction(tmp_path):
    body = canonical_json({"event_id": "delivery_1", "text": "external evidence", "data": {"owner": "forged"}}).encode()
    timestamp = int(time.time())
    async with services(tmp_path, NetworkPolicy()) as (core, vault, service):
        cred = vault.put(core.scope, "hook", "shared-secret")
        row = await ready(service, "https://example.test", kind="webhook", reference=cred["reference"])
        ingress = WebhookIngress(service)
        event = ingress.admit("remote", body, timestamp=timestamp, nonce="nonce1", signature=signature(row, body, timestamp, "nonce1"))
        assert event.kind == "external.webhook" and event.payload["trust"] == "external_evidence"
        assert event.payload["sender"] == "remote" and event.scope == core.scope
        assert ingress.admit("remote", body, timestamp=timestamp, nonce="nonce1", signature=signature(row, body, timestamp, "nonce1")).id == event.id
        assert ingress.admit("remote", body, timestamp=timestamp, nonce="nonce2", signature=signature(row, body, timestamp, "nonce2")).id == event.id
        assert core.journal.list("work", core.scope) == []
        assert not any(e.kind.startswith("user.") for e in core.journal.events(core.scope))
        different = canonical_json({"event_id": "delivery_2"}).encode()
        with pytest.raises(CoreError, match="nonce was already used"):
            ingress.admit("remote", different, timestamp=timestamp, nonce="nonce1", signature=signature(row, different, timestamp, "nonce1"))
        changed = canonical_json({"event_id": "delivery_1", "text": "changed"}).encode()
        with pytest.raises(CoreError, match="identity was reused"):
            ingress.admit("remote", changed, timestamp=timestamp, nonce="nonce3", signature=signature(row, changed, timestamp, "nonce3"))
        assert core.journal.get("consumer.webhook_nonces", "remote:nonce3", core.scope) is None
    async with services(tmp_path, NetworkPolicy()) as (_, _, service):
        reopened = WebhookIngress(service).admit("remote", body, timestamp=timestamp, nonce="nonce1", signature=signature(row, body, timestamp, "nonce1"))
        assert reopened.id == event.id


@pytest.mark.parametrize("payload,override,code", [
    ({"event_id": "one", "scope": "victim"}, {}, "invalid_request"),
    ({"event_id": "one", "participant": "owner"}, {}, "invalid_request"),
    ({"event_id": "one", "text": 2}, {}, "invalid_webhook"),
    ({"event_id": "one", "data": []}, {}, "invalid_webhook"),
    ({"event_id": "one"}, {"signature": "x" * 64}, "webhook_signature"),
    ({"event_id": "one"}, {"timestamp": 0}, "webhook_expired"),
    ({"event_id": "one"}, {"signature": "bad"}, "invalid_webhook")])
async def test_webhook_rejections_have_no_admission(tmp_path, payload, override, code):
    async with services(tmp_path, NetworkPolicy()) as (core, vault, service):
        cred = vault.put(core.scope, "hook", "shared-secret")
        row = await ready(service, "https://example.test", kind="webhook", reference=cred["reference"])
        body, timestamp = canonical_json(payload).encode(), int(time.time())
        kwargs = {"timestamp": timestamp, "nonce": "new", "signature": signature(row, body, timestamp, "new"), **override}
        with pytest.raises(CoreError) as error: WebhookIngress(service).admit("remote", body, **kwargs)
        assert error.value.code == code
        assert core.journal.list("consumer.webhooks", core.scope) == []
