"""One subprocess transport with correlated requests and independent async readers."""
from __future__ import annotations

import asyncio
import math
from pathlib import Path

from agent.context import ContextBlock
from agent.framing import encode_frame, read_frame
from agent.protocol import CoreError, canonical_json, new_id, strict_loads, validate_scope


STATUSES = {"ok", "partial", "unavailable", "conflict", "invalid_request"}


def _unavailable(request_id, code, message, dispatched=False):
    return {"protocol_version": "memory/1", "request_id": request_id, "status": "unavailable", "data": None,
            "error": {"code": code, "message": message, "dispatched": dispatched,
                      "effect_state": "unknown" if dispatched else "not_dispatched"},
            "generation": None, "watermark": None}


class NativeMemoryClient:
    def __init__(self, db_path, *, binary=".build/native/sturgis-memory", timeout=5.0,
                 max_pending=128, max_frame=1048576):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise CoreError("memory_config", "Request timeout must be finite and positive")
        if type(max_pending) is not int or not 1 <= max_pending <= 10000:
            raise CoreError("memory_config", "Pending request bound must be 1..10000")
        if type(max_frame) is not int or not 1 <= max_frame <= 1048576:
            raise CoreError("memory_config", "Frame bound must be 1..1048576")
        self.db_path, self.binary = str(db_path), str(Path(binary).resolve())
        self.timeout, self.max_pending, self.max_frame = timeout, max_pending, max_frame
        self.available, self.diagnostic = False, ""
        self.process = None
        self._pending = {}
        self._writer_lock, self._start_lock = asyncio.Lock(), asyncio.Lock()
        self._reader_task = self._stderr_task = None
        self._closed = False

    async def start(self):
        async with self._start_lock:
            if self._closed:
                return False
            if self.available and self.process is not None and self.process.returncode is None:
                return True
            await self._stop_process()
            try:
                self.process = await asyncio.create_subprocess_exec(
                    self.binary, "--db", self.db_path, stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    limit=self.max_frame + 4,
                )
            except OSError as exc:
                self.diagnostic = str(exc)[-4096:]
                return False
            self.available = True
            self._reader_task = asyncio.create_task(self._read_responses(self.process))
            self._stderr_task = asyncio.create_task(self._read_diagnostics(self.process))
            try:
                response = await self._exchange("memory-health", "ping", {}, self.timeout)
            except asyncio.CancelledError:
                self.available = False
                raise
            self.available = response["status"] == "ok"
            if not self.available:
                await self._stop_process()
            return self.available

    async def _read_diagnostics(self, process):
        while data := await process.stderr.read(1024):
            self.diagnostic = (self.diagnostic + data.decode(errors="replace"))[-4096:]

    async def _read_responses(self, process):
        message = "Memory worker closed its response stream"
        try:
            while (payload := await read_frame(process.stdout, self.max_frame)) is not None:
                response = strict_loads(payload)
                required = {"protocol_version", "request_id", "status", "data", "error", "generation", "watermark"}
                if (not isinstance(response, dict) or set(response) != required
                        or response["protocol_version"] != "memory/1" or response["status"] not in STATUSES
                        or not isinstance(response["request_id"], str)
                        or response["data"] is not None and not isinstance(response["data"], dict)
                        or response["error"] is not None and not isinstance(response["error"], dict)
                        or any(value is not None and (type(value) is not int or value < 0)
                               for value in (response["generation"], response["watermark"]))):
                    raise CoreError("memory_protocol", "Malformed native response envelope")
                pending = self._pending.get(response["request_id"])
                # Cancelled or expired requests can finish later; their frames must still be drained.
                if pending is not None and not pending[0].done():
                    pending[0].set_result(response)
        except (CoreError, OSError, ValueError, TypeError) as exc:
            message = str(exc)
        finally:
            if self.process is process:
                self.available = False
                for request_id, (future, sent) in tuple(self._pending.items()):
                    if not future.done():
                        future.set_result(_unavailable(request_id, "memory_unavailable", message, sent))

    async def _stop_process(self):
        process = self.process
        if process is not None:
            if process.stdin and not process.stdin.is_closing():
                process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 1.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self._reader_task = self._stderr_task = None
        self.process = None
        self.available = False

    async def close(self):
        self._closed = True
        async with self._start_lock:
            await self._stop_process()

    async def request(self, scope, operation, arguments=None, *, timeout=None):
        validate_scope(scope)
        if not isinstance(operation, str) or not operation or not isinstance(arguments if arguments is not None else {}, dict):
            raise CoreError("memory_request", "Operation and argument object are required")
        budget = self.timeout if timeout is None else timeout
        if type(budget) not in (int, float) or not math.isfinite(budget) or budget <= 0:
            raise CoreError("memory_request", "Request deadline must be finite and positive")
        deadline = asyncio.get_running_loop().time() + budget
        try:
            async with asyncio.timeout(budget):
                ready = await self.start()
        except asyncio.TimeoutError:
            return _unavailable(None, "deadline", "Memory startup/admission deadline exceeded")
        if not ready:
            return _unavailable(None, "memory_unavailable", self.diagnostic or "Memory client is closed")
        return await self._exchange(scope, operation, arguments or {}, deadline - asyncio.get_running_loop().time())

    async def _exchange(self, scope, operation, arguments, budget):
        request_id = new_id("memreq")
        if len(self._pending) >= self.max_pending:
            return _unavailable(request_id, "capacity", "Memory request queue is full")
        body = canonical_json({"protocol_version": "memory/1", "request_id": request_id,
                               "scope": scope, "operation": operation, "arguments": arguments}).encode()
        frame = encode_frame(body, self.max_frame)
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = [future, False]
        sent = False
        try:
            async with asyncio.timeout(max(0, budget)):
                async with self._writer_lock:
                    if self.process is None or self.process.returncode is not None or not self.available:
                        return _unavailable(request_id, "memory_unavailable", "Memory process is not available")
                    # Mark dispatch before write: partial pipe writes make the effect uncertain.
                    sent = self._pending[request_id][1] = True
                    self.process.stdin.write(frame)
                    await self.process.stdin.drain()
                return await asyncio.shield(future)
        except asyncio.TimeoutError:
            return _unavailable(request_id, "deadline", "Memory request deadline exceeded", sent)
        except (OSError, ConnectionError) as exc:
            self.available = False
            return _unavailable(request_id, "memory_unavailable", str(exc), sent)
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()

    async def ingest(self, scope, record, *, operation_id, expected_revision=0, timeout=None):
        return await self.request(scope, "ingest", {"operation_id": operation_id, "record": record,
                                  "expected_revision": expected_revision}, timeout=timeout)

    async def query(self, scope, *, text="", concepts=None, embedding=None, at=None, limit=20,
                    lease_id=None, timeout=None):
        arguments = {"text": text, "limit": limit}
        for key, value in {"concepts": concepts, "embedding": embedding, "at": at, "lease_id": lease_id}.items():
            if value is not None:
                arguments[key] = value
        return await self.request(scope, "query", arguments, timeout=timeout)


def memory_blocks(scope, response):
    """Return context blocks plus explicit freshness; retrieved text never gets an instruction role."""
    validate_scope(scope)
    data = response.get("data") or {}
    current, source = response.get("watermark"), data.get("source_watermark")
    metadata = {"status": response["status"], "generation": data.get("generation", response.get("generation")),
                "source_watermark": source, "current_watermark": current,
                "stale": source < current if type(source) is int and type(current) is int else None,
                "deferred": response["status"] != "ok", "sources": {}}
    blocks = []
    for match in data.get("records", []):
        record = match["record"]
        if record.get("scope") != scope or record.get("retracted"):
            raise CoreError("memory_scope", "Memory result is retracted or belongs to another scope")
        block_id = "memory:" + record["id"] + ":" + str(record["revision"])
        blocks.append(ContextBlock(block_id, scope, "memory", record["content"], revision=record["revision"], origin="retrieved"))
        metadata["sources"][block_id] = {"kind": record["kind"], "evidence": record["sources"],
                                        "source_time": record["source_time"], "watermark": record["watermark"]}
    return blocks, metadata
