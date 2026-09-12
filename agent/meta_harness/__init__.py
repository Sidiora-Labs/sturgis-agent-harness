"""Trace hypotheses and immutable, host-evaluated checks over isolated candidates."""

import asyncio
import base64
from dataclasses import asdict, dataclass
import hashlib
import os
from pathlib import Path
import shutil
import sys

from agent.protocol import CoreError, ToolCall, canonical_json, new_id
from agent.tool_runner import ExecutorResult, ProcessRunner


@dataclass(frozen=True)
class SourceSnapshot:
    id: str
    path: str
    sha256: str
    files: tuple[tuple[str, str], ...]

    def as_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        return cls(value["id"], value["path"], value["sha256"], tuple(tuple(row) for row in value["files"]))


def source_snapshot(path, *, snapshot_id=None):
    root = Path(path).resolve(strict=True)
    if not root.is_dir():
        raise CoreError("invalid_source", "Source root must be a directory")
    files, total, entries = [], 0, 0
    for item in root.rglob("*"):
        entries += 1
        if entries > 2000 or item.is_symlink():
            raise CoreError("invalid_source", "Source tree is oversized or contains a symlink")
        if item.is_file():
            size = item.stat().st_size
            total += size
            if total > 16777216 or len(files) >= 1000:
                raise CoreError("source_limit", "Source tree exceeds its byte/file bound")
            content = item.read_bytes()
            if len(content) != size:
                raise CoreError("source_changed", "Source changed while it was captured")
            files.append((item.relative_to(root).as_posix(), hashlib.sha256(content).hexdigest()))
        elif not item.is_dir():
            raise CoreError("invalid_source", "Only regular source files are supported")
    files = tuple(sorted(files))
    digest = hashlib.sha256(canonical_json(files).encode()).hexdigest()
    return SourceSnapshot(snapshot_id or new_id("snapshot"), str(root), digest, files)


def copy_snapshot(snapshot, destination):
    """Durably copy exact bytes; reject changed sources before any execution."""
    source = source_snapshot(snapshot.path, snapshot_id=snapshot.id)
    if source.sha256 != snapshot.sha256:
        raise CoreError("source_changed", "Source no longer matches its pinned hash")
    destination = Path(destination)
    if destination.exists():
        raise CoreError("conflict", "Snapshot destination already exists")
    destination.mkdir(parents=True)
    try:
        for relative, expected in snapshot.files:
            original = Path(snapshot.path) / relative
            if original.is_symlink():
                raise CoreError("source_changed", "Source acquired a symlink")
            content = original.read_bytes()
            if hashlib.sha256(content).hexdigest() != expected:
                raise CoreError("source_changed", "Source changed during copy")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        result = source_snapshot(destination, snapshot_id=snapshot.id)
        if result.sha256 != snapshot.sha256 or source_snapshot(snapshot.path).sha256 != snapshot.sha256:
            raise CoreError("source_changed", "Snapshot changed while it was copied")
        for directory in [*sorted((p for p in destination.rglob("*") if p.is_dir()), reverse=True), destination, destination.parent]:
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        return result
    except BaseException:
        shutil.rmtree(destination)
        raise


@dataclass(frozen=True)
class CheckSpec:
    name: str
    phase: str
    argv: tuple[str, ...]
    stdin: bytes = b""
    expected_stdout: bytes | None = None
    expected_exit: int = 0
    timeout: float = 5.0
    output_limit: int = 1048576

    def __post_init__(self):
        if (not isinstance(self.name, str) or not self.name or self.phase not in {"build", "shadow", "canary"}
                or not isinstance(self.argv, tuple) or not self.argv or any(not isinstance(x, str) or not x for x in self.argv)
                or not isinstance(self.stdin, bytes) or len(self.stdin) > 65536
                or self.expected_stdout is not None and not isinstance(self.expected_stdout, bytes)
                or type(self.expected_exit) is not int or not 0 <= self.expected_exit <= 255
                or type(self.timeout) not in {int, float} or not 0 < self.timeout <= 300
                or type(self.output_limit) is not int or not 1 <= self.output_limit <= 1048576):
            raise CoreError("invalid_check", "A governing check must have bounded explicit expectations")

    def as_dict(self):
        return {**asdict(self), "stdin": base64.b64encode(self.stdin).decode(),
                "expected_stdout": None if self.expected_stdout is None else base64.b64encode(self.expected_stdout).decode()}


class MetaHarness:
    def __init__(self, journal, root, dispatcher, *, checks, allowed_programs=(sys.executable,)):
        self.journal, self.dispatcher = journal, dispatcher
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._checks = tuple(checks)
        if (not self.checks or len(self.checks) > 32 or any(not isinstance(check, CheckSpec) for check in self.checks)
                or len({check.name for check in self.checks}) != len(self.checks)):
            raise CoreError("invalid_check", "Nonempty uniquely named governing checks are required")
        self.allowed_programs = tuple(str(Path(program).resolve(strict=True)) for program in allowed_programs)
        if any(str(Path(check.argv[0]).resolve(strict=True)) not in self.allowed_programs for check in self.checks):
            raise CoreError("program_denied", "A governing check uses an unapproved executable")
        self.policy_hash = hashlib.sha256(canonical_json({"checks": [check.as_dict() for check in self.checks],
                                       "programs": self.allowed_programs}).encode()).hexdigest()
        self.tool_name = "harness.evaluate." + self.policy_hash[:16]
        dispatcher.register(self.tool_name, self._execute, input_schema={"type": "object", "required": ["evaluation_id"],
            "properties": {"evaluation_id": {"type": "string", "minLength": 1}}, "additionalProperties": False},
            output_schema={"type": "object", "required": ["evaluation_id", "passed"]},
            required_capabilities=("evolution.evaluate",), timeout=min(3600, sum(check.timeout for check in self.checks) + 5),
            output_limit=1048576)

    @property
    def checks(self):
        return self._checks

    def diagnose(self, scope, evidence_event_ids, *, hypothesis, expected_observation, falsifier):
        if (not evidence_event_ids or len(evidence_event_ids) > 128 or len(set(evidence_event_ids)) != len(evidence_event_ids)
                or any(not isinstance(text, str) or not text.strip() or len(text) > 8192
                       for text in (hypothesis, expected_observation, falsifier))):
            raise CoreError("invalid_diagnosis", "Trace evidence and a bounded falsifiable hypothesis are required")
        events = {event.id: event for event in self.journal.events(scope, limit=100000)}
        if not set(evidence_event_ids) <= events.keys():
            raise CoreError("scope_denied", "Diagnosis evidence is outside this scope")
        evidence = [events[key] for key in evidence_event_ids]
        classification = ("tool_failure" if any(event.kind == "tool.result" and event.payload.get("outcome") not in {None, "success"} for event in evidence)
                          else "expectation_violation" if any(event.payload.get("assessment") == "violated" for event in evidence)
                          else "unclassified")
        value = {"evidence_event_ids": list(evidence_event_ids), "classification": classification,
                 "hypothesis": hypothesis, "expected_observation": expected_observation, "falsifier": falsifier,
                 "status": "hypothesis", "policy_hash": self.policy_hash}
        with self.journal.transaction():
            row = self.journal.put("harness_diagnoses", new_id("diagnosis"), scope, value)
            self.journal.append(scope, "harness.diagnosed", {"diagnosis_id": row.id, **value}, source="meta_harness")
            return row

    def prepare(self, scope, snapshot, phase):
        if not isinstance(snapshot, SourceSnapshot) or not any(check.phase == phase for check in self.checks):
            raise CoreError("invalid_evaluation", "A pinned snapshot and configured phase are required")
        candidate = Path(snapshot.path).resolve(strict=True)
        if candidate == self.root or candidate in self.root.parents or self.root in candidate.parents:
            raise CoreError("invalid_source", "Candidate and evaluator work roots must be separate")
        if source_snapshot(candidate).sha256 != snapshot.sha256:
            raise CoreError("source_changed", "Candidate differs from its pinned snapshot")
        value = {"snapshot": snapshot.as_dict(), "phase": phase, "policy_hash": self.policy_hash,
                 "status": "queued", "results": [], "tool_result": None}
        return self.journal.put("harness_evaluations", new_id("evaluation"), scope, value)

    def get_evaluation(self, scope, evaluation_id):
        row = self.journal.get("harness_evaluations", evaluation_id, scope)
        if row is None:
            raise CoreError("not_found", "Evaluation does not exist in this scope")
        return row

    @staticmethod
    def verified(row):
        """A check verdict needs its exact terminal canonical execution receipt."""
        receipt = row.value.get("tool_result") or {}
        return (row.value["status"] == "passed" and receipt.get("state") == "completed"
                and receipt.get("outcome") == "success" and receipt.get("attempt_id") == row.value.get("attempt_id")
                and receipt.get("data") == {"evaluation_id": row.id, "passed": True})

    def _update(self, scope, evaluation_id, changes):
        with self.journal.transaction():
            row = self.get_evaluation(scope, evaluation_id)
            if all(row.value.get(key) == value for key, value in changes.items()):
                return row
            updated = self.journal.put("harness_evaluations", evaluation_id, scope, {**row.value, **changes}, expected_revision=row.revision)
            self.journal.append(scope, "harness.evaluation_changed", {"evaluation_id": evaluation_id,
                                "status": updated.value["status"], "revision": updated.revision}, source="meta_harness")
            return updated

    async def _execute(self, arguments, context):
        row = await asyncio.to_thread(self.get_evaluation, context.scope, arguments["evaluation_id"])
        if row.value["status"] != "queued" or row.value["policy_hash"] != self.policy_hash:
            raise CoreError("invalid_evaluation", "Evaluation state or governing checks changed", effects="none")
        snapshot = SourceSnapshot.from_dict(row.value["snapshot"])
        await asyncio.to_thread(self._update, context.scope, row.id, {"status": "running", "action_id": context.action_id,
                                                                   "attempt_id": context.attempt_id, "work_id": context.work_id})
        results = []
        root = self.root / hashlib.sha256(context.scope.encode()).hexdigest() / row.id
        try:
            for index, check in enumerate(check for check in self.checks if check.phase == row.value["phase"]):
                workspace = root / str(index)
                copying = asyncio.create_task(asyncio.to_thread(copy_snapshot, snapshot, workspace))
                try:
                    await asyncio.shield(copying)
                except asyncio.CancelledError:
                    # A filesystem worker keeps running after its asyncio waiter is cancelled.
                    # Join it before cleanup so it cannot recreate candidate files afterwards.
                    await copying
                    raise
                runner = ProcessRunner(workspace, self.allowed_programs)
                try:
                    result = await runner.run(list(check.argv), stdin=check.stdin, timeout=check.timeout,
                                              output_limit=check.output_limit, cancel=context.cancel)
                    passed = result["exit_code"] == check.expected_exit and (check.expected_stdout is None or result["stdout"] == check.expected_stdout)
                    observed = {"name": check.name, "passed": passed, "exit_code": result["exit_code"],
                                "stdout": base64.b64encode(result["stdout"]).decode(), "stderr": base64.b64encode(result["stderr"]).decode()}
                except CoreError as exc:
                    observed = {"name": check.name, "passed": False, "error": exc.as_dict()}
                results.append(observed)
            unchanged = await asyncio.to_thread(source_snapshot, snapshot.path)
            if unchanged.sha256 != snapshot.sha256:
                raise CoreError("source_changed", "Candidate changed during evaluation")
            passed = all(result["passed"] for result in results)
            await asyncio.to_thread(self._update, context.scope, row.id, {"status": "passed" if passed else "failed", "results": results})
            return ExecutorResult({"evaluation_id": row.id, "passed": passed}, outcome="success" if passed else "target_error", effects="observed")
        finally:
            if root.exists():
                await asyncio.to_thread(shutil.rmtree, root)

    async def run(self, scope, evaluation_id, *, work_id, generation, cancel=None):
        result = await self.dispatcher.invoke(ToolCall(self.tool_name, {"evaluation_id": evaluation_id}),
                        scope=scope, work_id=work_id, generation=generation, request_id="eval:" + evaluation_id, cancel=cancel)
        row = await asyncio.to_thread(self.get_evaluation, scope, evaluation_id)
        if row.value["status"] in {"queued", "running"}:
            status = "interrupted" if result.state in {"cancelled", "indeterminate"} else "rejected" if result.state == "rejected" else "failed"
            return await asyncio.to_thread(self._update, scope, evaluation_id, {"status": status, "tool_result": result.as_dict()})
        if row.value["status"] == "passed" and (result.state != "completed" or result.outcome != "success"):
            return await asyncio.to_thread(self._update, scope, evaluation_id, {"status": "interrupted", "tool_result": result.as_dict()})
        return await asyncio.to_thread(self._update, scope, evaluation_id, {"tool_result": result.as_dict()})

    async def evaluate(self, scope, snapshot, *, phase, work_id, generation, cancel=None):
        row = await asyncio.to_thread(self.prepare, scope, snapshot, phase)
        return await self.run(scope, row.id, work_id=work_id, generation=generation, cancel=cancel)

    def recover(self, scope):
        return [self._update(scope, row.id, {"status": "interrupted", "error": "Evaluation was interrupted before a recorded verdict"})
                for row in self.journal.list("harness_evaluations", scope)
                if row.value["status"] in {"queued", "running"} or row.value["status"] == "passed" and not self.verified(row)]
