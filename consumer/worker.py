"""Authenticated private application host with durable anti-replay receipts."""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import hashlib
import hmac
import os
from pathlib import Path
import re
import signal
import time

from agent.framing import read_frame, write_frame
from agent.protocol import CoreError, canonical_json, strict_loads
from agent.state import Journal
from consumer.contracts import MAX_BODY, VERSION, failure, identifier, object_body, success
from consumer.workers import SECRET_METHODS, WorkerConfig, _directory_fd, _read_private, signature


class WorkerServer:
    def __init__(self, config, application, key):
        self.config, self.application, self.key = config, application, key
        self.journal = Journal(config.private_root / "transport.sqlite")
        self.server = None; self.tasks = set(); self.draining = False
        self.active_commands = 0; self._claim_lock = asyncio.Lock()

    async def start(self):
        def recover():
            for row in self.journal.list("ipc.requests", self.config.scope):
                if row.value["state"] == "running":
                    self.journal.put("ipc.requests", row.id, self.config.scope,
                        {**row.value, "state": "indeterminate", "response": None}, row.revision)
        await asyncio.to_thread(recover)
        path = self.config.private_root / "worker.sock"
        path.unlink(missing_ok=True)
        fd = _directory_fd(self.config.private_root)
        try:
            self.server = await asyncio.start_unix_server(self._connection,
                path=f"/proc/self/fd/{fd}/worker.sock", limit=MAX_BODY + 4)
        finally:
            os.close(fd)
        path.chmod(0o600)
        return self

    def _authenticate(self, frame):
        names = {"version", "request_id", "method", "params", "deadline", "nonce", "signature"}
        object_body(frame, names, names)
        unsigned = {k: v for k, v in frame.items() if k != "signature"}
        if not isinstance(frame["signature"], str) or not hmac.compare_digest(frame["signature"], signature(self.key, unsigned)):
            raise CoreError("unauthenticated", "Private request authentication failed")
        identifier(frame["request_id"]); identifier(frame["nonce"])
        if frame["version"] != VERSION or not isinstance(frame["params"], dict):
            raise CoreError("invalid_request", "Invalid private request version or parameters")
        if not isinstance(frame["method"], str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,127}", frame["method"]):
            raise CoreError("invalid_request", "Invalid private command name")
        now = time.time()
        if type(frame["deadline"]) not in {int, float} or not now < frame["deadline"] <= now + 301:
            raise CoreError("expired_request", "Private request deadline is invalid or expired")
        digest = hashlib.sha256(canonical_json({"method": frame["method"], "params": frame["params"]}).encode()).hexdigest()
        with self.journal.transaction():
            nonce = frame["nonce"]
            if self.journal.get("ipc.nonces", nonce, self.config.scope):
                raise CoreError("replayed_request", "Private frame nonce was already used")
            for row in self.journal.list("ipc.nonces", self.config.scope):
                if row.value["expires_at"] < now:
                    self.journal.delete("ipc.nonces", row.id, self.config.scope, row.revision)
            if len(self.journal.list("ipc.nonces", self.config.scope)) >= 10000:
                raise CoreError("worker_busy", "Private anti-replay window is full")
            self.journal.put("ipc.nonces", nonce, self.config.scope, {"expires_at": frame["deadline"]})
            prior = self.journal.get("ipc.requests", frame["request_id"], self.config.scope)
            if prior and prior.value["digest"] != digest:
                raise CoreError("conflict", "Request identifier was reused with different content")
            return prior, digest

    def _claim(self, frame, digest):
        return self.journal.put("ipc.requests", frame["request_id"], self.config.scope,
            {"digest": digest, "method": frame["method"], "state": "running", "response": None})

    def _finish(self, request_id, response, state, *, transient=False):
        with self.journal.transaction():
            row = self.journal.get("ipc.requests", request_id, self.config.scope)
            self.journal.put("ipc.requests", request_id, self.config.scope,
                {**row.value, "state": state, "response": None if transient else response}, row.revision)

    async def _snapshot(self):
        snapshot = await self.application.lifecycle_snapshot()
        return {**snapshot, "agent_id": self.config.agent_id, "scope": self.config.scope,
                "draining": self.draining, "active_commands": self.active_commands}

    async def _internal(self, method, params):
        if method == "_worker.receipt":
            object_body(params, {"request_id", "digest"}, {"request_id", "digest"})
            identifier(params["request_id"])
            row = await asyncio.to_thread(self.journal.get, "ipc.requests", params["request_id"], self.config.scope)
            if row is None:
                raise CoreError("result_unavailable", "Original worker receipt is unavailable; command was not replayed")
            if row.value["digest"] != params["digest"]:
                raise CoreError("conflict", "Receipt digest does not match the original command")
            if row.value["response"] is None:
                raise CoreError("result_unavailable" if row.value["state"] == "completed" else "worker_indeterminate",
                                "Original command has no retained terminal response", prior_state=row.value["state"])
            return {"state": row.value["state"], "response": row.value["response"]}
        if params:
            raise CoreError("invalid_request", "Lifecycle commands take no parameters")
        if method == "_worker.status": return await self._snapshot()
        if method == "_worker.quiesce":
            self.draining = True
            try:
                await self.application.prepare_suspend()
                return await self._snapshot()
            except BaseException:
                self.draining = False
                await self.application.resume_admission()
                raise
        if method == "_worker.resume":
            await self.application.resume_admission()
            self.draining = False
            return await self._snapshot()
        raise CoreError("unknown_command", "Unknown private lifecycle command")

    async def _dispatch(self, frame):
        async with self._claim_lock:
            prior, digest = await asyncio.to_thread(self._authenticate, frame)
            if prior:
                if prior.value["response"] is not None:
                    return prior.value["response"]
                if prior.value["state"] == "completed":
                    raise CoreError("result_unavailable", "Sensitive command completed; its transient response is not retained")
                raise CoreError("worker_indeterminate", "A prior command attempt has no durable terminal response", prior_state=prior.value["state"])
            if not frame["method"].startswith("_worker.") and self.draining:
                raise CoreError("worker_draining", "Private runtime admission is paused", effects="none")
            await asyncio.to_thread(self._claim, frame, digest)
        request_id, method = frame["request_id"], frame["method"]
        internal = method.startswith("_worker.")
        if not internal: self.active_commands += 1
        try:
            async with asyncio.timeout(max(.001, frame["deadline"] - time.time())):
                if internal:
                    data = await self._internal(method, frame["params"])
                else:
                    data = await self.application.command(method, frame["params"], request_id=request_id)
            response, state = success(data, request_id), "completed"
        except (asyncio.TimeoutError, asyncio.CancelledError):
            response, state = failure(CoreError("worker_indeterminate", "Command interrupted before a terminal acknowledgment"), request_id), "indeterminate"
        except Exception as exc:
            response, state = failure(exc, request_id), "failed"
        finally:
            if not internal: self.active_commands -= 1
        if len(canonical_json(response).encode()) > MAX_BODY - 512:
            response, state = failure(CoreError("response_too_large", "Command result exceeds the private transport bound"), request_id), "failed"
        await asyncio.to_thread(self._finish, request_id, response, state, transient=method in SECRET_METHODS)
        return response

    async def _connection(self, reader, writer):
        task = asyncio.current_task()
        if len(self.tasks) >= 128:
            writer.close(); await writer.wait_closed(); return
        self.tasks.add(task)
        frame = None
        try:
            async with asyncio.timeout(305):
                raw = await read_frame(reader, MAX_BODY)
                if raw is None: return
                frame = strict_loads(raw)
                try: response = await self._dispatch(frame)
                except Exception as exc:
                    request_id = None
                    if isinstance(frame, dict):
                        try: request_id = identifier(frame.get("request_id"))
                        except CoreError: pass
                    response = failure(exc, request_id)
                nonce = frame.get("nonce") if isinstance(frame, dict) else None
                signed = {"response": response, "nonce": nonce}
                await write_frame(writer, canonical_json({**signed, "signature": signature(self.key, signed, response=True)}).encode(), MAX_BODY)
        except (CoreError, OSError, asyncio.TimeoutError):
            pass  # Malformed/truncated transport is closed without reflecting private data.
        finally:
            writer.close()
            try: await writer.wait_closed()
            except OSError: pass
            self.tasks.discard(task)

    async def close(self):
        if self.server:
            self.server.close(); await self.server.wait_closed()
        tasks = tuple(self.tasks)
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        (self.config.private_root / "worker.sock").unlink(missing_ok=True)
        self.journal.close()


async def run(config):
    from consumer.application import AgentApplication
    key = _read_private(config.private_root / "ipc.key", 32)
    if len(key) != 32:
        raise CoreError("invalid_config", "Invalid private worker key")
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT): loop.add_signal_handler(sig, stopped.set)
    application = AgentApplication(config)
    server = None
    try:
        await application.start()
        server = await WorkerServer(config, application, key).start()
        await stopped.wait()
    finally:
        if server is not None: await server.close()
        await application.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Private Sturgis worker; launched by its lifecycle manager")
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    config = WorkerConfig.from_dict(strict_loads(_read_private(Path(args.config), 65536)))
    # An orphaned trusted-local process must not outlive the placement owner.
    # Hosted bubblewrap also provides --die-with-parent for its process tree.
    parent = os.getppid()
    if (ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGTERM) != 0
            or parent == 1 and config.mode != "hosted" or os.getppid() != parent):
        raise CoreError("ownership_lost", "Private worker parent ownership is unavailable")
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
