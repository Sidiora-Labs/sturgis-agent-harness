"""Durable private-runtime desired state, message retention and scheduled wakes."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import time

from agent.protocol import CoreError, canonical_json, new_id
from consumer.contracts import identifier, object_body


CONTROL = "control"
RUNTIMES = "consumer.runtime_lifecycle"
COMMANDS = "consumer.runtime_commands"
PERSISTED_INPUTS = frozenset({"messages.send", "messages.cancel"})


class Lifecycle:
    """Control-plane durability surrounds the existing agent scheduler and runtime.

    Only undispatched message commands are automatically forwarded after recovery.
    Other parameters are transient; durable records hold their digest and status.
    A transport ambiguity never licenses replay of an effectful command.
    """
    def __init__(self, journal, manager, *, poll_interval=.25):
        if type(poll_interval) not in {int, float} or not .01 <= poll_interval <= 60:
            raise CoreError("invalid_config", "Invalid lifecycle polling interval")
        self.journal, self.manager, self.poll_interval = journal, manager, poll_interval
        self._locks = {}; self._wake_tasks = {}; self._loop = None; self._closed = False

    def _row(self, agent_id):
        identifier(agent_id)
        row = self.journal.get(RUNTIMES, agent_id, CONTROL)
        if row is None or row.value["desired"] == "deleted":
            raise CoreError("not_found", "Runtime lifecycle is unavailable")
        return row

    def _update(self, agent_id, **changes):
        with self.journal.transaction():
            row = self._row(agent_id)
            updated = self.journal.put(RUNTIMES, agent_id, CONTROL,
                {**row.value, **changes, "updated_at": time.time()}, row.revision)
            self.journal.append(CONTROL, "runtime.lifecycle", {"agent_id": agent_id,
                "revision": updated.revision, "desired": updated.value["desired"], "observed": updated.value["observed"]},
                source="consumer.lifecycle")
            return updated

    @staticmethod
    def _public(row):
        return {"agent_id": row.id, "revision": row.revision, **row.value}

    async def create(self, agent_id):
        identifier(agent_id)
        async with self._locks.setdefault(agent_id, asyncio.Lock()):
            def record():
                with self.journal.transaction():
                    prior = self.journal.get(RUNTIMES, agent_id, CONTROL)
                    if prior:
                        if prior.value["desired"] == "deleted":
                            raise CoreError("conflict", "A deleted runtime identifier cannot be reused")
                        return prior
                    return self.journal.put(RUNTIMES, agent_id, CONTROL, {"desired": "stopped", "observed": "new",
                        "generation": 0, "next_wake_at": None, "error": None, "updated_at": time.time()})
            row = await asyncio.to_thread(record)
            await self.manager.create(agent_id)
            return self._public(row)

    async def _start_locked(self, agent_id):
        row = await asyncio.to_thread(self._row, agent_id)
        self._admissible(row)
        status = await self.manager.status(agent_id)
        if status["state"] == "running":
            if row.value["desired"] != "running" or row.value["observed"] != "running":
                await asyncio.to_thread(self._update, agent_id, desired="running", observed="running", error=None)
            return self.manager.client(agent_id)
        await asyncio.to_thread(self._update, agent_id, desired="running", observed="starting",
                                generation=row.value["generation"] + 1, error=None)
        try:
            client = await self.manager.start(agent_id)
        except Exception as exc:
            error = {"code": exc.code if isinstance(exc, CoreError) else "worker_unavailable"}
            await asyncio.to_thread(self._update, agent_id, observed="failed", error=error)
            raise
        await asyncio.to_thread(self._update, agent_id, observed="running", next_wake_at=None, error=None)
        return client

    async def start_agent(self, agent_id):
        async with self._locks.setdefault(identifier(agent_id), asyncio.Lock()):
            await self._start_locked(agent_id)
        return await self.status(agent_id)

    async def status(self, agent_id):
        row = await asyncio.to_thread(self._row, agent_id)
        process = await self.manager.status(agent_id)
        result = {**self._public(row), "process": process}
        if process["state"] == "running":
            try:
                result["runtime"] = await self.manager.client(agent_id).request("_worker.status", {}, timeout=1)
            except CoreError as exc:
                result["readiness_error"] = {"code": exc.code}
        return result

    async def suspend(self, agent_id):
        async with self._locks.setdefault(identifier(agent_id), asyncio.Lock()):
            row = await asyncio.to_thread(self._row, agent_id)
            self._admissible(row)
            if (await self.manager.status(agent_id))["state"] != "running":
                if row.value["desired"] == "suspended": return self._public(row)
                # A cold runtime must inspect its durable scheduler before suspension.
                await self._start_locked(agent_id)
            client = self.manager.client(agent_id)
            try:
                snapshot = await client.request("_worker.quiesce", {})
                self._idle(snapshot)
                next_wake = snapshot.get("next_wake_at")
                if next_wake is not None and (type(next_wake) not in {int, float} or next_wake <= time.time()):
                    raise CoreError("runtime_busy", "Runtime has due scheduled work")
                # Persist the next wake before stopping the worker that owns the schedule.
                await asyncio.to_thread(self._update, agent_id, desired="suspended", observed="stopping", next_wake_at=next_wake)
                await self.manager.stop(agent_id)
                stopped = await asyncio.to_thread(self._update, agent_id, observed="suspended", error=None)
                return self._public(stopped)
            except BaseException:
                try: await client.request("_worker.resume", {}, timeout=1)
                except CoreError: pass
                raise

    @staticmethod
    def _admissible(row):
        if row.value["desired"] == "archived":
            raise CoreError("runtime_archived", "Archived agents cannot run commands or scheduled work")
        if row.value["desired"] == "restoring":
            raise CoreError("runtime_restoring", "Agent restoration has not been published")

    @asynccontextmanager
    async def snapshot(self, agent_id):
        """Hold admission ownership through a stopped-worker filesystem snapshot."""
        async with self._locks.setdefault(identifier(agent_id), asyncio.Lock()):
            original = await asyncio.to_thread(self._row, agent_id)
            if original.value["desired"] == "restoring":
                raise CoreError("runtime_restoring", "Agent restoration has not been published")
            commands = await asyncio.to_thread(self.journal.list, COMMANDS, CONTROL)
            if any(r.value["agent_id"] == agent_id and (r.value["state"] == "dispatching" or
                    r.value["state"] == "queued" and r.value["params"] is not None) for r in commands):
                raise CoreError("runtime_busy", "Snapshot requires settled command admission")
            process = await self.manager.status(agent_id)
            running = process["state"] == "running"
            client = self.manager.client(agent_id) if running else None
            stopped = False
            try:
                if client is not None:
                    self._idle(await client.request("_worker.quiesce", {}))
                    await self.manager.stop(agent_id)
                    stopped = True
                yield self.manager.config(agent_id)
            finally:
                if running and original.value["desired"] != "archived":
                    if stopped: await self.manager.start(agent_id)
                    else:
                        try: await client.request("_worker.resume", {}, timeout=1)
                        except CoreError: pass

    @staticmethod
    def _idle(snapshot):
        required = ("active_work", "queued_work", "pending_deliveries", "active_commands")
        if any(type(snapshot.get(k)) is not int or snapshot[k] < 0 for k in required):
            raise CoreError("worker_protocol", "Runtime cannot establish an idle snapshot")
        if any(snapshot[k] for k in required):
            raise CoreError("runtime_busy", "Runtime has accepted work or undelivered output", **{k: snapshot[k] for k in required})

    async def archive(self, agent_id, *, finalize=None):
        if finalize is not None and not callable(finalize):
            raise CoreError("invalid_request", "Archive finalizer must be a synchronous transaction callback")
        def persist():
            with self.journal.transaction():
                if finalize is not None: finalize()
                return self._update(agent_id, desired="archived", observed="stopping", next_wake_at=None)
        async with self._locks.setdefault(identifier(agent_id), asyncio.Lock()):
            row = await asyncio.to_thread(self._row, agent_id)
            if row.value["desired"] == "archived":
                if finalize is not None: row = await asyncio.to_thread(persist)
                await self.manager.stop(agent_id)
                if row.value["observed"] != "archived":
                    row = await asyncio.to_thread(self._update, agent_id, observed="archived", error=None)
                return self._public(row)
            commands = await asyncio.to_thread(self.journal.list, COMMANDS, CONTROL)
            if any(r.value["agent_id"] == agent_id and (r.value["state"] == "dispatching" or
                    r.value["state"] == "queued" and r.value["params"] is not None) for r in commands):
                raise CoreError("runtime_busy", "Runtime has commands awaiting durable admission or acknowledgment")
            process = await self.manager.status(agent_id)
            # Successful suspension already established idle state. Do not wake a
            # suspended agent merely to archive it after its next schedule is due.
            cold_idle = row.value["desired"] == "suspended" or (
                row.value["desired"] == "stopped" and row.value["observed"] == "new")
            client = None; archived = False
            try:
                if process["state"] == "running" or not cold_idle:
                    client = await self._start_locked(agent_id)
                    self._idle(await client.request("_worker.quiesce", {}))
                committing = asyncio.create_task(asyncio.to_thread(persist))
                try:
                    await asyncio.shield(committing)
                except asyncio.CancelledError:
                    await committing
                    archived = True
                    raise
                archived = True
                await self.manager.stop(agent_id)
                row = await asyncio.to_thread(self._update, agent_id, observed="archived", error=None)
                return self._public(row)
            except BaseException:
                if client is not None and not archived:
                    try: await client.request("_worker.resume", {}, timeout=1)
                    except CoreError: pass
                raise

    async def recover(self, agent_id):
        async with self._locks.setdefault(identifier(agent_id), asyncio.Lock()):
            self._admissible(await asyncio.to_thread(self._row, agent_id))
            await self.manager.stop(agent_id)
            await self._start_locked(agent_id)
        return await self.status(agent_id)

    async def delete(self, agent_id):
        async with self._locks.setdefault(identifier(agent_id), asyncio.Lock()):
            await asyncio.to_thread(self._row, agent_id)
            await self.manager.delete(agent_id)
            row = await asyncio.to_thread(self._update, agent_id, desired="deleted", observed="deleted", next_wake_at=None)
            return self._public(row)

    def _prepare_command(self, agent_id, method, params, request_id):
        self._admissible(self._row(agent_id))
        digest = hashlib.sha256(canonical_json({"method": method, "params": params}).encode()).hexdigest()
        key = agent_id + ":" + request_id
        with self.journal.transaction():
            prior = self.journal.get(COMMANDS, key, CONTROL)
            if prior:
                if prior.value["digest"] != digest:
                    raise CoreError("conflict", "Command identifier was reused with different content")
                return prior
            return self.journal.put(COMMANDS, key, CONTROL, {"agent_id": agent_id, "request_id": request_id,
                "method": method, "digest": digest, "params": params if method in PERSISTED_INPUTS else None,
                "state": "queued", "response": None, "error": None, "created_at": time.time()})

    def _command_state(self, key, state, *, response=None, error=None):
        with self.journal.transaction():
            row = self.journal.get(COMMANDS, key, CONTROL)
            return self.journal.put(COMMANDS, key, CONTROL,
                {**row.value, "state": state, "response": response, "error": error,
                 "params": None if state == "completed" else row.value["params"]}, row.revision)

    async def _receipt_locked(self, row):
        client = await self._start_locked(row.value["agent_id"])
        receipt = await client.request("_worker.receipt", {"request_id": row.value["request_id"],
            "digest": row.value["digest"]})
        response = receipt["response"]
        if response["error"] is not None:
            error = response["error"]
            state = "indeterminate" if error["code"] == "worker_indeterminate" else "failed"
            await asyncio.to_thread(self._command_state, row.id, state, error={"code": error["code"]})
            raise CoreError(error["code"], error["message"], **error.get("details", {}))
        await asyncio.to_thread(self._command_state, row.id, "completed")
        return response["data"]

    async def _forward(self, row, params, timeout):
        agent_id, request_id, method = row.value["agent_id"], row.value["request_id"], row.value["method"]
        async with self._locks.setdefault(agent_id, asyncio.Lock()):
            self._admissible(await asyncio.to_thread(self._row, agent_id))
            current = await asyncio.to_thread(self.journal.get, COMMANDS, row.id, CONTROL)
            if current.value["state"] == "completed":
                return await self._receipt_locked(current)
            if current.value["state"] == "failed":
                error = current.value["error"]
                raise CoreError(error["code"], "Previous command failed; submit a new request after correction")
            if current.value["state"] != "queued":
                return await self._receipt_locked(current)
            client = await self._start_locked(agent_id)
            await asyncio.to_thread(self._command_state, row.id, "dispatching")
        try:
            result = await client.request(method, params, request_id=request_id, timeout=timeout)
        except CoreError as exc:
            retryable = exc.code == "worker_draining" or exc.details.get("dispatched") is False
            state = "queued" if retryable else "failed" if exc.details.get("worker_response") and exc.code != "worker_indeterminate" else "indeterminate"
            await asyncio.to_thread(self._command_state, row.id, state, error={"code": exc.code})
            raise
        except asyncio.CancelledError:
            await asyncio.to_thread(self._command_state, row.id, "indeterminate", error={"code": "cancelled"})
            raise
        await asyncio.to_thread(self._command_state, row.id, "completed")
        return result

    async def command(self, agent_id, method, params, request_id=None, timeout=None):
        identifier(agent_id); request_id = identifier(request_id or new_id("request"))
        if not isinstance(method, str) or not method or method.startswith("_") or len(method) > 128:
            raise CoreError("unknown_command", "Invalid consumer command")
        object_body(params, params.keys() if isinstance(params, dict) else ())
        if method == "messages.send":
            object_body(params, {"conversation_id", "message_id", "text", "attachments", "kind"},
                        {"conversation_id", "message_id", "text"})
        elif method == "messages.cancel":
            object_body(params, {"conversation_id", "message_id"}, {"conversation_id", "message_id"})
        async with self._locks.setdefault(agent_id, asyncio.Lock()):
            row = await asyncio.to_thread(self._prepare_command, agent_id, method, params, request_id)
        return await self._forward(row, params, timeout)

    def _recover_commands(self):
        for row in self.journal.list(COMMANDS, CONTROL):
            if row.value["state"] == "dispatching":
                self._command_state(row.id, "indeterminate", error={"code": "control_restart"})

    async def _wake(self, agent_id):
        try:
            await self.start_agent(agent_id)
            rows = await asyncio.to_thread(self.journal.list, COMMANDS, CONTROL)
            for row in rows:
                if row.value["agent_id"] == agent_id and row.value["state"] == "queued" and row.value["params"] is not None:
                    try: await self._forward(row, row.value["params"], None)
                    except CoreError: pass
        except CoreError:
            pass  # Failure is retained in the lifecycle record; the poller remains alive.

    async def _tick(self):
        rows = await asyncio.to_thread(self.journal.list, RUNTIMES, CONTROL)
        now = time.time()
        for row in rows:
            agent_id, value = row.id, row.value
            if value["desired"] in {"archived", "deleted"}: continue
            due = value["desired"] == "suspended" and value["next_wake_at"] is not None and value["next_wake_at"] <= now
            restart = value["desired"] == "running" and (await self.manager.status(agent_id))["state"] != "running"
            pending = any(r.value["agent_id"] == agent_id and r.value["state"] == "queued" and r.value["params"] is not None
                          for r in await asyncio.to_thread(self.journal.list, COMMANDS, CONTROL))
            if (due or restart or pending) and (agent_id not in self._wake_tasks or self._wake_tasks[agent_id].done()):
                self._wake_tasks[agent_id] = asyncio.create_task(self._wake(agent_id))

    async def start(self):
        if self._closed: raise CoreError("closed", "Lifecycle is closed")
        if self._loop is not None: return self
        await asyncio.to_thread(self._recover_commands)
        async def watch():
            while True:
                await self._tick()
                await asyncio.sleep(self.poll_interval)
        self._loop = asyncio.create_task(watch())
        return self

    async def close(self):
        if self._closed: return
        self._closed = True
        tasks = list(self._wake_tasks.values()) + ([self._loop] if self._loop else [])
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.manager.close()
