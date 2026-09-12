"""Private process ownership and authenticated, bounded consumer IPC clients."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import fcntl
import hashlib
import hmac
import math
import os
from pathlib import Path
import secrets
import shutil
import signal
import stat
import sys
import time

from agent.framing import read_frame, write_frame
from agent.protocol import CoreError, canonical_json, new_id, strict_loads
from consumer.config import Settings
from consumer.contracts import MAX_BODY, VERSION, identifier, object_body


SECRET_METHODS = frozenset({"providers.credentials.put", "devices.pair", "devices.pairing.create", "ingress.device.claim"})


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    agent_id: str
    scope: str
    root: Path
    workspace: Path
    private_root: Path
    mode: str
    native_binary: Path
    source_root: Path
    private_network_origins: tuple[str, ...] = ()
    browser_binary: Path | None = None
    worker_memory_bytes: int = 2147483648
    gateway_endpoint: str | None = None

    def __post_init__(self):
        identifier(self.agent_id)
        if self.scope != self.agent_id:
            raise CoreError("invalid_config", "Worker scope must equal its server-issued agent identifier")
        if self.mode not in {"hosted", "self_hosted"}:
            raise CoreError("invalid_config", "Unknown worker mode")
        if type(self.worker_memory_bytes) is not int or not 134217728 <= self.worker_memory_bytes <= 274877906944:
            raise CoreError("invalid_config", "Worker address-space limit must be between 128 MiB and 256 GiB")
        for name in ("root", "workspace", "private_root", "native_binary", "source_root"):
            path = Path(getattr(self, name))
            if not path.is_absolute() or ".." in path.parts or path.resolve() != path:
                raise CoreError("invalid_config", "Worker paths must be absolute without symlinks")
            object.__setattr__(self, name, path)
        roots = [self.root, self.workspace, self.private_root]
        if len({p.parent for p in roots}) != 1 or len(set(roots)) != 3:
            raise CoreError("invalid_config", "Worker data, workspace and private directories must be distinct siblings")
        if self.root.parent.is_relative_to(self.source_root) or self.source_root.is_relative_to(self.root.parent):
            raise CoreError("invalid_config", "Private runtime storage cannot overlap immutable source")
        checked = Settings(root=self.root, source_root=self.source_root, mode=self.mode,
                           native_binary=self.native_binary, private_network_origins=self.private_network_origins)
        object.__setattr__(self, "private_network_origins", checked.private_network_origins)
        if self.gateway_endpoint is not None:
            from consumer.network import origin
            origin(self.gateway_endpoint)
            if not self.gateway_endpoint.endswith("/model-gateway/v1/chat/completions"):
                raise CoreError("invalid_config", "Gateway endpoint must be the operator's model gateway")
        if self.browser_binary is not None:
            browser = Path(self.browser_binary)
            if not browser.is_absolute() or browser.resolve() != browser:
                raise CoreError("invalid_config", "Browser executable must have a canonical operator path")
            object.__setattr__(self, "browser_binary", browser)

    def as_dict(self):
        return {k: str(v) if isinstance(v, Path) else list(v) if isinstance(v, tuple) else v for k, v in asdict(self).items()}

    @classmethod
    def from_dict(cls, value):
        names = set(cls.__dataclass_fields__)
        object_body(value, names, names - {"private_network_origins", "browser_binary", "worker_memory_bytes", "gateway_endpoint"})
        return cls(**value)


def signature(key, value, *, response=False):
    domain = b"sturgis-worker-response/1\0" if response else b"sturgis-worker-request/1\0"
    return hmac.new(key, domain + canonical_json(value).encode(), hashlib.sha256).hexdigest()


def _read_private(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
            raise CoreError("unsafe_private_file", "Private runtime configuration has unsafe permissions")
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise CoreError("invalid_config", "Private runtime configuration exceeds its limit")
    return data


def _atomic_private(path, data):
    temporary = path.with_name(new_id("private"))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _directory_fd(path):
    # Linux resolves this short proc-fd pathname to the real private directory;
    # UNIX socket sun_path's 108-byte bound therefore does not limit install roots.
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


class WorkerClient:
    def __init__(self, config, key, *, timeout=30):
        if not isinstance(key, bytes) or len(key) != 32:
            raise CoreError("invalid_config", "Worker authentication requires a 256-bit private key")
        self.config, self._key, self.timeout = config, key, timeout

    async def request(self, method, params, request_id=None, timeout=None):
        request_id = identifier(request_id or new_id("rpc"))
        budget = self.timeout if timeout is None else timeout
        if type(budget) not in {int, float} or not math.isfinite(budget) or not 0 < budget <= 300:
            raise CoreError("invalid_request", "Worker deadline must be between zero and 300 seconds")
        if not isinstance(method, str) or not method or len(method) > 128:
            raise CoreError("invalid_request", "Invalid worker command")
        object_body(params, params.keys() if isinstance(params, dict) else ())
        if len(canonical_json(params).encode()) > MAX_BODY - 4096:
            raise CoreError("body_too_large", "Command exceeds the private transport limit")
        unsigned = {"version": VERSION, "request_id": request_id, "method": method, "params": params,
                    "deadline": time.time() + budget, "nonce": secrets.token_hex(24)}
        payload = canonical_json({**unsigned, "signature": signature(self._key, unsigned)}).encode()
        writer, sent = None, False
        try:
            async with asyncio.timeout(budget):
                fd = _directory_fd(self.config.private_root)
                try:
                    reader, writer = await asyncio.open_unix_connection(f"/proc/self/fd/{fd}/worker.sock", limit=MAX_BODY + 4)
                finally:
                    os.close(fd)
                # Once bytes enter the transport, loss of acknowledgment is ambiguous.
                sent = True
                await write_frame(writer, payload, MAX_BODY)
                raw = await read_frame(reader, MAX_BODY)
                if raw is None:
                    raise CoreError("worker_protocol", "Worker closed before acknowledging the command")
                frame = strict_loads(raw)
                object_body(frame, {"response", "nonce", "signature"}, {"response", "nonce", "signature"})
                signed = {"response": frame["response"], "nonce": frame["nonce"]}
                if (frame["nonce"] != unsigned["nonce"] or not isinstance(frame["signature"], str)
                        or not hmac.compare_digest(frame["signature"], signature(self._key, signed, response=True))):
                    raise CoreError("worker_protocol", "Worker response authentication failed")
                response = frame["response"]
                object_body(response, {"version", "request_id", "data", "error"}, {"version", "request_id", "data", "error"})
                if response["version"] != VERSION or response["request_id"] != request_id or (response["data"] is None) == (response["error"] is None):
                    raise CoreError("worker_protocol", "Worker response does not match its request")
                if response["error"] is not None:
                    error = response["error"]
                    details = {**error.get("details", {}), "dispatched": True, "worker_response": True}
                    raise CoreError(error["code"], error["message"], **details)
                return response["data"]
        except (OSError, asyncio.TimeoutError) as exc:
            raise CoreError("worker_indeterminate" if sent else "worker_unavailable",
                            "Worker acknowledgment is unavailable", dispatched=sent, request_id=request_id) from exc
        except CoreError as exc:
            if "dispatched" not in exc.details:
                exc.details.update(dispatched=sent, request_id=request_id)
            raise
        finally:
            if writer is not None:
                writer.close()
                try: await writer.wait_closed()
                except OSError: pass


class WorkerManager:
    """One local placement owner; no distributed-placement or network-isolation claim."""
    def __init__(self, settings):
        self.settings = settings
        self.settings.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._ownership = (settings.root / ".worker-manager.lock").open("a+b")
        try: fcntl.flock(self._ownership, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._ownership.close()
            raise CoreError("manager_owned", "Another manager owns this runtime installation") from exc
        self._processes, self._clients, self._locks, self._drains = {}, {}, {}, {}
        self._closed = False

    def config(self, agent_id):
        identifier(agent_id)
        base = self.settings.root / "runtimes" / agent_id
        host = "[" + self.settings.host + "]" if ":" in self.settings.host else self.settings.host
        gateway_origin = self.settings.public_origin or f"http://{host}:{self.settings.port}"
        return WorkerConfig(agent_id, agent_id, base / "state", base / "workspace", base / "private",
            self.settings.mode, self.settings.native_binary, self.settings.source_root, self.settings.private_network_origins,
            self.settings.browser_binary, self.settings.worker_memory_bytes, gateway_origin + "/model-gateway/v1/chat/completions")

    async def create(self, agent_id):
        if self._closed:
            raise CoreError("closed", "Worker manager is closed")
        config = self.config(agent_id)
        def prepare():
            for path in (config.root, config.workspace, config.private_root):
                path.mkdir(parents=True, exist_ok=True, mode=0o700)
                if path.resolve() != path or not path.is_dir():
                    raise CoreError("unsafe_runtime_root", "Runtime storage follows a symlink")
                os.chmod(path, 0o700)
            _atomic_private(config.private_root / "launch.json", canonical_json(config.as_dict()).encode())
            key_file = config.private_root / "ipc.key"
            if not key_file.exists():
                _atomic_private(key_file, secrets.token_bytes(32))
            key = _read_private(key_file, 32)
            self._clients[agent_id] = WorkerClient(config, key, timeout=self.settings.worker_timeout)
        await asyncio.to_thread(prepare)
        return config

    def client(self, agent_id):
        identifier(agent_id)
        if agent_id not in self._clients:
            raise CoreError("worker_unavailable", "Worker has not been prepared", dispatched=False)
        return self._clients[agent_id]

    def _command(self, config):
        program = [sys.executable, "-m", "consumer.worker", "--config", str(config.private_root / "launch.json")]
        environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1",
                       "PYTHONUNBUFFERED": "1", "PYTHONPATH": str(config.source_root), "HOME": str(config.private_root)}
        if config.mode == "self_hosted":
            # pytest-cov's established child interpreter hook is test instrumentation,
            # never mounted writable through hosted isolation.
            environment.update({k: v for k, v in os.environ.items() if k.startswith("COV_CORE_") or k == "COVERAGE_FILE"})
            return program, environment
        bwrap, prlimit = shutil.which("bwrap"), shutil.which("prlimit")
        if not bwrap or not prlimit:
            raise CoreError("isolation_unavailable", "Hosted workers require Linux bubblewrap and prlimit")
        command = [prlimit, f"--as={config.worker_memory_bytes}", "--cpu=86400", "--nofile=1024", "--nproc=256", "--",
            bwrap, "--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--die-with-parent",
            "--new-session", "--clearenv", "--cap-drop", "ALL"]
        for system in ("/usr", "/lib", "/lib64", "/bin"):
            if Path(system).exists(): command.extend(["--ro-bind", system, system])
        command.extend(["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"])
        for path in ("/etc/resolv.conf", "/etc/hosts", "/etc/nsswitch.conf", "/etc/ssl/certs", "/etc/fonts"):
            if Path(path).exists(): command.extend(["--ro-bind", path, path])
        command.extend(["--ro-bind", str(config.source_root), str(config.source_root)])
        if not config.native_binary.is_relative_to(config.source_root):
            command.extend(["--ro-bind", str(config.native_binary), str(config.native_binary)])
        if config.browser_binary is not None and not config.browser_binary.is_relative_to(config.source_root):
            browser_home = str(config.browser_binary.parent)
            command.extend(["--ro-bind", browser_home, browser_home])
        interpreter_home = Path(sys.prefix)
        if not interpreter_home.is_relative_to(config.source_root) and not interpreter_home.is_relative_to("/usr"):
            command.extend(["--ro-bind", str(interpreter_home), str(interpreter_home)])
        for path in (config.root, config.workspace, config.private_root):
            command.extend(["--bind", str(path), str(path)])
        for key, value in environment.items(): command.extend(["--setenv", key, value])
        command.extend(["--chdir", str(config.source_root), "--", *program])
        return command, {"PATH": "/usr/bin:/bin"}

    async def _drain(self, stream):
        # Diagnostics are bounded and discarded here; never returned in public errors.
        while await stream.read(4096): pass

    async def start(self, agent_id):
        async with self._locks.setdefault(identifier(agent_id), asyncio.Lock()):
            if self._closed:
                raise CoreError("closed", "Worker manager is closed")
            process = self._processes.get(agent_id)
            if process is not None and process.returncode is None:
                return self.client(agent_id)
            config = await self.create(agent_id)
            if not config.native_binary.is_file():
                raise CoreError("worker_unavailable", "Native memory executable is unavailable", dispatched=False)
            # A prior owner's socket cannot satisfy this launch's readiness probe.
            # Domain receipts survive rotation; transport authentication does not.
            key = secrets.token_bytes(32)
            await asyncio.to_thread(_atomic_private, config.private_root / "ipc.key", key)
            self._clients[agent_id] = WorkerClient(config, key, timeout=self.settings.worker_timeout)
            command, environment = self._command(config)
            process = await asyncio.create_subprocess_exec(*command, cwd=config.source_root, env=environment,
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True)
            self._processes[agent_id] = process
            self._drains[agent_id] = [asyncio.create_task(self._drain(s)) for s in (process.stdout, process.stderr)]
            client = self.client(agent_id)
            deadline = time.monotonic() + self.settings.worker_timeout
            try:
                while time.monotonic() < deadline:
                    if process.returncode is not None:
                        raise CoreError("isolation_unavailable" if config.mode == "hosted" else "worker_unavailable",
                                        "Private worker exited before readiness", dispatched=False)
                    try:
                        ready = await client.request("_worker.status", {}, timeout=min(.5, self.settings.worker_timeout))
                        if ready.get("agent_id") != agent_id or ready.get("scope") != config.scope:
                            raise CoreError("worker_protocol", "Worker readiness identity mismatch")
                        return client
                    except CoreError as exc:
                        if exc.code not in {"worker_unavailable", "worker_indeterminate"}: raise
                    await asyncio.sleep(.025)
                raise CoreError("worker_unavailable", "Private worker readiness deadline exceeded", dispatched=False)
            except BaseException:
                await self._stop(agent_id)
                raise

    async def status(self, agent_id):
        config = self.config(agent_id)
        process = self._processes.get(agent_id)
        return {"agent_id": agent_id, "state": "running" if process is not None and process.returncode is None else "stopped",
                "pid": process.pid if process is not None and process.returncode is None else None,
                "isolation": "linux_namespaces" if config.mode == "hosted" else "trusted_local_process",
                "network": "shared", "exit_code": process.returncode if process is not None else None}

    async def _stop(self, agent_id):
        process = self._processes.get(agent_id)
        if process is not None and process.returncode is None:
            try: os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            try: await asyncio.wait_for(process.wait(), 5)
            except asyncio.TimeoutError:
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                await process.wait()
        drains = self._drains.pop(agent_id, [])
        for task in drains: task.cancel()
        await asyncio.gather(*drains, return_exceptions=True)

    async def stop(self, agent_id):
        async with self._locks.setdefault(identifier(agent_id), asyncio.Lock()):
            await self._stop(agent_id)
            return await self.status(agent_id)

    async def delete(self, agent_id):
        async with self._locks.setdefault(identifier(agent_id), asyncio.Lock()):
            await self._stop(agent_id)
            config = self.config(agent_id)
            if config.root.parent.exists():
                await asyncio.to_thread(shutil.rmtree, config.root.parent)
            self._clients.pop(agent_id, None); self._processes.pop(agent_id, None)
            return {"agent_id": agent_id, "deleted": True}

    async def close(self):
        if self._closed: return
        self._closed = True
        await asyncio.gather(*(self.stop(agent) for agent in tuple(self._processes)))
        self._ownership.close()
