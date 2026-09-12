"""Canonical recorded tool dispatch over explicit restricted executors."""

import asyncio
import base64
from dataclasses import dataclass, field
import inspect
import math
import time
from pathlib import Path

from agent.plugin_sdk import Capability, validate_schema
from agent.protocol import CoreError, ToolCall, ToolResult, canonical_json, new_id
from .files import WorkspaceFs, digest
from .process import ProcessRunner


@dataclass(frozen=True)
class ExecutionContext:
    scope: str
    work_id: str
    generation: int
    action_id: str
    attempt_id: str
    cancel: asyncio.Event
    constraints: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutorResult:
    data: object
    outcome: str = "success"
    effects: str = "unknown"


class ToolDispatcher:
    def __init__(self, journal, *, authorize):
        if not callable(authorize):
            raise CoreError("invalid_authority", "A scheduler authorization callback is mandatory")
        self.journal, self.authorize = journal, authorize
        self.registry = {}

    def register(self, name, executor, *, input_schema, output_schema, version="1",
                 required_capabilities=(), read_only=False, timeout=30, output_limit=1048576):
        descriptor = Capability(name, input_schema, output_schema, tuple(required_capabilities), read_only, timeout, output_limit)
        if (not (inspect.iscoroutinefunction(executor) or inspect.iscoroutinefunction(getattr(executor, "__call__", None)))
                or not isinstance(version, str) or not version or (name, version) in self.registry):
            raise CoreError("invalid_registration", "Executor/version must be unique and valid")
        self.registry[name, version] = descriptor, executor

    def descriptors(self):
        return [{**descriptor.as_dict(), "version": version} for (name, version), (descriptor, _) in sorted(self.registry.items())]

    def _persist(self, scope, request_id, body):
        with self.journal.transaction():
            prior = self.journal.get("tool_requests", request_id, scope)
            self.journal.put("tool_requests", request_id, scope, body, expected_revision=prior.revision if prior else 0)
            self.journal.append(scope, "tool.result", body["result"], source="tool_runner")
            if body.get("usage") is not None:
                self.journal.append(scope, "tool.usage", {"work_id": body["work_id"],
                    "claim_id": body["result"]["attempt_id"], "resources": body["usage"]},
                    source="tool_runner", dedupe_key="tool-usage:" + body["result"]["attempt_id"])

    def _claim(self, call, descriptor, scope, work_id, generation, request_id):
        fingerprint = canonical_json({"call": call.as_dict(), "work_id": work_id, "generation": generation})
        with self.journal.transaction():
            prior = self.journal.get("tool_requests", request_id, scope)
            if prior:
                if prior.value["fingerprint"] != fingerprint:
                    raise CoreError("dedupe_conflict", "Request ID was reused with another invocation")
                return prior.value, False
            action_id, attempt_id = new_id("action"), new_id("attempt")
            grant = {"work_id": work_id, "generation": generation, "claim_id": attempt_id,
                     "action_id": action_id, "attempt_id": attempt_id,
                     "required_capabilities": list(descriptor.required_capabilities),
                     "resources": {"tool_calls": 1, "elapsed_ms": math.ceil(descriptor.timeout * 1000)}}
            self.journal.append(scope, "tool.request", {"action_id": action_id, "attempt_id": attempt_id,
                                "call": call.as_dict(), "work_id": work_id}, source="tool_runner")
            authority = self.authorize(scope, grant, call)
            if not isinstance(authority, dict):
                raise CoreError("invalid_authority", "Scheduler must return an authorization record")
            if authority.get("constraints", {}).get("workspace_root") and not getattr(
                    self.registry[call.tool, call.tool_version][1], "supports_constraints", False):
                raise CoreError("workspace_incompatible", "Executor does not enforce delegated workspace constraints")
            result = ToolResult(action_id, attempt_id, call.tool, "running", None).as_dict()
            body = {"fingerprint": fingerprint, "call": call.as_dict(), "work_id": work_id,
                    "generation": generation, "authorization": authority, "result": result,
                    "backend_kind": getattr(self.registry[call.tool, call.tool_version][1], "backend_kind", "custom")}
            self.journal.put("tool_requests", request_id, scope, body)
            return body, True

    async def invoke(self, call, *, scope, work_id, generation, request_id=None, cancel=None):
        request_id = new_id("request") if request_id is None else request_id
        if not isinstance(request_id, str) or not request_id or len(request_id) > 256:
            raise CoreError("invalid_request", "A bounded request identity is required")
        name = call.tool if isinstance(call, ToolCall) else str(call.get("tool", "invalid")) if isinstance(call, dict) else "invalid"
        try:
            call = ToolCall.from_dict(call.as_dict() if isinstance(call, ToolCall) else call)
            if not isinstance(work_id, str) or not work_id or type(generation) is not int or generation < 1:
                raise CoreError("invalid_authority", "Authenticated work and generation are required")
            descriptor, executor = self.registry.get((call.tool, call.tool_version), (None, None))
            if descriptor is None:
                raise CoreError("unknown_tool", "No compatible tool is registered")
            validate_schema(descriptor.input_schema, call.arguments, label="tool arguments")
            if cancel is not None and cancel.is_set():
                raise CoreError("cancelled", "Action cancelled before claim")
            body, claimed = await asyncio.to_thread(self._claim, call, descriptor, scope, work_id, generation, request_id)
        except CoreError as exc:
            if exc.code == "dedupe_conflict":
                raise
            result = ToolResult(new_id("action"), new_id("attempt"), name, "rejected", "rejected",
                                error=exc.as_dict(), effects={"certainty": "none", "evidence_event_ids": []})
            await asyncio.to_thread(self.journal.append, scope, "tool.result", result.as_dict(), "tool_runner")
            return result
        if not claimed:
            result = ToolResult(**body["result"])
            if result.state in {"accepted", "running"}:
                return ToolResult(result.action_id, result.attempt_id, result.tool, "indeterminate", "unknown",
                                  error={"code": "pending_or_recovery", "message": "Existing attempt must complete or reconcile"})
            return result
        running = ToolResult(**body["result"])
        cancel = cancel if cancel is not None else asyncio.Event()
        context = ExecutionContext(scope, work_id, generation, running.action_id, running.attempt_id, cancel,
            body["authorization"].get("constraints", {}))
        started = time.monotonic()
        job = asyncio.create_task(executor(call.arguments, context))
        cancellation = asyncio.create_task(cancel.wait())
        try:
            done, _ = await asyncio.wait([job, cancellation], timeout=descriptor.timeout, return_when=asyncio.FIRST_COMPLETED)
            if cancellation in done:
                raise CoreError("cancelled", "Action cancellation requested", effects="unknown")
            if job not in done:
                raise CoreError("timeout", "Action deadline exceeded", effects="unknown")
            value = job.result()
            if not isinstance(value, ExecutorResult):
                raise CoreError("invalid_result", "Executor must return ExecutorResult", effects="unknown")
            validate_schema(descriptor.output_schema, value.data, label="tool result")
            if len(canonical_json(value.data).encode()) > descriptor.output_limit:
                raise CoreError("output_limit", "Normalized result exceeds output bound", effects=value.effects)
            effects = {"certainty": "none" if descriptor.read_only else value.effects, "evidence_event_ids": []}
            if effects["certainty"] == "observed":
                evidence = await asyncio.to_thread(self.journal.append, scope, "tool.effect", {
                    "action_id": running.action_id, "attempt_id": running.attempt_id, "data": value.data}, "tool_runner")
                effects["evidence_event_ids"] = [evidence.id]
            result = ToolResult(running.action_id, running.attempt_id, call.tool, "completed", value.outcome,
                                value.data, effects=effects)
        except (CoreError, asyncio.CancelledError, Exception) as exc:
            code = exc.code if isinstance(exc, CoreError) else "cancelled" if isinstance(exc, asyncio.CancelledError) else "executor_error"
            confirmed = isinstance(exc, CoreError) and (exc.details.get("execution_stopped") or exc.details.get("effects") == "none")
            state = "cancelled" if code == "cancelled" and confirmed else "indeterminate" if code in {"timeout", "cancelled"} else "completed"
            outcome = "cancelled" if state == "cancelled" else "unknown" if state == "indeterminate" else "execution_error"
            result = ToolResult(running.action_id, running.attempt_id, call.tool, state, outcome,
                    error=exc.as_dict() if isinstance(exc, CoreError) else {"code": code, "message": str(exc)},
                    effects={"certainty": exc.details.get("effects", "unknown") if isinstance(exc, CoreError) else "unknown", "evidence_event_ids": []})
        finally:
            job.cancel()
            cancellation.cancel()
            await asyncio.gather(job, cancellation, return_exceptions=True)
        body["result"] = result.as_dict()
        body["observed_resources"] = {"tool_calls": 1, "elapsed_ms": math.ceil((time.monotonic() - started) * 1000)}
        if result.state in {"completed", "cancelled"}:
            body["usage"] = body["observed_resources"]
        await asyncio.to_thread(self._persist, scope, request_id, body)
        return result

    def recover(self, scope):
        recovered = []
        for row in self.journal.list("tool_requests", scope):
            result = ToolResult(**row.value["result"])
            if result.state in {"accepted", "running"}:
                result = ToolResult(result.action_id, result.attempt_id, result.tool, "indeterminate", "unknown",
                                    error={"code": "restart", "message": "Interrupted attempt requires reconciliation"})
                body = {**row.value, "result": result.as_dict()}
                self._persist(scope, row.id, body)
                recovered.append(result)
        return recovered

    def reconcile(self, scope, request_id, result, *, evidence_event_ids, usage=None):
        """Admit causal terminal observations from the trusted local tool observer."""
        with self.journal.transaction():
            row = self.journal.get("tool_requests", request_id, scope)
            if row is None or row.value["result"]["state"] != "indeterminate":
                raise CoreError("invalid_state", "Only indeterminate attempts may be reconciled")
            prior = row.value["result"]
            if (not isinstance(result, ToolResult) or not isinstance(evidence_event_ids, (list, tuple))
                    or not 1 <= len(evidence_event_ids) <= 64
                    or any(not isinstance(key, str) or not key or len(key) > 256 for key in evidence_event_ids)
                    or len(set(evidence_event_ids)) != len(evidence_event_ids)):
                raise CoreError("invalid_evidence", "Reconciliation requires linked terminal evidence")
            evidence_event_ids = list(evidence_event_ids)
            proposed = ToolResult(**result.as_dict()).as_dict()
            if (any(proposed[key] != prior[key] for key in ("action_id", "attempt_id", "tool"))
                    or proposed["state"] not in {"completed", "cancelled"}
                    or set(proposed["effects"]) != {"certainty", "evidence_event_ids"}
                    or proposed["effects"]["evidence_event_ids"] not in ([], evidence_event_ids)):
                raise CoreError("invalid_evidence", "Reconciliation result must name this attempt and admitted evidence")
            # Evidence IDs are attached by admission, avoiding a circular event ID
            # inside the observer's own canonical result and refusing unrelated IDs.
            proposed["effects"]["evidence_event_ids"] = []
            resources = usage if usage is not None else row.value.get("observed_resources")
            if resources is not None and (not isinstance(resources, dict) or set(resources) != {"tool_calls", "elapsed_ms"}
                    or any(type(value) is not int or value < 0 for value in resources.values())
                    or resources["tool_calls"] != 1):
                raise CoreError("invalid_usage", "Reconciliation requires actual nonnegative resource evidence")
            for evidence_id in evidence_event_ids:
                event = self.journal.event(scope, evidence_id)
                if event is None or event.kind != "tool.observation" or event.source != "tool_observer":
                    raise CoreError("invalid_evidence", "Reconciliation requires scoped trusted tool observations")
                request_event_id = event.payload.get("request_event_id")
                request_event = self.journal.event(scope, request_event_id) if isinstance(request_event_id, str) else None
                request_payload = {"action_id": prior["action_id"], "attempt_id": prior["attempt_id"],
                                   "call": row.value["call"], "work_id": row.value["work_id"]}
                if (request_event is None or request_event.kind != "tool.request" or request_event.source != "tool_runner"
                        or request_event.seq >= event.seq or canonical_json(request_event.payload) != canonical_json(request_payload)):
                    raise CoreError("invalid_evidence", "Observation must follow the exact recorded tool request")
                expected = {"schema_version": "tool-observation/1", "request_id": request_id,
                            "work_id": row.value["work_id"], "request_event_id": request_event.id,
                            "result": proposed, "usage": resources}
                if canonical_json(event.payload) != canonical_json(expected):
                    raise CoreError("invalid_evidence", "Observation does not attest this terminal result and measured usage")
            proposed["effects"]["evidence_event_ids"] = evidence_event_ids
            admitted = ToolResult(**proposed)
            self._persist(scope, request_id, {**row.value, "result": admitted.as_dict(), "usage": resources,
                "reconciliation": {"prior_result": prior, "evidence_event_ids": evidence_event_ids}})
            self.journal.append(scope, "action.reconciled", {"request_id": request_id,
                                "action_id": prior["action_id"], "attempt_id": prior["attempt_id"],
                                "tool": prior["tool"], "evidence_event_ids": evidence_event_ids}, source="tool_runner")
            return admitted


def process_executor(runner):
    async def execute(arguments, context):
        backend = runner
        if context.constraints.get("workspace_root"):
            root = _constrained_root(context)
            if any(not source.is_relative_to(root) for source, _, _ in runner.mounts):
                raise CoreError("scope_denied", "Delegated process mounts exceed its workspace", effects="none")
            backend = ProcessRunner(root, runner.programs, environment_keys=runner.environment_keys,
                mounts=[(source, destination, write and context.constraints.get("workspace_writable", False))
                        for source, destination, write in runner.mounts],
                writable=runner.writable and context.constraints.get("workspace_writable", False))
        result = await backend.run(arguments["argv"], cwd=arguments.get("cwd", "."),
                                  environment=arguments.get("environment"), timeout=arguments.get("timeout", 30),
                                  output_limit=arguments.get("output_limit", 1048576), cancel=context.cancel)
        data = dict(result)
        for stream in ("stdout", "stderr"):
            try:
                data[stream], data[stream + "_encoding"] = result[stream].decode(), "utf-8"
            except UnicodeError:
                data[stream], data[stream + "_encoding"] = base64.b64encode(result[stream]).decode(), "base64"
        return ExecutorResult(data, "success" if result["exit_code"] == 0 else "target_error")
    execute.supports_constraints = True
    execute.backend_kind = "process"
    return execute


def file_executor(workspace):
    async def execute(arguments, context):
        operation = arguments["operation"]
        args = {key: value for key, value in arguments.items() if key != "operation"}
        if operation == "write":
            args["data"] = args.pop("content").encode()
        if operation not in {"read", "write", "edit", "delete", "copy", "rename", "list", "search"}:
            raise CoreError("validation", "Unknown filesystem operation", effects="none")
        def operate():
            if context.constraints.get("workspace_root"):
                if operation not in {"read", "list", "search"} and not context.constraints.get("workspace_writable", False):
                    raise CoreError("scope_denied", "Delegated workspace is read-only", effects="none")
                backend = WorkspaceFs(_constrained_root(context), max_bytes=workspace.max_bytes,
                    max_entries=workspace.max_entries)
                return getattr(backend, operation)(**args)
            return getattr(workspace, operation)(**args)
        value = await asyncio.to_thread(operate)
        if operation == "read":
            try:
                content, encoding = value.decode(), "utf-8"
            except UnicodeError:
                content, encoding = base64.b64encode(value).decode(), "base64"
            value = {"content": content, "encoding": encoding, "sha256": digest(value), "bytes": len(value)}
        return ExecutorResult(value, effects="none" if operation in {"read", "list", "search"} else "observed")
    execute.supports_constraints = True
    return execute


def _constrained_root(context):
    path = Path(context.constraints["workspace_root"])
    if not path.is_absolute() or not path.is_dir() or path.resolve() != path:
        raise CoreError("scope_denied", "Delegated workspace changed after admission", effects="none")
    return path
