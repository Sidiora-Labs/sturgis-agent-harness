"""Parent-scoped children, isolated workspaces and evidence-bearing result admission."""

import asyncio
import hashlib
import math
from pathlib import Path
import time

from agent.awareness import required, text, timestamp
from agent.awareness.evidence import resolve_evidence, validate_checks, verify_checks
from agent.protocol import CoreError, ToolCall, canonical_json, new_id
from agent.scheduler import TERMINAL


class Subagents:
    def __init__(self, journal, scheduler, *, workspace_root, memory=None, max_depth=4):
        if scheduler.journal is not journal or type(max_depth) is not int or not 1 <= max_depth <= 32:
            raise CoreError("invalid_dependency", "Children require shared transactions and bounded depth")
        root = Path(workspace_root).resolve(strict=True)
        if not root.is_dir():
            raise CoreError("invalid_workspace", "Child workspace root must be a directory")
        self.journal, self.scheduler, self.workspace_root = journal, scheduler, root
        self.memory, self.max_depth = memory, max_depth

    def get(self, scope, child_id):
        return required(self.journal, "subagents", child_id, scope)

    def _save(self, scope, row, body):
        updated = self.journal.put("subagents", row.id, scope, body, row.revision)
        self.journal.append(scope, "child.changed", {"child_id": row.id, **body}, source="subagents")
        return updated

    def delegate(self, scope, parent_id, objective, *, capabilities, resources, criteria=None, workspace_mode="isolated", retained=False, expires_at=None, memory_scout=False, now=None):
        text(objective, "objective", 16384)
        if workspace_mode not in ("isolated", "shared") or type(retained) is not bool or type(memory_scout) is not bool:
            raise CoreError("invalid_child", "Workspace mode and retention/scout flags must be explicit")
        checks = validate_checks([] if criteria is None else criteria)
        now = self.scheduler._now(now)
        if expires_at is not None and timestamp(expires_at) <= now:
            raise CoreError("invalid_child", "Child expiry must be in the future")
        with self.journal.transaction():
            parent = self.scheduler.get(scope, parent_id)
            lineage = self.scheduler._lineage(scope, parent, now, executing=False)
            if len(lineage) > self.max_depth:
                raise CoreError("depth_exceeded", "Delegation exceeds configured ancestry depth")
            if memory_scout and (set(capabilities) != {"memory.read"} or not resources.get("tool_calls")):
                raise CoreError("scope_denied", "Memory scouts require only memory.read and a query allowance")
            base = Path(parent.value["constraints"].get("workspace_root", str(self.workspace_root)))
            if not base.is_relative_to(self.workspace_root):
                raise CoreError("scope_denied", "Parent workspace is outside the child service root")
            child_id = new_id("child")
            root = base if workspace_mode == "shared" else base / ".sturgis" / "children" / hashlib.sha256(scope.encode()).hexdigest()[:16] / child_id
            # Resolve existing prefixes before mkdir so a replaced namespace cannot redirect creation.
            if not root.resolve().is_relative_to(base):
                raise CoreError("scope_denied", "Child workspace namespace was redirected")
            root.mkdir(parents=True, exist_ok=True)
            root = root.resolve(strict=True)
            writable = not memory_scout and parent.value["constraints"].get("workspace_writable", True)
            work = self.scheduler.submit(scope, "child", {"child_id": child_id, "objective": objective},
                parent_id=parent_id, capabilities=capabilities, resources=resources, now=now,
                retain_after_parent=retained, constraints={"workspace_root": str(root), "workspace_writable": writable,
                    "delegated": True, "memory_scout": memory_scout})
            event = self.journal.append(scope, "child.delegated", {"child_id": child_id, "work_id": work.id, "parent_id": parent_id}, source="subagents")
            return self.journal.put("subagents", child_id, scope, {"parent_id": parent_id, "work_id": work.id,
                "objective": objective, "criteria": checks, "workspace_mode": workspace_mode, "workspace_root": str(root),
                "retained": retained, "memory_scout": memory_scout, "state": "active", "created_at": now,
                "created_seq": event.seq, "last_heartbeat": now, "expires_at": expires_at,
                "archived": False, "result": None, "wait_id": None, "wait_for": None})

    def _active(self, scope, child_id, now=None):
        child = self.get(scope, child_id)
        body = child.value
        now = self.scheduler._now(now)
        if body["state"] != "active" or body["archived"] or body["expires_at"] is not None and body["expires_at"] <= now:
            raise CoreError("child_inactive", "Child is terminal, archived or expired")
        self.scheduler._lineage(scope, self.scheduler.get(scope, body["work_id"]), now, executing=False)
        return child

    def heartbeat(self, scope, child_id, *, now=None):
        now = self.scheduler._now(now)
        with self.journal.transaction():
            row = self._active(scope, child_id, now)
            work = self.scheduler.get(scope, row.value["work_id"])
            if work.value["state"] == "running":
                self.scheduler.heartbeat(scope, work.id, work.value["generation"], now=now)
            return self._save(scope, row, {**row.value, "last_heartbeat": now})

    def message(self, scope, child_id, sender_work_id, content):
        text(content, "message", 65536)
        with self.journal.transaction():
            row = self.get(scope, child_id)
            if row.value["state"] != "active" or row.value["archived"] or sender_work_id not in {row.value["parent_id"], row.value["work_id"]}:
                raise CoreError("scope_denied", "Only this active child's parent and child may send messages")
            self.scheduler._lineage(scope, self.scheduler.get(scope, sender_work_id), self.scheduler._now(), executing=False)
            event = self.journal.append(scope, "child.message", {"child_id": child_id, "sender_work_id": sender_work_id, "content": content}, source="subagents")
            return self.journal.put("child_messages", event.id, scope, {**event.payload, "seq": event.seq})

    def messages(self, scope, child_id, *, after=0):
        self.get(scope, child_id)
        if type(after) is not int or after < 0:
            raise CoreError("invalid_cursor", "Message cursor must be nonnegative")
        return sorted((row for row in self.journal.list("child_messages", scope) if row.value["child_id"] == child_id and row.value["seq"] > after), key=lambda row: row.value["seq"])

    def wait_for(self, scope, child_id, target_child_id, *, after_seq=0, expires_at=None):
        with self.journal.transaction():
            child = self._active(scope, child_id)
            target = self.get(scope, target_child_id)
            if child.value["parent_id"] != target.value["parent_id"]:
                raise CoreError("scope_denied", "Child waits are confined to siblings under the same parent")
            seen, cursor = {child_id}, target
            while cursor:
                if cursor.id in seen:
                    raise CoreError("dependency_cycle", "Child wait would introduce a dependency cycle")
                seen.add(cursor.id)
                cursor = self.get(scope, cursor.value["wait_for"]) if cursor.value["wait_for"] else None
            if child.value["wait_id"]:
                raise CoreError("invalid_wait", "Child already has a wait subscription")
            wait = self.scheduler.register_wait(scope, child.value["work_id"], {"kind": "child_terminal", "child_id": target_child_id},
                wait_id=new_id("wait"), after_seq=after_seq, expires_at=expires_at)
            work = self.scheduler.get(scope, child.value["work_id"])
            self.scheduler.park(scope, work.id, work.value["generation"], wait_id=wait.id)
            return self._save(scope, child, {**child.value, "wait_id": wait.id, "wait_for": target_child_id})

    def resume(self, scope, child_id):
        with self.journal.transaction():
            child = self.get(scope, child_id)
            wait = required(self.journal, "waits", child.value["wait_id"], scope)
            if wait.value["state"] != "fired":
                raise CoreError("invalid_wait", "Child wait has not fired")
            return self._save(scope, child, {**child.value, "wait_id": None, "wait_for": None})

    def submit_result(self, scope, child_id, *, evidence_event_ids, summary, submission_id, now=None):
        text(summary, "summary", 65536)
        text(submission_id, "submission_id", 256)
        fingerprint = canonical_json({"child_id": child_id, "evidence": evidence_event_ids, "summary": summary})
        with self.journal.transaction():
            prior = self.journal.get("child_submissions", submission_id, scope)
            if prior:
                if prior.value["fingerprint"] != fingerprint:
                    raise CoreError("dedupe_conflict", "Result submission identity was reused")
                return self.get(scope, child_id)
            row = self._active(scope, child_id, now)
            matches, failures = {}, {}
            try:
                events = resolve_evidence(self.journal, scope, evidence_event_ids)
                fresh = [event.id for event in events if event.seq > row.value["created_seq"]]
                for check in row.value["criteria"]:
                    try:
                        matches.update(verify_checks(self.journal, scope, [check], fresh))
                    except CoreError as exc:
                        failures[check["id"]] = exc.as_dict()
            except CoreError as exc:
                failures["evidence"] = exc.as_dict()
            verdict = "accepted" if matches and len(matches) == len(row.value["criteria"]) else "partial" if matches else "rejected"
            result = {"verdict": verdict, "summary": summary, "evidence_event_ids": evidence_event_ids,
                "matched_checks": matches, "failures": failures, "submission_id": submission_id}
            row = self._save(scope, row, {**row.value, "result": result, "state": "completed" if verdict == "accepted" else "active"})
            self.journal.put("child_submissions", submission_id, scope, {"fingerprint": fingerprint, "result": result})
            self.journal.append(scope, "child.result", {"child_id": child_id, **result}, source="subagents")
            if verdict == "accepted":
                work = self.scheduler.get(scope, row.value["work_id"])
                self.scheduler.finish(scope, work.id, work.value["generation"], now=now)
                self.journal.append(scope, "child.terminal", {"child_id": child_id, "work_id": work.id, "state": "completed", "result": result}, source="subagents")
            return row

    def cancel(self, scope, child_id, *, reason="cancelled", now=None):
        with self.journal.transaction():
            row = self.get(scope, child_id)
            self.scheduler.cancel(scope, row.value["work_id"], reason=reason, now=now)
            if row.value["wait_id"]:
                self.scheduler.cancel_wait(scope, row.value["wait_id"])
            if row.value["state"] != "active":
                return row
            row = self._save(scope, row, {**row.value, "state": "cancelled", "reason": reason})
            self.journal.append(scope, "child.terminal", {"child_id": child_id, "work_id": row.value["work_id"], "state": "cancelled", "reason": reason}, source="subagents")
            return row

    def recover(self, scope, *, now=None):
        now, result = self.scheduler._now(now), []
        with self.journal.transaction():
            self.scheduler.recover(scope, now=now)
            for row in self.journal.list("subagents", scope):
                if row.value["state"] != "active":
                    continue
                work = self.scheduler.get(scope, row.value["work_id"])
                parent = self.journal.get("work", row.value["parent_id"], scope)
                reason = "expired" if row.value["expires_at"] is not None and row.value["expires_at"] <= now else "orphaned" if parent is None else "authority_lost" if parent.value["revoked"] or work.value["state"] in TERMINAL else None
                if reason:
                    result.append(self.cancel(scope, row.id, reason=reason, now=now))
            return result

    def archive(self, scope, child_id):
        with self.journal.transaction():
            row = self.get(scope, child_id)
            if row.value["state"] == "active":
                raise CoreError("invalid_transition", "Active child cannot be archived")
            return row if row.value["archived"] else self._save(scope, row, {**row.value, "archived": True})

    def _claim_scout(self, scope, child_id, query):
        if self.memory is None:
            raise CoreError("memory_unavailable", "No native memory client is configured")
        if set(query) - {"text", "concepts", "embedding", "at", "limit", "lease_id"}:
            raise CoreError("scope_denied", "Scout query cannot select an operation or another scope")
        row = self._active(scope, child_id)
        if not row.value["memory_scout"]:
            raise CoreError("scope_denied", "Child is not a restricted memory scout")
        work = self.scheduler.get(scope, row.value["work_id"])
        claim_id = new_id("scout")
        grant = {"work_id": work.id, "generation": work.value["generation"], "claim_id": claim_id,
            "required_capabilities": ["memory.read"], "resources": {"tool_calls": 1, "elapsed_ms": math.ceil(self.memory.timeout * 1000)}}
        self.scheduler.authorize(scope, grant, ToolCall("memory.query", query))
        return row, work, claim_id

    async def scout(self, scope, child_id, **query):
        row, work, claim_id = await asyncio.to_thread(self._claim_scout, scope, child_id, query)
        started = time.monotonic()
        try:
            result = await self.memory.query(scope, **query)
            await asyncio.to_thread(self.journal.append, scope, "child.scout_result", {"child_id": child_id, "work_id": work.id, "response": result}, "subagents")
            return result
        finally:
            # Read-only native queries cannot cause an unknown mutation. Account actual transport occupancy.
            await asyncio.to_thread(self.scheduler.record_usage, scope, work.id, claim_id, {"tool_calls": 1, "elapsed_ms": math.ceil((time.monotonic() - started) * 1000)}, claim_id=claim_id)
