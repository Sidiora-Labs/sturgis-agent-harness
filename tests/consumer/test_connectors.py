"""Real wire providers, device exchanges and Chromium renderer qualification."""
import asyncio
import base64
from contextlib import asynccontextmanager
import hashlib
import hmac
import io
from pathlib import Path
import subprocess
import time
import wave

import pytest

from agent.protocol import CoreError, ToolCall, canonical_json, strict_loads
from consumer.browser import BrowserBackend, CDPPipe
from consumer.connectors import install_connectors, MCP_VERSION
from consumer.device import DeviceService, device_signing_bytes
from consumer.network import NetworkPolicy
from .test_connections import wire, services, admit_work

CHROME = subprocess.check_output(
    ["node", "--input-type=module", "-e", "import { chromium } from 'playwright'; console.log(chromium.executablePath())"],
    cwd=Path(__file__).resolve().parents[2], text=True,
).strip()
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a6m8AAAAASUVORK5CYII=")


async def reply(writer, value, *, status=200, headers=None):
    data = value if isinstance(value, bytes) else canonical_json(value).encode()
    # wire() serves one exchange per socket; do not advertise HTTP/1.1 reuse.
    metadata = {"Content-Type": "application/json", "Content-Length": str(len(data)), **(headers or {}), "Connection": "close"}
    writer.write((f"HTTP/1.1 {status} Response\r\n" + "".join(f"{k}: {v}\r\n" for k, v in metadata.items()) + "\r\n").encode() + data)
    await writer.drain()


async def connect(service, kind, endpoint, operation, *, options=None, credential_ref=None, name="remote"):
    row = service.configure(name, kind=kind, endpoint=endpoint, options=options, credential_ref=credential_ref)
    row = service.grant(name, [operation], expected_revision=row["revision"])
    return await service.probe(name, expected_revision=row["revision"])


async def invoke(core, service, row, operation, arguments, *, cancel=None):
    work = admit_work(core, service.capabilities())
    assert work is not None
    return await core.tools.invoke(ToolCall(f'connection.{row["id"]}.{operation}', arguments, str(row["revision"])),
        scope=core.scope, work_id=work.id, generation=1, cancel=cancel)


async def test_real_search_schema_sources_and_registration(tmp_path):
    async def handler(writer, request):
        await reply(writer, {"results": [{"title": "Reference", "url": "https://example.test/source", "snippet": "Observed text"}]})
    async with wire(handler) as (url, requests), services(tmp_path, NetworkPolicy(private_origins=[url])) as (core, vault, connections):
        suite = install_connectors(connections)
        reference = vault.put(core.scope, "key", "provider-key")["reference"]
        row = await connect(connections, "search", url, "query", options={"max_results": 2}, credential_ref=reference)
        assert row["status"] == "ready"
        result = await invoke(core, connections, row, "query", {"query": "actual query"})
        assert result.outcome == "success" and result.data["trust"] == "external_evidence"
        assert result.data["results"][0]["url"] == "https://example.test/source"
        assert requests[1]["path"] == "/?q=actual+query&limit=2"
        assert requests[1]["headers"]["authorization"] == "Bearer provider-key"
        assert suite.core is core


@pytest.mark.parametrize("payload", [{"results": [None]}, {"results": [{}]}, {"results": "bad"},
    {"results": [{"title": 2, "url": "https://example.test", "snippet": "x"}]},
    {"results": [{"title": "x", "url": "ws://example.test", "snippet": "x"}]}])
async def test_bad_search_evidence_cannot_be_ready(tmp_path, payload):
    async def handler(writer, request): await reply(writer, payload)
    async with wire(handler) as (url, _), services(tmp_path, NetworkPolicy(private_origins=[url])) as (_, _, connections):
        install_connectors(connections)
        row = await connect(connections, "search", url, "query")
        assert row["status"] == "unavailable"


@pytest.mark.parametrize("kind,options", [("search", {"max_results": 99}), ("tts", {"model": ""}),
    ("mcp", {"allowed_tools": []}), ("device", {"device_id": "desk", "operations": {}}),
    ("device", {"device_id": "desk", "operations": {"write": []}}),
    ("device", {"device_id": "desk", "operations": {"write": {"$ref": "https://schema.test"}}})])
async def test_adapter_option_bounds(tmp_path, kind, options):
    async with services(tmp_path, NetworkPolicy()) as (_, _, connections):
        install_connectors(connections); DeviceService(connections)
        with pytest.raises(CoreError): connections.configure("remote", kind=kind, endpoint="https://example.test", options=options)


@asynccontextmanager
async def mcp_wire(*, sse=False):
    state = {"schema": {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"], "additionalProperties": False},
        "mode": "ok", "calls": [], "methods": []}
    async def handler(writer, request):
        if request["method"] == "DELETE":
            await reply(writer, b"", status=204); return
        body = strict_loads(request["body"])
        method = body["method"]; state["methods"].append(method)
        if method == "notifications/initialized":
            assert request["headers"]["mcp-session-id"] == "fixture-session"
            await reply(writer, b"", status=202); return
        if method == "initialize":
            assert body["params"]["protocolVersion"] == MCP_VERSION
            result = {"protocolVersion": MCP_VERSION if state["mode"] != "version" else "unsupported", "capabilities": {"tools": {}}, "serverInfo": {"name": "Wire MCP", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [{"name": "double", "inputSchema": state["schema"]}]}
            if state["mode"] == "cursor": result["nextCursor"] = "more"
            if state["mode"] == "declaration": result["tools"] = [{}]
        else:
            state["calls"].append(body)
            result = {"content": [{"type": "text", "text": str(body["params"]["arguments"]["value"] * 2)}], "isError": state["mode"] == "target_error"}
            if state["mode"] == "bad_result": result = {"content": "invalid"}
        response = {"jsonrpc": "2.0", "id": "wrong" if state["mode"] == "id" else body["id"], "result": result}
        if state["mode"] == "remote_error": response = {"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32603}}
        headers = {"Mcp-Session-Id": "fixture-session"}
        if sse:
            headers["Content-Type"] = "text/event-stream"
            response = ("event: message\ndata: " + canonical_json(response) + "\n\n").encode()
        await reply(writer, response, headers=headers)
    async with wire(handler) as (url, requests): yield url, state, requests


@pytest.mark.parametrize("sse", [False, True])
async def test_mcp_real_initialize_catalog_schema_and_call(tmp_path, sse):
    async with mcp_wire(sse=sse) as (url, state, requests), services(tmp_path, NetworkPolicy(private_origins=[url])) as (core, _, connections):
        suite = install_connectors(connections)
        row = await connect(connections, "mcp", url, "call", options={"allowed_tools": ["double"]})
        assert row["status"] == "ready" and row["probe"]["tools"] == ["double"]
        result = await invoke(core, connections, row, "call", {"name": "double", "arguments": {"value": 6}})
        assert result.data["result"]["content"][0]["text"] == "12"
        assert result.effects["certainty"] == "unknown"
        assert state["methods"] == ["initialize", "notifications/initialized", "tools/list"] + ["initialize", "notifications/initialized", "tools/list", "tools/call"]
        with pytest.raises(CoreError, match="Invalid remote tool arguments"):
            await suite.mcp_call({"name": "double", "arguments": {"value": "bad"}}, None, row, None)
        with pytest.raises(CoreError, match="outside the connection grant"):
            await suite.mcp_call({"name": "other", "arguments": {}}, None, row, None)
        assert len(state["calls"]) == 1
        state["schema"]["properties"]["value"]["minimum"] = 5
        with pytest.raises(CoreError, match="catalog changed"):
            await suite.mcp_call({"name": "double", "arguments": {"value": 8}}, None, row, None)
        assert len(state["calls"]) == 1 and any(r["method"] == "DELETE" for r in requests)


@pytest.mark.parametrize("mode", ["version", "cursor", "declaration", "id", "remote_error"])
async def test_mcp_invalid_negotiation_and_response_rejected(tmp_path, mode):
    async with mcp_wire() as (url, state, _), services(tmp_path, NetworkPolicy(private_origins=[url])) as (_, _, connections):
        install_connectors(connections); state["mode"] = mode
        row = await connect(connections, "mcp", url, "call", options={"allowed_tools": ["double"]})
        assert row["status"] == "unavailable" and state["calls"] == []


async def test_mcp_grants_and_actual_remote_target_errors(tmp_path):
    async with mcp_wire() as (url, state, _), services(tmp_path, NetworkPolicy(private_origins=[url])) as (_, _, connections):
        suite = install_connectors(connections)
        missing = await connect(connections, "mcp", url, "call", options={"allowed_tools": ["absent"]}, name="missing")
        assert missing["status"] == "unavailable"
        absent = await connect(connections, "mcp", url, "call", name="ungranted")
        assert absent["status"] == "unavailable"
        row = await connect(connections, "mcp", url, "call", options={"allowed_tools": ["double"]})
        state["mode"] = "target_error"
        assert (await suite.mcp_call({"name": "double", "arguments": {"value": 1}}, None, row, None)).outcome == "target_error"
        state["mode"] = "bad_result"
        with pytest.raises(CoreError, match="invalid result"):
            await suite.mcp_call({"name": "double", "arguments": {"value": 1}}, None, row, None)


@pytest.mark.parametrize("remote_schema", [{"type": "string", "pattern": "(a+)+$"}, {"$ref": "https://schema.test"},
    {"type": "object", "properties": {str(i): {"type": "string"} for i in range(300)}}])
async def test_remote_mcp_validator_programs_are_bounded(tmp_path, remote_schema):
    async with mcp_wire() as (url, state, _), services(tmp_path, NetworkPolicy(private_origins=[url])) as (_, _, connections):
        suite = install_connectors(connections); state["schema"] = remote_schema
        row = await connect(connections, "mcp", url, "call", options={"allowed_tools": ["double"]})
        assert row["status"] == "unavailable" and row["probe"]["error"]["code"] == "unsupported_schema"
        with pytest.raises(CoreError, match="validation byte bound"):
            await suite.mcp_call({"name": "double", "arguments": {"x": "x" * 65536}}, None, row, None)
        assert state["calls"] == []


async def test_mcp_schema_keyword_named_parameters_are_plain_properties(tmp_path):
    async with mcp_wire() as (url, state, _), services(tmp_path, NetworkPolicy(private_origins=[url])) as (_, _, connections):
        install_connectors(connections)
        state["schema"] = {"type": "object", "properties": {"pattern": {"type": "string"}, "values": {"type": "array", "items": {"type": "integer"}}}}
        assert (await connect(connections, "mcp", url, "call", options={"allowed_tools": ["double"]}))["status"] == "ready"


def wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(8000); out.writeframes(b"\0\0" * 80)
    return buffer.getvalue()


async def test_media_wire_transcription_audio_image_and_artifact_scope(tmp_path):
    audio = wav_bytes()
    async def handler(writer, request):
        if request["path"].endswith("/models"): await reply(writer, {"data": [{"id": "wire-model"}]})
        elif request["path"].endswith("/audio/transcriptions"):
            assert audio in request["body"] and b"wire-model" in request["body"]
            assert request["headers"]["content-type"].startswith("multipart/form-data; boundary=")
            await reply(writer, {"text": "Wire transcription"})
        elif request["path"].endswith("/audio/speech"):
            assert strict_loads(request["body"])["input"] == "Speak this"
            await reply(writer, audio, headers={"Content-Type": "audio/wav"})
        else:
            assert strict_loads(request["body"])["prompt"] == "A small image"
            await reply(writer, {"data": [{"b64_json": base64.b64encode(PNG).decode()}]})
    async with wire(handler) as (url, requests), services(tmp_path, NetworkPolicy(private_origins=[url])) as (core, _, connections):
        suite = install_connectors(connections)
        rows = {}
        for kind, operation in [("stt", "transcribe"), ("tts", "speak"), ("image", "generate")]:
            rows[kind] = await connect(connections, kind, url + "/v1", operation, options={"model": "wire-model"}, name=kind)
            assert rows[kind]["probe"]["operation_verified"] is False
        artifact = core.artifacts.put(core.scope, audio, media_type="audio/wav")
        transcript = await invoke(core, connections, rows["stt"], "transcribe", {"artifact_id": artifact.id})
        assert transcript.data["text"] == "Wire transcription"
        speech = await invoke(core, connections, rows["tts"], "speak", {"text": "Speak this"})
        assert core.artifacts.get(core.scope, speech.data["artifact_id"]) == audio
        image = await invoke(core, connections, rows["image"], "generate", {"prompt": "A small image"})
        assert core.artifacts.get(core.scope, image.data["artifact_id"]) == PNG
        with pytest.raises(CoreError): core.artifacts.get("another-agent", image.data["artifact_id"])
        empty = core.artifacts.put(core.scope, b"", media_type="audio/wav")
        with pytest.raises(CoreError, match="Audio artifact is empty"):
            await suite.transcribe({"artifact_id": empty.id}, None, rows["stt"], None)
        assert len(requests) == 6


@pytest.mark.parametrize("kind,operation,payload", [("stt", "transcribe", {"text": []}), ("tts", "speak", b"not wav"),
    ("image", "generate", {"data": []}), ("image", "generate", {"data": [{"b64_json": "not base64"}]})])
async def test_media_invalid_wire_output_never_becomes_artifact(tmp_path, kind, operation, payload):
    async def handler(writer, request):
        await reply(writer, {"data": [{"id": "wire-model"}]} if request["path"].endswith("/models") else payload)
    async with wire(handler) as (url, _), services(tmp_path, NetworkPolicy(private_origins=[url])) as (core, _, connections):
        suite = install_connectors(connections)
        row = await connect(connections, kind, url, operation, options={"model": "wire-model"})
        artifact = core.artifacts.put(core.scope, wav_bytes(), media_type="audio/wav")
        args = {"artifact_id": artifact.id} if kind == "stt" else {"text": "speak"} if kind == "tts" else {"prompt": "image"}
        result = await invoke(core, connections, row, operation, args)
        assert result.outcome == "execution_error" and result.error["code"] == "invalid_remote_response"


@pytest.mark.parametrize("payload,status", [({}, 200), ({"data": []}, 200), ({}, 503)])
async def test_media_probe_does_not_invent_available_models(tmp_path, payload, status):
    async def handler(writer, request): await reply(writer, payload, status=status)
    async with wire(handler) as (url, _), services(tmp_path, NetworkPolicy(private_origins=[url])) as (_, _, connections):
        install_connectors(connections)
        assert (await connect(connections, "tts", url, "speak", options={"model": "missing"}))["status"] == "unavailable"


def signed(secret, action, row, nonce, body=b"", timestamp=None):
    timestamp = int(time.time()) if timestamp is None else timestamp
    return {"timestamp": timestamp, "nonce": nonce, "signature": hmac.new(secret.encode(),
        device_signing_bytes(action, row["id"], row["revision"], timestamp, nonce, body), hashlib.sha256).hexdigest()}


async def paired(connections, devices, *, name="desk"):
    row = connections.configure(name, kind="device", endpoint="https://sturgis.example/device",
        options={"device_id": name, "operations": {"write_note": {"type": "object", "properties": {"text": {"type": "string", "maxLength": 100}},
            "required": ["text"], "additionalProperties": False}}})
    offer = devices.pair(name, expected_revision=row["revision"])
    claim = devices.claim(name, pairing_token=offer["pairing_token"], device_id=name)
    row = connections.grant(name, ["request"], expected_revision=claim["revision"])
    row = await connections.probe(name, expected_revision=row["revision"])
    return row, claim["secret"], offer


async def test_device_pairing_wire_poll_actual_action_and_signed_result(tmp_path):
    async with services(tmp_path, NetworkPolicy(timeout=5)) as (core, _, connections):
        devices = DeviceService(connections)
        row, secret, offer = await paired(connections, devices)
        assert devices.get("desk")["paired"] and devices.list()[0]["status"] == "ready"
        sync = devices.sync("desk", **signed(secret, "sync", {**row, "revision": 0}, "discover"))
        assert sync["revision"] == row["revision"] and sync["status"] == "ready"
        assert secret not in canonical_json(devices.list())
        with pytest.raises(CoreError, match="invalid or expired"):
            devices.claim("desk", pairing_token=offer["pairing_token"], device_id="desk")
        action_file = tmp_path / "physical-device-note.txt"
        async def endpoint(writer, request):
            envelope = strict_loads(request["body"])
            try:
                if request["path"] == "/poll":
                    value = devices.poll("desk", **envelope)
                else:
                    body = base64.b64decode(envelope.pop("body"))
                    value = devices.respond("desk", body, **envelope)
                await reply(writer, value)
            except CoreError as exc: await reply(writer, {"error": exc.code}, status=400)
        async with wire(endpoint) as (url, _):
            network = NetworkPolicy(private_origins=[url])
            task = asyncio.create_task(invoke(core, connections, row, "request", {"operation": "write_note", "arguments": {"text": "actual device result"}}))
            received = None
            for index in range(100):
                response = await network.request("POST", url + "/poll", allowed_origins=[url],
                    content=canonical_json(signed(secret, "poll", row, "poll" + str(index))).encode())
                requests = response.json()["requests"]
                if requests: received = requests[0]; break
                await asyncio.sleep(.02)
            assert received is not None and received["state"] == "offered"
            action_file.write_text(received["arguments"]["text"])
            result = canonical_json({"request_id": received["request_id"], "status": "success", "data": {"sha256": hashlib.sha256(action_file.read_bytes()).hexdigest()}}).encode()
            envelope = {**signed(secret, "respond", row, "answer", result), "body": base64.b64encode(result).decode()}
            response = await network.request("POST", url + "/respond", allowed_origins=[url], content=canonical_json(envelope).encode())
            receipt = response.json()
            assert receipt["state"] == "responded"
            completed = await asyncio.wait_for(task, 3)
            assert completed.outcome == "success" and completed.effects["certainty"] == "unknown"
            assert completed.data["evidence_event_id"] == receipt["event_id"]
            assert action_file.read_text() == "actual device result"
            assert devices.respond("desk", result, **signed(secret, "respond", row, "answer", result, envelope["timestamp"])) == receipt
            changed = canonical_json({"request_id": received["request_id"], "status": "success", "data": {"changed": True}}).encode()
            with pytest.raises(CoreError, match="changed an existing"):
                devices.respond("desk", changed, **signed(secret, "respond", row, "changed", changed))
            with pytest.raises(CoreError, match="nonce was reused"):
                devices.poll("desk", **signed(secret, "poll", row, "answer"))
        assert devices.revoke("desk", expected_revision=row["revision"])["status"] == "revoked"
        with pytest.raises(CoreError): devices.poll("desk", **signed(secret, "poll", row, "afterrevoke"))


async def test_device_cancellation_late_evidence_and_schema_denial(tmp_path):
    async with services(tmp_path, NetworkPolicy(timeout=3)) as (core, _, connections):
        devices = DeviceService(connections)
        row, secret, _ = await paired(connections, devices)
        denied = await invoke(core, connections, row, "request", {"operation": "other", "arguments": {}})
        assert denied.error["code"] == "device_operation_denied"
        invalid = await invoke(core, connections, row, "request", {"operation": "write_note", "arguments": {"text": 2}})
        assert invalid.error["code"] == "validation"
        cancel = asyncio.Event()
        task = asyncio.create_task(invoke(core, connections, row, "request", {"operation": "write_note", "arguments": {"text": "cancel"}}, cancel=cancel))
        received = None
        for index in range(100):
            values = devices.poll("desk", **signed(secret, "poll", row, "cancelpoll" + str(index)))["requests"]
            if values: received = values[0]; break
            await asyncio.sleep(.01)
        assert received
        cancel.set()
        assert (await task).state == "indeterminate"
        polled = devices.poll("desk", **signed(secret, "poll", row, "cancelled"))["requests"][0]
        assert polled["state"] == "cancel_requested"
        body = canonical_json({"request_id": received["request_id"], "status": "cancelled", "data": {}}).encode()
        assert devices.respond("desk", body, **signed(secret, "respond", row, "late", body))["state"] == "late_response"
        assert core.journal.events(core.scope)[-1].payload["late"] is True


@pytest.mark.parametrize("override", [{"timestamp": 0}, {"signature": "bad"}, {"signature": "0" * 64}])
async def test_device_forgery_cannot_poll(tmp_path, override):
    async with services(tmp_path, NetworkPolicy()) as (_, _, connections):
        devices = DeviceService(connections); row, secret, _ = await paired(connections, devices)
        with pytest.raises(CoreError, match="signature"):
            devices.poll("desk", **{**signed(secret, "poll", row, "nonce"), **override})


async def test_device_pairing_revision_identity_and_response_bounds(tmp_path):
    async with services(tmp_path, NetworkPolicy()) as (_, _, connections):
        devices = DeviceService(connections)
        row, secret, offer = await paired(connections, devices)
        with pytest.raises(CoreError): devices.pair("desk", expected_revision=1)
        with pytest.raises(CoreError): devices.pair("desk", expected_revision=row["revision"], ttl=0)
        with pytest.raises(CoreError): devices.claim("desk", pairing_token=None, device_id="desk")
        for data in (b"x" * 262145, canonical_json({"request_id": "missing", "status": "bad", "data": {}}).encode()):
            with pytest.raises(CoreError): devices.respond("desk", data, **signed(secret, "respond", row, "bad", data))
        missing = canonical_json({"request_id": "missing", "status": "success", "data": {}}).encode()
        with pytest.raises(CoreError, match="authorized request"):
            devices.respond("desk", missing, **signed(secret, "respond", row, "missing", missing))
        connections.configure("other", kind="http", endpoint="https://example.test")
        with pytest.raises(CoreError, match="not a paired device"): devices.get("other")


async def test_unavailable_browser_is_an_explicit_readiness_failure(tmp_path):
    async with services(tmp_path, NetworkPolicy()) as (_, _, connections):
        suite = install_connectors(connections)
        row = await connect(connections, "browser", "https://example.test", "inspect")
        assert row["status"] == "unavailable" and row["probe"]["error"]["code"] == "browser_unavailable"
        with pytest.raises(CoreError, match="No contained Chromium"):
            await suite.inspect_browser({}, None, row, None)
        with pytest.raises(CoreError): BrowserBackend("/does-not-exist")


async def test_real_chromium_executes_javascript_and_blocks_ungranted_subresources(tmp_path):
    async with wire() as (forbidden, unexpected):
        async def handler(writer, request):
            if request["path"] == "/script.js":
                await reply(writer, b'document.title="Executed title"; document.body.append("JavaScript executed");', headers={"Content-Type": "application/javascript"})
            elif request["path"] == "/start":
                await reply(writer, b"", status=302, headers={"Location": "/page"})
            else:
                body = ('<html><head><title>Original</title></head><body>Rendered page<script src="/script.js"></script>'
                    '<img src="' + forbidden + '/private"><script>document.body.dataset.worker=typeof Worker;</script></body></html>').encode()
                await reply(writer, body, headers={"Content-Type": "text/html"})
        async with wire(handler) as (url, requests), services(tmp_path, NetworkPolicy(private_origins=[url, forbidden])) as (core, _, connections):
            backend = BrowserBackend(CHROME, timeout=20)
            install_connectors(connections, browser_backend=backend)
            row = await connect(connections, "browser", url + "/start", "inspect")
            assert row["status"] == "ready"
            result = await invoke(core, connections, row, "inspect", {})
            assert result.outcome == "success", result.error
            assert result.data["title"] == "Executed title" and "JavaScript executed" in result.data["text"]
            assert result.data["url"] == url + "/page"
            assert result.data["network"] == "namespace_isolated"
            assert unexpected == [] and any(item["code"] == "network_origin_denied" for item in result.data["blocked"])
            assert core.artifacts.get(core.scope, result.data["screenshot_artifact_id"]).startswith(b"\x89PNG")
            assert {"/start", "/page", "/script.js"} <= {r["path"] for r in requests}


async def test_real_browser_raw_network_has_no_host_access(tmp_path):
    async with wire() as (url, requests):
        backend = BrowserBackend(CHROME, timeout=10)
        async with backend._session() as (pipe, _):
            target = (await pipe.call("Target.createTarget", {"url": "about:blank"}))["targetId"]
            session = (await pipe.call("Target.attachToTarget", {"targetId": target, "flatten": True}))["sessionId"]
            response = await pipe.call("Runtime.evaluate", {"expression": f'fetch({canonical_json(url)}).then(()=>"escaped",()=>"blocked")', "awaitPromise": True, "returnByValue": True}, session=session)
            assert response["result"]["value"] == "blocked"
            assert requests == []
            with pytest.raises(CoreError, match="rejected the CDP command"):
                await pipe.call("Unknown.method")


async def test_browser_prelaunch_origin_denial_and_actual_resident_limit(tmp_path):
    async with services(tmp_path, NetworkPolicy()) as (core, _, _):
        backend = BrowserBackend(CHROME, timeout=10)
        with pytest.raises(CoreError):
            await backend.inspect("file:///etc/passwd", network=NetworkPolicy(), allowed_origins=["https://example.test"], artifact_store=core.artifacts, scope=core.scope)
        with pytest.raises(CoreError, match="origin is not granted"):
            await backend.inspect("https://other.test", network=NetworkPolicy(), allowed_origins=["https://example.test"], artifact_store=core.artifacts, scope=core.scope)
        with pytest.raises(CoreError):
            await BrowserBackend(CHROME, max_resident_bytes=16777216, timeout=5).probe()
        with pytest.raises(CoreError, match="deadline"):
            await BrowserBackend(CHROME, timeout=.001).probe()


def test_browser_diagnostics_only_expose_safe_operator_reason():
    fallback = CoreError("browser_unavailable", "Closed")
    namespace = BrowserBackend._failure(b"bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted\nprivate-log", fallback)
    assert namespace.details["reason"] == "network_namespace_denied"
    memory = BrowserBackend._failure(b"Oilpan: CagedHeap reservation. private-log", fallback)
    assert memory.details["reason"] == "renderer_address_space_limit"
    assert "private-log" not in canonical_json(namespace.as_dict()) + canonical_json(memory.as_dict())
    assert BrowserBackend._failure(b"other diagnostic", fallback) is fallback
