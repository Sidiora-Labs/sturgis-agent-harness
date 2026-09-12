"""Authenticated consumer CLI; only serve hosts the existing application server."""
from __future__ import annotations

import argparse
import base64
from dataclasses import asdict
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time

import httpx

from agent.protocol import CoreError, canonical_json, new_id, strict_loads
from consumer.config import Settings
from consumer.contracts import MAX_BODY, VERSION, failure, identifier, object_body
from consumer.network import exact_origin

SECRET_FIELDS = {"password", "token", "csrf_token", "secret", "pairing_token", "bootstrap_token", "authorization", "api_key"}


def redacted(value):
    if isinstance(value, dict):
        return {key: "[redacted]" if key.casefold() in SECRET_FIELDS else redacted(item) for key, item in value.items()}
    if isinstance(value, list): return [redacted(item) for item in value]
    return value


class PrivateStore:
    """Directory-relative atomic files: private ownership and no symlink following."""
    def __init__(self, root):
        self.root = Path(root).expanduser().absolute()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            self._check(os.fstat(self.fd), directory=True)
        except BaseException:
            os.close(self.fd); raise

    @staticmethod
    def _check(meta, *, directory=False):
        if ((not stat.S_ISDIR(meta.st_mode) if directory else not stat.S_ISREG(meta.st_mode))
                or meta.st_uid != os.getuid() or meta.st_mode & 0o077 or not directory and meta.st_nlink != 1):
            raise CoreError("unsafe_client_state", "Client files must be privately owned regular files and directories")

    def read(self, name):
        try: fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.fd)
        except FileNotFoundError: return None
        with os.fdopen(fd, "rb") as stream:
            self._check(os.fstat(stream.fileno()))
            data = stream.read(1048577)
        if len(data) > 1048576: raise CoreError("unsafe_client_state", "Client configuration exceeds its byte bound")
        value = strict_loads(data)
        if not isinstance(value, dict): raise CoreError("unsafe_client_state", "Client configuration must be a JSON object")
        return value

    def write(self, name, value):
        self.read(name)  # Reject an unsafe existing target before replacement.
        temporary = ".tmp_" + secrets.token_hex(16)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(canonical_json(value).encode()); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            os.fsync(self.fd)
        finally:
            try: os.unlink(temporary, dir_fd=self.fd)
            except FileNotFoundError: pass

    def remove(self, name):
        if self.read(name) is not None:
            os.unlink(name, dir_fd=self.fd); os.fsync(self.fd)

    def __enter__(self): return self

    def __exit__(self, *_): os.close(self.fd)


def _private_output(path, value):
    _private_bytes(path, canonical_json(value).encode() + b"\n")


def _private_bytes(path, value):
    fd = os.open(Path(path).expanduser(), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(value); stream.flush(); os.fsync(stream.fileno())


def _prompt(label, *, secret=False):
    if secret and sys.stdin.isatty():
        value = getpass.getpass(label + ": ")
    else:
        print(label + ": ", end="", file=sys.stderr, flush=True)
        value = sys.stdin.readline(65538).rstrip("\r\n")
    if not value or len(value) > 65536:
        raise CoreError("invalid_input", "A nonempty bounded value is required")
    return value


def _params(path):
    if path is None: return {}
    if path == "-": raw = sys.stdin.buffer.read(MAX_BODY + 1)
    else:
        with Path(path).expanduser().open("rb") as stream: raw = stream.read(MAX_BODY + 1)
    if len(raw) > MAX_BODY: raise CoreError("body_too_large", "Command parameters exceed the byte limit")
    value = strict_loads(raw)
    return object_body(value, value.keys() if isinstance(value, dict) else ())


class Client:
    def __init__(self, url, store, *, timeout=30):
        self.url, self.store = exact_origin(url), store
        if not self.url.startswith(("http://", "https://")) or type(timeout) not in {int, float} or not 0 < timeout <= 300:
            raise CoreError("invalid_client", "Client requires an HTTP origin and bounded request timeout")
        self.http = httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False)

    def request(self, method, path, *, data=None, params=None, authenticated=True):
        headers = {"Accept": "application/json"}
        if authenticated:
            session = self.store.read("session.json")
            if (not session or session.get("url") != self.url or not isinstance(session.get("expires_at"), (int, float))
                    or session["expires_at"] <= time.time() or not isinstance(session.get("token"), str)
                    or not 1 <= len(session["token"]) <= 1024 or any(not 33 <= ord(c) <= 126 for c in session["token"])):
                raise CoreError("unauthenticated", "Run sturgis login for this server before using authenticated commands")
            headers["Authorization"] = "Bearer " + session["token"]
        encoded = None
        if data is not None:
            encoded = canonical_json(data).encode()
            if len(encoded) > MAX_BODY: raise CoreError("body_too_large", "Command exceeds the request byte limit")
            headers["Content-Type"] = "application/json"
        try:
            with self.http.stream(method, self.url + "/api/v1" + path, headers=headers, content=encoded, params=params) as response:
                chunks, total = [], 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > MAX_BODY: raise CoreError("response_too_large", "Server response exceeds the client byte limit")
                    chunks.append(chunk)
                status = response.status_code
        except httpx.TimeoutException:
            raise CoreError("request_timeout", "Server response timed out; mutation outcomes may require inspection") from None
        except httpx.HTTPError:
            raise CoreError("connection_failed", "Could not reach the configured server") from None
        try:
            value = object_body(strict_loads(b"".join(chunks)), {"version", "request_id", "data", "error"}, ["version", "request_id", "data", "error"])
            if value["version"] != VERSION or (value["data"] is None) == (value["error"] is None): raise ValueError()
            if value["error"] is not None:
                error = object_body(value["error"], {"code", "message", "details"}, ["code", "message", "details"])
                if not isinstance(error["code"], str) or not isinstance(error["message"], str) or not isinstance(error["details"], dict): raise ValueError()
                raise CoreError(error["code"][:128], error["message"][:512], **redacted(error["details"]))
            if not 200 <= status < 300: raise ValueError()
            return value["data"]
        except (ValueError, TypeError):
            raise CoreError("invalid_response", "Server did not return a valid consumer response") from None

    def command(self, agent, method, params=None, *, request_id=None):
        identifier(agent)
        if not isinstance(method, str) or not re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){0,4}", method):
            raise CoreError("invalid_command", "Command names must use the consumer dotted method format")
        request_id = identifier(request_id) if request_id else new_id("cli")
        try:
            return self.request("POST", f"/agents/{agent}/commands/{method}", data={"params": params or {}, "request_id": request_id})
        except CoreError as exc:
            exc.details.setdefault("request_id", request_id)
            raise

    def close(self): self.http.close()


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's raw message can echo an accidentally supplied credential.
        raise CoreError("usage", "Invalid command arguments; use --help for supported options")


def parser():
    root = Parser(prog="sturgis", description="One persistent agent through the shared authenticated consumer API.")
    root.add_argument("--url", help="Server origin, such as http://127.0.0.1:8787")
    root.add_argument("--config-dir", default=str(Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "sturgis"))
    root.add_argument("--json", action="store_true", help="Emit compact JSON records")
    root.add_argument("--output-file", help="Write one full response to a new private 0600 file")
    root.add_argument("--timeout", type=float, default=30)
    commands = root.add_subparsers(dest="action", required=True, parser_class=Parser)
    setup = commands.add_parser("setup", help="Create private client configuration and native build guidance")
    for key in ("root", "native-binary", "source-root"): setup.add_argument("--" + key)
    serve = commands.add_parser("serve", help="Serve the existing Sturgis consumer application")
    for key in ("root", "host", "native-binary", "source-root", "public-origin", "browser-binary"):
        serve.add_argument("--" + key)
    serve.add_argument("--mode", choices=["self_hosted", "hosted"])
    serve.add_argument("--port", type=int)
    serve.add_argument("--worker-memory-bytes", type=int, help="Operator RLIMIT_AS virtual address-space ceiling; not physical RAM")
    serve.add_argument("--worker-timeout", type=float)
    serve.add_argument("--private-origin", action="append")
    serve.add_argument("--allow-signup", action="store_true", default=None)
    serve.add_argument("--bootstrap-prompt", action="store_true")
    login = commands.add_parser("login", help="Authenticate; passwords and bootstrap tokens are prompted privately")
    login.add_argument("--email"); login.add_argument("--bootstrap", action="store_true")
    commands.add_parser("logout", help="Revoke the current server session")
    agents = commands.add_parser("agents", help="List and manage owned agents")
    actions = agents.add_subparsers(dest="agent_action", required=True, parser_class=Parser)
    actions.add_parser("list")
    create = actions.add_parser("create"); create.add_argument("name")
    get = actions.add_parser("get"); get.add_argument("agent")
    for action in ("rename", "archive"):
        item = actions.add_parser(action); item.add_argument("agent"); item.add_argument("--revision", type=int, required=True)
        if action == "rename": item.add_argument("name")
    chat = commands.add_parser("chat", help="Admit a message and replay its persisted response")
    chat.add_argument("agent"); chat.add_argument("--conversation"); chat.add_argument("--message-id")
    chat.add_argument("--text-file", help="Read UTF-8 message text from a file or - for stdin")
    chat.add_argument("--no-wait", action="store_true"); chat.add_argument("--wait-timeout", type=float, default=120)
    events = commands.add_parser("events", help="Replay durable events after a cursor")
    events.add_argument("agent"); events.add_argument("--conversation"); events.add_argument("--after", type=int, default=0)
    events.add_argument("--follow", action="store_true")
    stop = commands.add_parser("stop", help="Send a durable cancellation request for a conversation")
    stop.add_argument("agent"); stop.add_argument("conversation"); stop.add_argument("--message-id")
    command = commands.add_parser("command", help="Inspect or manage any allowlisted consumer domain")
    command.add_argument("agent"); command.add_argument("method"); command.add_argument("--params-file")
    command.add_argument("--request-id")
    credential = commands.add_parser("credential", help="Put an API credential into the agent's encrypted vault")
    credential.add_argument("agent"); credential.add_argument("name"); credential.add_argument("--revision", type=int, default=0)
    doctor = commands.add_parser("doctor", help="Inspect local native availability, HTTP health and authentication")
    doctor.add_argument("--agent")
    backup = commands.add_parser("backup", help="Download and verify an owned agent backup into a private archive")
    backup.add_argument("agent"); backup.add_argument("output")
    restore = commands.add_parser("restore", help="Restore a bounded backup into a new owned agent")
    restore.add_argument("archive"); restore.add_argument("--name", required=True)
    restore.add_argument("--request-id", help="Stable restore identity; override the content-derived default for an intentional additional copy")
    return root


def _emit(value, args):
    if args.output_file:
        _private_output(args.output_file, value)
        value = {"written": str(Path(args.output_file).expanduser()), "mode": "0600"}
    print(canonical_json(redacted(value)) if args.json else json.dumps(redacted(value), ensure_ascii=False, indent=2), flush=True)


def _settings(store, args):
    config = store.read("config.json") or {}
    server = config.get("server", {})
    if not isinstance(server, dict): raise CoreError("unsafe_client_state", "Server configuration must be an object")
    environ = {"STURGIS_" + key.upper(): str(value) for key, value in server.items()}
    environ.update(os.environ)
    values = asdict(Settings.from_env(environ))
    for key in ("root", "host", "port", "mode", "native_binary", "source_root", "public_origin", "browser_binary", "worker_memory_bytes", "worker_timeout", "allow_signup"):
        if getattr(args, key, None) is not None: values[key] = getattr(args, key)
    if getattr(args, "private_origin", None) is not None: values["private_network_origins"] = tuple(args.private_origin)
    if getattr(args, "bootstrap_prompt", False): values["bootstrap_token"] = _prompt("Bootstrap token", secret=True)
    return Settings(**values)


def _setup(store, args):
    settings = _settings(store, args)
    config = store.read("config.json") or {}
    url = exact_origin(args.url or config.get("url", "http://127.0.0.1:8787"))
    config = {"url": url, "server": {"root": str(settings.root), "mode": settings.mode,
        "native_binary": str(settings.native_binary), "source_root": str(settings.source_root)}}
    store.write("config.json", config)
    ready = settings.native_binary.is_file() and os.access(settings.native_binary, os.X_OK)
    return {"configured": True, "url": url, "mode": settings.mode, "native_ready": ready,
        "native_binary": str(settings.native_binary), "bootstrap_token_file": str(settings.root / "bootstrap.token"), "next": "sturgis serve",
        "build_commands": [] if ready else [["cmake", "-S", str(settings.source_root), "-B", str(settings.native_binary.parent), "-DCMAKE_BUILD_TYPE=Release"],
            ["cmake", "--build", str(settings.native_binary.parent), "--target", "sturgis-memory"]]}


def _chat(client, args):
    identifier(args.agent)
    if not 0 < args.wait_timeout <= 3600: raise CoreError("usage", "Chat wait timeout must be one to 3600 seconds")
    if args.text_file:
        if args.text_file == "-": data = sys.stdin.buffer.read(65537)
        else:
            with Path(args.text_file).expanduser().open("rb") as stream: data = stream.read(65537)
        if len(data) > 65536: raise CoreError("body_too_large", "Chat text exceeds the byte limit")
        text = data.decode("utf-8")
    else: text = _prompt("Message")
    conversation = identifier(args.conversation) if args.conversation else client.command(args.agent, "conversations.create", {})["id"]
    message = identifier(args.message_id) if args.message_id else new_id("message")
    request_id = "send_" + hashlib.sha256(canonical_json([conversation, message]).encode()).hexdigest()
    receipt = client.command(args.agent, "messages.send", {"conversation_id": conversation, "message_id": message, "text": text}, request_id=request_id)
    _emit(receipt, args)
    if args.no_wait: return
    turn = next((item for item in client.command(args.agent, "conversations.get", {"id": conversation})["turns"] if item["id"] == receipt["work_id"]), None)
    if turn is None: raise CoreError("invalid_response", "Server omitted the admitted message's turn identity")
    deadline, cursor = time.monotonic() + args.wait_timeout, 0
    while time.monotonic() < deadline:
        page = client.request("GET", f"/agents/{args.agent}/events", params={"conversation_id": conversation, "after": cursor, "wait": min(25, max(0, deadline - time.monotonic()))})
        cursor = page["cursor"]
        for event in page["items"]:
            payload = event["payload"]
            if event["kind"] == "output.persisted" and payload["stream_id"] == turn["stream_id"]:
                if args.json: _emit(event, args)
                elif payload["content"].get("type") == "text": print(payload["content"]["text"], end="", flush=True)
            if event["kind"].startswith("runtime.turn_") and payload.get("work_id") == receipt["work_id"]:
                if not args.json: print()
                _emit(event, args)
                if event["kind"] != "runtime.turn_completed": raise CoreError("turn_incomplete", "Agent turn ended without completion", event_id=event["id"])
                return
    raise CoreError("wait_timeout", "Stopped waiting; accepted work can be inspected with events", conversation_id=conversation, message_id=message, cursor=cursor)


def _remote(client, store, args):
    if args.action == "backup":
        value = client.request("POST", f"/agents/{identifier(args.agent)}/backup", data={})
        value = object_body(value, {"format", "archive_base64", "sha256", "bytes"}, ["format", "archive_base64", "sha256", "bytes"])
        try:
            if value["format"] != "sturgis-backup/1" or type(value["bytes"]) is not int or not 1 <= value["bytes"] <= 4194304:
                raise ValueError()
            data = base64.b64decode(value["archive_base64"], validate=True)
            if len(data) != value["bytes"] or hashlib.sha256(data).hexdigest() != value["sha256"]: raise ValueError()
        except (ValueError, TypeError):
            raise CoreError("backup_integrity", "Backup response failed its format, size or digest verification") from None
        _private_bytes(args.output, data)
        return {"written": str(Path(args.output).expanduser()), "format": value["format"], "sha256": value["sha256"], "bytes": len(data), "mode": "0600"}
    if args.action == "restore":
        with Path(args.archive).expanduser().open("rb") as stream: data = stream.read(4194305)
        if not 1 <= len(data) <= 4194304: raise CoreError("backup_too_large", "Restore requires a nonempty archive of at most 4 MiB")
        digest = hashlib.sha256(data).hexdigest()
        request_id = args.request_id if args.request_id is not None else "restore_" + hashlib.sha256(canonical_json({"name": args.name, "sha256": digest}).encode()).hexdigest()
        return client.request("POST", "/agents/restore", data={"name": args.name,
            "archive_base64": base64.b64encode(data).decode(), "sha256": digest, "request_id": identifier(request_id)})
    if args.action == "login":
        email = args.email or _prompt("Email")
        data = {"email": email, "password": _prompt("Password", secret=True)}
        if args.bootstrap: data["token"] = _prompt("Bootstrap token", secret=True)
        result = client.request("POST", "/bootstrap" if args.bootstrap else "/auth/login", data=data, authenticated=False)
        store.write("session.json", {"url": client.url, "token": result["token"], "expires_at": result["expires_at"]})
        return {"authenticated": True, "principal": result["principal"], "expires_at": result["expires_at"]}
    if args.action == "logout":
        value = client.request("POST", "/auth/logout", data={}); store.remove("session.json"); return value
    if args.action == "agents":
        if args.agent_action == "list": return client.request("GET", "/agents")
        if args.agent_action == "create": return client.request("POST", "/agents", data={"name": args.name})
        path = "/agents/" + identifier(args.agent)
        if args.agent_action == "get": return client.request("GET", path)
        return client.request("PATCH", path, data={"expected_revision": args.revision, **({"name": args.name} if args.agent_action == "rename" else {"archived": True})})
    if args.action == "command": return client.command(args.agent, args.method, _params(args.params_file), request_id=args.request_id)
    if args.action == "credential":
        return client.command(args.agent, "providers.credentials.put", {"name": identifier(args.name), "secret": _prompt("API credential", secret=True), "expected_revision": args.revision})
    if args.action == "stop":
        return client.command(args.agent, "messages.cancel", {"conversation_id": identifier(args.conversation), "message_id": identifier(args.message_id) if args.message_id else new_id("cancel")})
    if args.action == "doctor":
        settings = _settings(store, args)
        result = {"local_native_ready": settings.native_binary.is_file() and os.access(settings.native_binary, os.X_OK), "server": client.request("GET", "/health", authenticated=False)}
        try: result["identity"] = client.request("GET", "/auth/me")
        except CoreError as error:
            if error.code != "unauthenticated": raise
            result["identity"] = {"authenticated": False}
        if args.agent: result["agent"] = client.command(args.agent, "diagnostics.get", {})
        return result
    if args.action == "chat": _chat(client, args); return None
    if args.action == "events":
        identifier(args.agent)
        if args.after < 0: raise CoreError("invalid_cursor", "Event cursor cannot be negative")
        params = {"after": args.after, "wait": 25 if args.follow else 0}
        if args.conversation: params["conversation_id"] = identifier(args.conversation)
        while True:
            value = client.request("GET", f"/agents/{args.agent}/events", params=params)
            _emit(value, args); params["after"] = value["cursor"]
            if not args.follow: return None


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        if args.output_file and args.action in {"chat", "events", "serve"}:
            raise CoreError("usage", "Private output files require a command with one response")
        with PrivateStore(args.config_dir) as store:
            if args.action == "setup": _emit(_setup(store, args), args); return 0
            if args.action == "serve":
                from consumer.api import serve
                serve(_settings(store, args)); return 0
            config = store.read("config.json") or {}
            client = Client(args.url or os.environ.get("STURGIS_URL") or config.get("url", "http://127.0.0.1:8787"), store, timeout=args.timeout)
            try:
                value = _remote(client, store, args)
                if value is not None: _emit(value, args)
            finally: client.close()
        return 0
    except KeyboardInterrupt:
        print(canonical_json(failure(CoreError("interrupted", "Client interrupted; inspect or stop any previously accepted work"))), file=sys.stderr); return 130
    except Exception as exc:
        error = exc if isinstance(exc, CoreError) else CoreError("local_error", "Command could not read, write or decode its local input")
        print(canonical_json(redacted(failure(error))), file=sys.stderr)
        return 2 if error.code == "usage" else 1
