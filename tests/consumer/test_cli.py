"""Installed CLI, real HTTP/private workers/native memory and private client state."""
import asyncio
import base64
from dataclasses import asdict
import io
import os
from pathlib import Path
import socket
import stat
import sys
import time

import httpx
import pytest

from agent.protocol import CoreError, canonical_json, strict_loads
from consumer.cli import Client, PrivateStore, _params, _private_output, _prompt, _settings, main, parser, redacted
from consumer.contracts import failure, success
from tests.consumer.test_connections import wire
from tests.consumer.test_connectors import reply
from tests.consumer.test_api import TOKEN, PASSWORD
from tests.consumer_integration.test_http import http_installation
from tests.integration.test_continuity import WireModel, proposal

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / ".venv/bin/sturgis"


async def cli(path, url, *arguments, input="", timeout=35, module=False):
    prefix = [sys.executable, "-m", "consumer"] if module else [str(CLI)]
    command = [*prefix, "--config-dir", str(path), "--url", url, "--json", *arguments]
    process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=ROOT)
    try:
        out, err = await asyncio.wait_for(process.communicate(input.encode()), timeout)
    finally:
        if process.returncode is None: process.kill(); await process.wait()
    return process.returncode, out.decode(), err.decode()


def records(output):
    return [strict_loads(line) for line in output.splitlines() if line.strip()]


async def test_installed_cli_http_login_agent_native_worker_model_chat_and_cancel(tmp_path):
    model = WireModel([[proposal("consumer.probe", {})], ["First CLI ", "response"], ["Second response"]])
    server = await asyncio.start_server(model.handle, "127.0.0.1", 0)
    model_origin = "http://127.0.0.1:" + str(server.sockets[0].getsockname()[1])
    config = tmp_path / "client"
    try:
        async with http_installation(tmp_path / "installation", model_origin) as (http, app):
            url = str(http.base_url).rstrip("/")
            code, out, err = await cli(config, url, "login", "--bootstrap", "--email", "cli@example.com", input=PASSWORD + "\n" + TOKEN + "\n")
            assert code == 0, err
            assert records(out)[0]["authenticated"] and PASSWORD not in out + err and TOKEN not in out + err
            session = strict_loads((config / "session.json").read_bytes())
            assert session["token"] not in out + err
            assert stat.S_IMODE((config / "session.json").stat().st_mode) == 0o600
            assert stat.S_IMODE(config.stat().st_mode) == 0o700
            code, out, err = await cli(config, url, "agents", "create", "CLI agent")
            assert code == 0, err
            owner = records(out)[0]
            for args in [("agents", "list"), ("agents", "get", owner["id"]),
                ("agents", "rename", owner["id"], "Renamed CLI", "--revision", "1")]:
                code, out, err = await cli(config, url, *args); assert code == 0, err
            for method, params in [("providers.configure", {"provider_id": "wire", "endpoint": model_origin + "/model", "model": "wire",
                    "context_limit": 64000, "output_limit": 512, "tools": True}),
                ("providers.probe", {"provider_id": "wire", "expected_revision": 1}),
                ("providers.activate", {"provider_id": "wire", "expected_revision": 1})]:
                code, out, err = await cli(config, url, "command", owner["id"], method, "--params-file", "-", input=canonical_json(params))
                assert code == 0, err
            code, out, err = await cli(config, url, "credential", owner["id"], "external-key", input="actual-api-secret\n")
            assert code == 0 and "actual-api-secret" not in out + err
            code, out, err = await cli(config, url, "chat", owner["id"], "--text-file", "-", "--message-id", "first", input="A real CLI input")
            assert code == 0, err
            events = records(out); receipt = events[0]
            assert receipt["status"] == "admitted" and receipt["request"]["message_id"] == "first"
            assert "".join(item["payload"]["content"]["text"] for item in events[1:] if item["kind"] == "output.persisted" and item["payload"]["content"].get("type") == "text") == "First CLI response"
            assert events[-1]["kind"] == "runtime.turn_completed"
            room = receipt["request"]["conversation_id"]
            code, out, err = await cli(config, url, "events", owner["id"], "--conversation", room)
            assert code == 0 and records(out)[0]["items"], err
            code, out, err = await cli(config, url, "chat", owner["id"], "--conversation", room, "--no-wait", input="Next message\n")
            assert code == 0 and records(out)[0]["status"] == "admitted", err
            code, out, err = await cli(config, url, "stop", owner["id"], room, "--message-id", "stop_cli")
            assert code == 0 and records(out)[0]["request"]["kind"] == "cancel", err
            code, out, err = await cli(config, url, "doctor", "--agent", owner["id"])
            assert code == 0 and records(out)[0]["local_native_ready"], err
            assert records(out)[0]["agent"]["native_memory_available"] is True
            code, out, err = await cli(config, url, "logout")
            assert code == 0 and not (config / "session.json").exists(), err
            code, out, err = await cli(config, url, "agents", "list")
            assert code == 1 and "unauthenticated" in err and out == ""
            code, out, err = await cli(config, url, "login", input="cli@example.com\n" + PASSWORD + "\n", module=True)
            assert code == 0 and records(out)[0]["authenticated"], err
    finally:
        server.close()
        for task in list(model.tasks): task.cancel()
        await asyncio.gather(*model.tasks, return_exceptions=True)
        await server.wait_closed()


async def test_installed_setup_guidance_and_actual_serve(tmp_path):
    config = tmp_path / "client"
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
    url = f"http://127.0.0.1:{port}"
    code, out, err = await cli(config, url, "setup", "--root", str(tmp_path / "server"), "--native-binary", str(tmp_path / "missing/native"))
    assert code == 0, err
    settings = records(out)[0]
    assert settings["mode"] == "self_hosted" and not settings["native_ready"]
    assert settings["build_commands"][0][0] == "cmake"
    assert stat.S_IMODE((config / "config.json").stat().st_mode) == 0o600
    code, out, err = await cli(config, url, "setup", "--native-binary", str(ROOT / ".build/native/sturgis-memory"))
    assert code == 0 and records(out)[0]["native_ready"]
    process = await asyncio.create_subprocess_exec(str(CLI), "--config-dir", str(config), "serve", "--port", str(port),
        "--public-origin", url, "--worker-memory-bytes", "2147483648", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=ROOT)
    try:
        async with httpx.AsyncClient() as http:
            for _ in range(100):
                try:
                    response = await http.get(url + "/api/v1/health")
                    if response.status_code == 200: break
                except httpx.ConnectError: pass
                if process.returncode is not None: pytest.fail("CLI server exited before startup")
                await asyncio.sleep(.05)
            assert response.json()["data"]["status"] == "ok"
            code, out, err = await cli(config, url, "doctor")
            assert code == 0 and records(out)[0]["identity"] == {"authenticated": False}, err
    finally:
        if process.returncode is None: process.terminate()
        out, err = await asyncio.wait_for(process.communicate(), 10)
    assert b"Traceback" not in err


def test_private_store_rotation_rejects_links_permissions_and_oversize(tmp_path):
    with PrivateStore(tmp_path / "client") as store:
        assert store.read("session.json") is None
        store.write("session.json", {"token": "first"})
        store.write("session.json", {"token": "second"})
        assert store.read("session.json") == {"token": "second"}
        target = store.root / "session.json"
        target.chmod(0o644)
        with pytest.raises(CoreError, match="privately owned"): store.read("session.json")
        target.chmod(0o600)
        os.link(target, store.root / "hardlink")
        with pytest.raises(CoreError): store.read("session.json")
        (store.root / "hardlink").unlink()
        store.remove("session.json"); store.remove("session.json")
        target.symlink_to(tmp_path / "outside")
        with pytest.raises(OSError): store.write("session.json", {"token": "not written"})
        assert not (tmp_path / "outside").exists()
        target.unlink(); target.write_bytes(b"x" * 1048577); target.chmod(0o600)
        with pytest.raises(CoreError, match="byte bound"): store.read("session.json")
        target.write_text("[]")
        with pytest.raises(CoreError, match="JSON object"): store.read("session.json")
    public = tmp_path / "public"; public.mkdir(mode=0o755)
    with pytest.raises(CoreError): PrivateStore(public)


def test_private_output_redaction_and_safe_argument_errors(tmp_path, capsys):
    data = {"token": "sensitive", "nested": [{"PASSWORD": "password", "reference": "safe"}]}
    assert redacted(data)["nested"][0] == {"PASSWORD": "[redacted]", "reference": "safe"}
    path = tmp_path / "secret.json"
    _private_output(path, data)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and strict_loads(path.read_bytes()) == data
    with pytest.raises(FileExistsError): _private_output(path, {})
    assert main(["--password", "accidental-secret"]) == 2
    assert "accidental-secret" not in capsys.readouterr().err
    assert main(["--output-file", str(path), "events", "agent"]) == 2
    assert "one response" in capsys.readouterr().err


def test_params_prompts_and_local_failures(tmp_path, monkeypatch, capsys):
    params = tmp_path / "params.json"; params.write_text('{"id":"one"}')
    assert _params(None) == {} and _params(str(params)) == {"id": "one"}
    params.write_bytes(b" " * 8388609)
    with pytest.raises(CoreError, match="byte limit"): _params(str(params))
    monkeypatch.setattr(sys, "stdin", io.StringIO("private\n"))
    assert _prompt("Secret", secret=True) == "private"
    assert "private" not in capsys.readouterr().out
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))
    with pytest.raises(CoreError): _prompt("Empty")
    stdin = io.StringIO(); monkeypatch.setattr(stdin, "isatty", lambda: True); monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr("consumer.cli.getpass.getpass", lambda prompt: "tty-private")
    assert _prompt("Secret", secret=True) == "tty-private"
    def interrupt(prompt): raise KeyboardInterrupt()
    monkeypatch.setattr("consumer.cli.getpass.getpass", interrupt)
    assert main(["--config-dir", str(tmp_path / "client"), "login", "--email", "owner@example.com"]) == 130
    assert "interrupted" in capsys.readouterr().err
    assert main(["--config-dir", str(tmp_path / "client"), "command", "agent", "overview", "--params-file", str(tmp_path / "missing")]) == 1
    assert "local_error" in capsys.readouterr().err


def test_serve_settings_preserve_operator_choices(tmp_path, monkeypatch):
    config = tmp_path / "client"
    with PrivateStore(config) as store:
        store.write("config.json", {"server": {"root": str(tmp_path / "server"), "mode": "self_hosted"}})
        args = parser().parse_args(["serve", "--host", "0.0.0.0", "--public-origin", "https://agent.example",
            "--browser-binary", "/operator/chromium", "--worker-memory-bytes", "274877906944", "--private-origin", "http://127.0.0.1:99",
            "--allow-signup", "--bootstrap-prompt"])
        monkeypatch.setattr("consumer.cli._prompt", lambda *args, **kwargs: "operator-private")
        settings = _settings(store, args)
        assert settings.worker_memory_bytes == 274877906944 and settings.allow_signup
        assert settings.private_network_origins == ("http://127.0.0.1:99",) and settings.bootstrap_token == "operator-private"
        assert settings.browser_binary == Path("/operator/chromium")
        captured = []
        monkeypatch.setattr("consumer.api.serve", lambda value: captured.append(value))
        assert main(["--config-dir", str(config), "serve", "--public-origin", "https://agent.example"]) == 0
        assert captured[0].public_origin == "https://agent.example"
        store.write("config.json", {"server": []})
        with pytest.raises(CoreError): _settings(store, args)


@pytest.mark.parametrize("payload,status,expected", [(success({}), 301, "invalid_response"),
    ({"version": "wrong", "request_id": None, "data": {}, "error": None}, 200, "invalid_response"),
    ({"version": "consumer/1", "request_id": None, "data": {}, "error": {}}, 200, "invalid_response"),
    ({"version": "consumer/1", "request_id": None, "data": None, "error": {"code": [], "message": "x", "details": {}}}, 400, "invalid_response"),
    (failure(CoreError("denied", "No access", token="hidden")), 403, "denied")])
async def test_client_normalizes_wire_errors_and_does_not_follow_redirects(tmp_path, payload, status, expected):
    async def handler(writer, request): await reply(writer, payload, status=status, headers={"Location": "https://ungranted.example"})
    async with wire(handler) as (url, requests):
        with PrivateStore(tmp_path / "client") as store:
            client = Client(url, store)
            try:
                with pytest.raises(CoreError) as error: await asyncio.to_thread(client.request, "GET", "/health", authenticated=False)
                assert error.value.code == expected and len(requests) == 1
                assert "hidden" not in canonical_json(error.value.as_dict())
            finally: client.close()


async def test_client_session_origin_binding_timeout_byte_limits_and_request_identity(tmp_path):
    async def handler(writer, request):
        if request["path"].endswith("/large"):
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 8388609\r\n\r\n" + b"x" * 8388609); await writer.drain()
        else: await asyncio.sleep(1)
    async with wire(handler) as (url, requests):
        with PrivateStore(tmp_path / "client") as store:
            store.write("session.json", {"url": "https://other.example", "token": "private", "expires_at": time.time() + 100})
            client = Client(url, store, timeout=.1)
            try:
                with pytest.raises(CoreError, match="Run sturgis login"): client.request("GET", "/health")
                assert requests == []
                with pytest.raises(CoreError, match="consumer dotted method"): client.command("agent", "../override")
                with pytest.raises(CoreError, match="request byte limit"):
                    client.request("POST", "/health", data={"x": "x" * 8388608}, authenticated=False)
                store.write("session.json", {"url": url, "token": "private", "expires_at": time.time() + 100})
                with pytest.raises(CoreError) as error:
                    await asyncio.to_thread(client.command, "agent", "overview", request_id="stable_request")
                assert error.value.code == "request_timeout" and error.value.details["request_id"] == "stable_request"
                with pytest.raises(CoreError, match="response exceeds"):
                    await asyncio.to_thread(client.request, "GET", "/large", authenticated=False)
            finally: client.close()
        with PrivateStore(tmp_path / "second") as store:
            with pytest.raises(CoreError): Client("ws://example.test", store)
    with PrivateStore(tmp_path / "third") as store:
        client = Client(url, store)
        try:
            with pytest.raises(CoreError, match="Could not reach"):
                await asyncio.to_thread(client.request, "GET", "/health", authenticated=False)
        finally: client.close()


async def test_events_follow_advances_cursor_and_secret_output_file(tmp_path, capsys):
    calls, next_request = [], asyncio.Event()
    async def handler(writer, request):
        calls.append(request)
        if "/events" in request["path"]:
            if len(calls) == 1:
                await reply(writer, success({"items": [], "cursor": 12}))
                # Keep this one-request handler alive through the next poll.
                # The response must announce close; otherwise HTTP/1.1 pooling
                # reuses this socket, where no second request will be read.
                await next_request.wait()
            else:
                next_request.set()
                await reply(writer, failure(CoreError("end_fixture", "Finished")), status=400)
        else: await reply(writer, success({"pairing_token": "external-pairing-secret", "id": "desk"}))
    async with wire(handler) as (url, _):
        config = tmp_path / "client"
        with PrivateStore(config) as store:
            store.write("session.json", {"url": url, "token": "private", "expires_at": time.time() + 100})
        code = await asyncio.to_thread(main, ["--config-dir", str(config), "--url", url, "--timeout", "1", "--json", "events", "agent", "--follow"])
        captured = capsys.readouterr()
        assert code == 1 and len(calls) == 2, captured.err
        assert "after=12" in calls[1]["path"]
        assert records(captured.out) == [{"items": [], "cursor": 12}]
        assert records(captured.err)[0]["error"]["code"] == "end_fixture"
        output = tmp_path / "pair.json"
        code = await asyncio.to_thread(main, ["--config-dir", str(config), "--url", url, "--output-file", str(output), "command", "agent", "devices.pair"])
        assert code == 0 and strict_loads(output.read_bytes())["pairing_token"] == "external-pairing-secret"
        assert "external-pairing-secret" not in capsys.readouterr().out
        assert main(["--config-dir", str(config), "events", "agent", "--after", "-1"]) == 1


async def test_backup_restore_exact_http_transport_integrity_and_private_archive(tmp_path, capsys):
    # Transport fixture bytes; archive semantic validation belongs to Portability.
    data = b"compressed archive transport fixture"
    import hashlib
    digest = hashlib.sha256(data).hexdigest()
    state = {"corrupt": False}
    async def handler(writer, request):
        if request["path"].endswith("/backup"):
            assert strict_loads(request["body"]) == {}
            await reply(writer, success({"format": "sturgis-backup/1", "archive_base64": base64.b64encode(data).decode(),
                "sha256": "0" * 64 if state["corrupt"] else digest, "bytes": len(data)}))
        else:
            body = strict_loads(request["body"])
            expected_id = "restore_" + hashlib.sha256(canonical_json({"name": "Restored agent", "sha256": digest}).encode()).hexdigest()
            assert body == {"name": "Restored agent", "archive_base64": base64.b64encode(data).decode(), "sha256": digest, "request_id": expected_id}
            await reply(writer, success({"id": "restored", "name": body["name"], "restore": {"status": "complete"}}))
    async with wire(handler) as (url, requests):
        config = tmp_path / "client"
        with PrivateStore(config) as store:
            store.write("session.json", {"url": url, "token": "private", "expires_at": time.time() + 100})
        archive = tmp_path / "agent.sturgis"
        prefix = ["--config-dir", str(config), "--url", url, "--json"]
        assert await asyncio.to_thread(main, [*prefix, "backup", "agent", str(archive)]) == 0
        assert archive.read_bytes() == data and stat.S_IMODE(archive.stat().st_mode) == 0o600
        assert await asyncio.to_thread(main, [*prefix, "restore", str(archive), "--name", "Restored agent"]) == 0
        assert requests[-1]["path"] == "/api/v1/agents/restore"
        state["corrupt"] = True
        denied = tmp_path / "corrupt.sturgis"
        assert await asyncio.to_thread(main, [*prefix, "backup", "agent", str(denied)]) == 1
        assert not denied.exists() and "backup_integrity" in capsys.readouterr().err
        oversized = tmp_path / "large.sturgis"; oversized.write_bytes(b"x" * 4194305)
        assert await asyncio.to_thread(main, [*prefix, "restore", str(oversized), "--name", "large"]) == 1
        assert "backup_too_large" in capsys.readouterr().err
