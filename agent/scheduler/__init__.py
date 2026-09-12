"""One durable work/grant authority, cumulative allocations, timers and wake subscriptions."""

from datetime import datetime, timedelta
import math
from pathlib import Path
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agent.awareness import required, text, timestamp
from agent.commitments import validate_trigger
from agent.goals import GoalService, amounts
from agent.protocol import CoreError, ToolCall, canonical_json, new_id, validate_scope
from agent.state import Journal


TERMINAL = {"completed", "failed", "cancelled"}
CONTROL = "__scheduler_control__"


def _constraints(value):
    if not isinstance(value, dict) or set(value) - {"workspace_root", "workspace_writable", "delegated", "memory_scout"}:
        raise CoreError("invalid_constraints", "Unknown execution constraint")
    for key in {"workspace_writable", "delegated", "memory_scout"} & set(value):
        if type(value[key]) is not bool:
            raise CoreError("invalid_constraints", "Constraint flags must be boolean")
    if "workspace_root" in value:
        try:
            root = Path(value["workspace_root"])
            if not root.is_absolute() or str(root.resolve(strict=True)) != str(root) or not root.is_dir():
                raise CoreError("scope_denied", "Execution workspace must remain its canonical recorded directory")
        except (OSError, TypeError, ValueError) as exc:
            raise CoreError("scope_denied", "Execution workspace is unavailable") from exc
    if value.get("delegated") and "workspace_root" not in value:
        raise CoreError("invalid_constraints", "Delegated work requires a workspace boundary")
    return value


def _capabilities(values):
    if not isinstance(values, (tuple, list, set)):
        raise CoreError("invalid_grant", "Capabilities must be explicit identifiers")
    for value in values:
        text(value, "capability", 256)
    return sorted(set(values))


class Scheduler:
    def __init__(self, journal: Journal, *, goals=None, max_running=4, lease_seconds=30, clock=None):
        if type(max_running) is not int or max_running < 1 or type(lease_seconds) not in (int, float) or not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise CoreError("invalid_limit", "Scheduler capacity and lease duration must be positive")
        self.journal, self.goals = journal, goals or GoalService(journal)
        if self.goals.journal is not journal:
            raise CoreError("invalid_dependency", "Goal accounting must share the operational transaction")
        self.max_running, self.lease_seconds, self.clock = max_running, lease_seconds, clock or time.time

    def _now(self, value=None):
        return timestamp(self.clock() if value is None else value)

    def get(self, scope, work_id):
        return required(self.journal, "work", work_id, scope)

    def _save(self, scope, row, body):
        updated = self.journal.put("work", row.id, scope, body, row.revision)
        self.journal.append(scope, "work.changed", {"work_id": row.id, "revision": updated.revision, **body}, source="scheduler")
        return updated

    def _all(self):
        return [self.get(row.value["scope"], row.id) for row in self.journal.list("work_index", CONTROL)]

    def _lineage(self, scope, row, now, *, executing=True, check_goal=True):
        result, seen, child = [], set(), None
        while row is not None:
            if row.id in seen:
                raise CoreError("dependency_cycle", "Work ancestry contains a cycle")
            seen.add(row.id)
            body = row.value
            if body["revoked"] or body["budget_overrun"]:
                raise CoreError("authority_revoked", "Work ancestry no longer grants execution")
            allowed = {"running"} if child is None and executing else {"running", "waiting"}
            if child is not None and child.value["retain_after_parent"]:
                allowed.add("completed")
            if body["state"] not in allowed or (body["state"] == "running" and body["lease_until"] <= now):
                raise CoreError("stale_authority", "Work or ancestor is not currently executable")
            if child is not None and child.value["parent_generation"] != body["generation"]:
                raise CoreError("stale_authority", "Parent authority generation changed")
            _constraints(body["constraints"])
            result.append(row)
            child, row = row, self.get(scope, body["parent_id"]) if body["parent_id"] else None
        if check_goal and result[-1].value["goal_id"]:
            goal = self.goals.get(scope, result[-1].value["goal_id"])
            if goal.value["state"] != "active" or goal.value["archived"] or goal.value["budget_overrun"]:
                raise CoreError("goal_inactive", "Current goal state does not permit fresh execution")
        return result

    def submit(self, scope, kind, payload, *, capabilities=(), resources=None, goal_id=None, parent_id=None, dedupe_key=None, priority=0, not_before=None, now=None, constraints=None, retain_after_parent=False):
        validate_scope(scope)
        text(kind, "kind", 128)
        if scope == CONTROL or not isinstance(payload, dict) or type(priority) is not int or not -100 <= priority <= 100 or type(retain_after_parent) is not bool:
            raise CoreError("invalid_work", "Work requires an external scope, object payload and bounded priority")
        capabilities, resources = _capabilities(capabilities), amounts({} if resources is None else resources)
        constraints = _constraints({} if constraints is None else constraints)
        now = self._now(now)
        explicit_not_before = not_before
        not_before = now if not_before is None else timestamp(not_before)
        request = {"kind": kind, "payload": payload, "capabilities": capabilities, "resources": resources, "goal_id": goal_id, "parent_id": parent_id, "priority": priority, "constraints": constraints, "retain_after_parent": retain_after_parent, "requested_not_before": explicit_not_before}
        fingerprint = canonical_json(request)
        with self.journal.transaction():
            if dedupe_key is not None:
                text(dedupe_key, "dedupe_key", 512)
                prior = self.journal.get("work_keys", dedupe_key, scope)
                if prior:
                    if prior.value["fingerprint"] != fingerprint:
                        raise CoreError("dedupe_conflict", "Work submission key was reused")
                    return self.get(scope, prior.value["work_id"])
            parent_generation = None
            if parent_id:
                parent = self.get(scope, parent_id)
                if parent.value["state"] == "completed" and retain_after_parent:
                    if parent.value["revoked"] or parent.value["budget_overrun"] or parent.value["allocation_closed"]:
                        raise CoreError("authority_revoked", "Parent no longer grants retained work")
                else:
                    self._lineage(scope, parent, now, executing=False)
                if not set(capabilities) <= set(parent.value["capabilities"]):
                    raise CoreError("scope_denied", "Child cannot expand parent capabilities")
                if goal_id is not None and goal_id != parent.value["goal_id"]:
                    raise CoreError("scope_denied", "Child cannot select another cumulative goal budget")
                parent_root = parent.value["constraints"].get("workspace_root")
                if parent_root and (not constraints.get("workspace_root") or not Path(constraints["workspace_root"]).is_relative_to(parent_root)):
                    raise CoreError("scope_denied", "Child workspace escapes its parent")
                if parent.value["constraints"].get("workspace_writable") is False and constraints.get("workspace_writable", True):
                    raise CoreError("scope_denied", "Child cannot regain write permission")
                goal_id, parent_generation = parent.value["goal_id"], parent.value["generation"]
            if goal_id:
                self.goals.get(scope, goal_id)
            body = {**request, "goal_id": goal_id, "parent_generation": parent_generation, "state": "queued", "generation": 1, "revoked": False, "budget_overrun": False, "usage": {}, "claim_reserved": {}, "child_reserved": {}, "allocation_reserved": False, "allocation_closed": False, "goal_reserved": False, "lease_until": None, "worker": None, "created_at": now, "not_before": not_before, "cancel_reason": None}
            work_id = new_id("work")
            event = self.journal.append(scope, "work.submitted", {"work_id": work_id, **body}, source="scheduler")
            body["queue_seq"] = event.seq
            row = self.journal.put("work", work_id, scope, body)
            self.journal.put("work_index", work_id, CONTROL, {"scope": scope})
            if dedupe_key is not None:
                self.journal.put("work_keys", dedupe_key, scope, {"work_id": work_id, "fingerprint": fingerprint})
            return row

    def admit(self, scope, work_id=None, *, worker="runtime", now=None, eligible_ids=None):
        now = self._now(now)
        text(worker, "worker", 256)
        if eligible_ids is not None:
            if not isinstance(eligible_ids, (list, tuple, set)) or len(eligible_ids) > 4096:
                raise CoreError("invalid_work", "Ready work identities must be a bounded collection")
            for key in eligible_ids:
                text(key, "work_id", 256)
            eligible_ids = set(eligible_ids)
        with self.journal.transaction():
            profile = self.journal.get("profiles", scope, scope)
            if profile and not profile.value["enabled"]:
                raise CoreError("profile_disabled", "Profile is disabled")
            all_work = self._all()
            running = [row for row in all_work if row.value["state"] == "running"]
            if len(running) >= self.max_running:
                return None
            candidates = [row for row in all_work if row.scope == scope and row.value["state"] == "queued" and row.value["not_before"] <= now and not row.value["revoked"] and (eligible_ids is None or row.id in eligible_ids)]
            candidates.sort(key=lambda row: (-(row.value["priority"] + (now - row.value["created_at"]) / self.lease_seconds), row.value["queue_seq"]))
            if not candidates or (work_id is not None and candidates[0].id != work_id):
                return None
            row = candidates[0]
            body = dict(row.value)
            provisional = type(row)(row.namespace, row.id, row.scope, row.revision, {**body, "state": "running", "lease_until": now + self.lease_seconds})
            self._lineage(scope, provisional, now)
            if body["parent_id"]:
                parent = self.get(scope, body["parent_id"])
                # Validate the queued child against the captured parent generation.
                if not body["allocation_reserved"]:
                    held = dict(parent.value["child_reserved"])
                    for key, value in body["resources"].items():
                        held[key] = held.get(key, 0) + value
                        if held[key] + parent.value["usage"].get(key, 0) + parent.value["claim_reserved"].get(key, 0) > parent.value["resources"].get(key, 0):
                            raise CoreError("budget_exceeded", "Child allocation exceeds its parent", dimension=key)
                    self._save(scope, parent, {**parent.value, "child_reserved": held})
                    body["allocation_reserved"] = True
            elif body["goal_id"] and not body["goal_reserved"]:
                self.goals.reserve(scope, body["goal_id"], row.id, body["resources"])
                body["goal_reserved"] = True
            body.update(state="running", worker=worker, lease_until=now + self.lease_seconds)
            return self._save(scope, row, body)

    def heartbeat(self, scope, work_id, generation, *, now=None):
        now = self._now(now)
        with self.journal.transaction():
            row = self.get(scope, work_id)
            self._lineage(scope, row, now)
            if type(generation) is not int or row.value["generation"] != generation:
                raise CoreError("stale_authority", "Work generation changed")
            return self._save(scope, row, {**row.value, "lease_until": now + self.lease_seconds})

    def authorize(self, scope, grant, call: ToolCall):
        if not isinstance(grant, dict) or not {"work_id", "generation", "claim_id", "resources", "required_capabilities"} <= set(grant) or not isinstance(call, ToolCall):
            raise CoreError("invalid_grant", "A runtime work grant and canonical call are required")
        resources = amounts(grant["resources"])
        capabilities = _capabilities(grant["required_capabilities"])
        text(grant["claim_id"], "claim_id", 256)
        fingerprint = canonical_json({"grant": grant, "call": call.as_dict()})
        with self.journal.transaction():
            row = self.get(scope, grant["work_id"])
            self._lineage(scope, row, self._now())
            if type(grant["generation"]) is not int or row.value["generation"] != grant["generation"] or not set(capabilities) <= set(row.value["capabilities"]):
                raise CoreError("scope_denied", "Grant generation or tool capabilities do not match admitted work")
            constraints = row.value["constraints"]
            if constraints.get("memory_scout") and (call.tool != "memory.query" or set(capabilities) - {"memory.read"}):
                raise CoreError("scope_denied", "Memory scout may only query scoped memory")
            previous = self.journal.get("work_claims", grant["claim_id"], scope)
            if previous:
                if previous.value["fingerprint"] != fingerprint:
                    raise CoreError("dedupe_conflict", "Claim identity was reused")
                return previous.value["authorization"]
            held = dict(row.value["claim_reserved"])
            for key, value in resources.items():
                held[key] = held.get(key, 0) + value
                if held[key] + row.value["usage"].get(key, 0) + row.value["child_reserved"].get(key, 0) > row.value["resources"].get(key, 0):
                    raise CoreError("budget_exceeded", "Tool claim exceeds its work allocation", dimension=key)
            authorization = {"work_id": row.id, "generation": row.value["generation"], "claim_id": grant["claim_id"], "resources": resources, "constraints": constraints}
            self.journal.put("work_claims", grant["claim_id"], scope, {"work_id": row.id, "resources": resources, "state": "claimed", "fingerprint": fingerprint, "authorization": authorization})
            self._save(scope, row, {**row.value, "claim_reserved": held})
            return authorization

    def _ancestry_records(self, scope, row):
        result, seen = [], set()
        while row:
            if row.id in seen:
                raise CoreError("dependency_cycle", "Work ancestry contains a cycle")
            seen.add(row.id)
            result.append(row)
            row = self.get(scope, row.value["parent_id"]) if row.value["parent_id"] else None
        return result

    def record_usage(self, scope, work_id, usage_id, resources, *, claim_id=None):
        amounts(resources)
        text(usage_id, "usage_id", 512)
        body = {"work_id": work_id, "resources": resources, "claim_id": claim_id}
        with self.journal.transaction():
            row = self.get(scope, work_id)
            prior = self.journal.get("work_usage", usage_id, scope)
            if prior:
                if prior.value != body:
                    raise CoreError("dedupe_conflict", "Usage receipt identity was reused")
                return row
            claim_overrun = False
            if claim_id is not None:
                claim = required(self.journal, "work_claims", claim_id, scope)
                if claim.value["work_id"] != work_id or claim.value["state"] != "claimed":
                    raise CoreError("invalid_claim", "Usage requires an unsettled claim belonging to this work")
                held = dict(row.value["claim_reserved"])
                for key, value in claim.value["resources"].items():
                    held[key] -= value
                claim_overrun = any(value > claim.value["resources"].get(key, 0) for key, value in resources.items())
                row = self._save(scope, row, {**row.value, "claim_reserved": held})
                self.journal.put("work_claims", claim_id, scope, {**claim.value, "state": "settled", "usage_id": usage_id, "actual": resources}, claim.revision)
            lineage, previous_child = self._ancestry_records(scope, row), None
            for item in lineage:
                updated = dict(item.value)
                usage, held = dict(updated["usage"]), dict(updated["child_reserved"])
                for key, value in resources.items():
                    usage[key] = usage.get(key, 0) + value
                    if previous_child and previous_child.value["allocation_reserved"]:
                        old = previous_child.value["usage"].get(key, 0)
                        allocation = previous_child.value["resources"].get(key, 0)
                        held[key] = held.get(key, 0) - (min(old + value, allocation) - min(old, allocation))
                overrun = updated["budget_overrun"] or (item.id == work_id and claim_overrun) or any(usage.get(key, 0) + held.get(key, 0) + updated["claim_reserved"].get(key, 0) > updated["resources"].get(key, 0) for key in set(usage) | set(held))
                updated.update(usage=usage, child_reserved=held, budget_overrun=overrun)
                if overrun:
                    updated["generation"] += 1
                    if updated["state"] not in TERMINAL:
                        updated["state"] = "failed"
                self._save(scope, item, updated)
                previous_child = item
            root = lineage[-1]
            if root.value["goal_id"]:
                self.goals.charge(scope, root.value["goal_id"], root.id, f"scheduler:{usage_id}", resources)
            self.journal.put("work_usage", usage_id, scope, body)
            self.journal.append(scope, "work.usage", {"usage_id": usage_id, **body}, source="scheduler")
            for item in lineage:
                self._release_finished(scope, self.get(scope, item.id))
            return self.get(scope, work_id)

    def reconcile_usage(self, scope):
        result = []
        with self.journal.transaction():
            cursor = self.journal.cursor(scope, "scheduler.tool_usage")
            for event in self.journal.events(scope, after=cursor):
                if event.kind == "tool.usage" and event.source == "tool_runner":
                    payload = event.payload
                    result.append(self.record_usage(scope, payload["work_id"], event.id, payload["resources"], claim_id=payload["claim_id"]))
                self.journal.advance_cursor(scope, "scheduler.tool_usage", event.seq)
        return result

    def _release_finished(self, scope, row):
        if row.value["state"] not in TERMINAL:
            return
        if any(claim.value["work_id"] == row.id and claim.value["state"] == "claimed" for claim in self.journal.list("work_claims", scope)) or any(child.value["parent_id"] == row.id and (child.value["allocation_reserved"] or child.value["state"] not in TERMINAL) for child in self.journal.list("work", scope)):
            return
        body = dict(row.value)
        body["allocation_closed"] = True
        parent = None
        if body["allocation_reserved"]:
            parent = self.journal.get("work", body["parent_id"], scope)
            if parent is None:
                return  # Preserve an orphaned allocation for explicit operational repair.
            held = dict(parent.value["child_reserved"])
            for key, value in body["resources"].items():
                held[key] -= max(0, value - body["usage"].get(key, 0))
            parent = self._save(scope, parent, {**parent.value, "child_reserved": held})
            body["allocation_reserved"] = False
        if body["goal_reserved"]:
            self.goals.release(scope, body["goal_id"], row.id)
            body["goal_reserved"] = False
        if body != row.value:
            self._save(scope, row, body)
        if parent:
            self._release_finished(scope, parent)

    def finish(self, scope, work_id, generation, *, state="completed", now=None):
        if state not in ("completed", "failed"):
            raise CoreError("invalid_transition", "Work finish must be completed or failed")
        with self.journal.transaction():
            row = self.get(scope, work_id)
            self._lineage(scope, row, self._now(now), executing=False, check_goal=False)
            if row.value["generation"] != generation:
                raise CoreError("stale_authority", "Work generation changed")
            row = self._save(scope, row, {**row.value, "state": state, "lease_until": None})
            for child in self.journal.list("work", scope):
                if child.value["parent_id"] == work_id and (state == "failed" or not child.value["retain_after_parent"]):
                    self.cancel(scope, child.id, reason="parent_finished", now=now)
            self._release_finished(scope, self.get(scope, row.id))
            return self.get(scope, row.id)

    def cancel(self, scope, work_id, *, reason="cancelled", now=None):
        text(reason, "reason")
        self._now(now)
        with self.journal.transaction():
            self.get(scope, work_id)
            affected, pending = [], [work_id]
            while pending:
                key = pending.pop(0)
                row = self.get(scope, key)
                pending.extend(child.id for child in self.journal.list("work", scope) if child.value["parent_id"] == key)
                if not row.value["revoked"]:
                    row = self._save(scope, row, {**row.value, "state": row.value["state"] if row.value["state"] in TERMINAL else "cancelled", "generation": row.value["generation"] + 1, "revoked": True, "cancel_reason": reason, "lease_until": None})
                affected.append(row)
            for row in reversed(affected):
                self._release_finished(scope, self.get(scope, row.id))
            return [self.get(scope, row.id) for row in affected]

    def park(self, scope, work_id, generation, *, wait_id):
        with self.journal.transaction():
            row = self.get(scope, work_id)
            self._lineage(scope, row, self._now())
            wait = required(self.journal, "waits", wait_id, scope)
            if row.value["generation"] != generation or wait.value["owner_id"] != work_id or wait.value["state"] != "armed":
                raise CoreError("invalid_wait", "Only a matching durable armed wait permits compute release")
            return self._save(scope, row, {**row.value, "state": "waiting", "lease_until": None, "wait_id": wait_id})

    def recover(self, scope, *, now=None):
        now, changed = self._now(now), []
        with self.journal.transaction():
            for row in self.journal.list("work", scope):
                if row.value["state"] == "running" and row.value["lease_until"] <= now:
                    unresolved = any(claim.value["work_id"] == row.id and claim.value["state"] == "claimed" for claim in self.journal.list("work_claims", scope))
                    for child in self.journal.list("work", scope):
                        if child.value["parent_id"] == row.id:
                            self.cancel(scope, child.id, reason="ancestor_lease_expired", now=now)
                    row = self.get(scope, row.id)
                    changed.append(self._save(scope, row, {**row.value, "state": "recovery_required" if unresolved else "queued", "generation": row.value["generation"] + 1, "lease_until": None}))
            self.fire_waits(scope, now=now)
            self.reconcile_usage(scope)
        return changed

    def register_wait(self, scope, owner_id, trigger, *, wait_id, after_seq, expires_at=None, now=None):
        validate_trigger(trigger)
        text(wait_id, "wait_id", 256)
        now = self._now(now)
        if type(after_seq) is not int or after_seq < 0 or (expires_at is not None and timestamp(expires_at) <= now):
            raise CoreError("invalid_wait", "Wake cursor or expiry is invalid")
        body = {"owner_id": owner_id, "trigger": trigger, "after_seq": after_seq, "expires_at": expires_at, "state": "armed", "work_id": None, "fired_by": None}
        with self.journal.transaction():
            if not any(self.journal.get(namespace, owner_id, scope) for namespace in ("work", "commitments", "goals", "subagents")):
                raise CoreError("not_found", "Wake owner must exist in this scope")
            prior = self.journal.get("waits", wait_id, scope)
            if prior:
                if any(prior.value[key] != body[key] for key in ("owner_id", "trigger", "after_seq", "expires_at")):
                    raise CoreError("dedupe_conflict", "Wait identity was reused")
                return prior
            row = self.journal.put("waits", wait_id, scope, body)
            self.journal.append(scope, "wait.armed", {"wait_id": wait_id, **body}, source="scheduler")
            return row

    def cancel_wait(self, scope, wait_id):
        with self.journal.transaction():
            row = required(self.journal, "waits", wait_id, scope)
            if row.value["state"] in {"cancelled", "expired"}:
                return row
            if row.value["work_id"]:
                self.cancel(scope, row.value["work_id"], reason="wait_cancelled")
            updated = self.journal.put("waits", wait_id, scope, {**row.value, "state": "cancelled"}, row.revision)
            self.journal.append(scope, "wait.cancelled", {"wait_id": wait_id}, source="scheduler")
            return updated

    def _matches_wait(self, scope, trigger, event):
        kind, payload = trigger["kind"], event.payload
        if kind == "process_exit" and event.kind == "tool.result" and event.source == "tool_runner":
            if payload.get("attempt_id") != trigger["process_id"]:
                return False
            persisted = next((row for row in self.journal.list("tool_requests", scope)
                if row.value.get("backend_kind") == "process" and row.value["result"].get("attempt_id") == trigger["process_id"]
                and row.value["result"].get("action_id") == payload.get("action_id")), None)
            if persisted is None or canonical_json(persisted.value["result"]) != canonical_json(payload):
                return False
            data, error = payload.get("data") or {}, payload.get("error") or {}
            return (payload.get("state") == "completed" and type(data.get("exit_code")) is int
                or payload.get("state") == "cancelled" and error.get("details", {}).get("execution_stopped") is True)
        pairs = {"schedule": ("schedule.due", "job_id", "scheduler"), "child_terminal": ("child.terminal", "child_id", "subagents"), "process_exit": ("process.exited", "process_id", "tool_runner")}
        if kind in pairs:
            event_kind, key, source = pairs[kind]
            return event.kind == event_kind and event.source == source and payload.get(key) == trigger[key]
        if kind in {"file_changed", "repository_changed"}:
            return event.kind == "awareness.observed" and event.source == "awareness" and payload.get("source_type") == ("file" if kind == "file_changed" else "repository") and payload.get("object_id") == trigger["object_id"]
        if kind in {"channel_message", "user_response"}:
            binding = self.journal.get("bindings", payload.get("binding_id", ""), scope)
            return event.kind in {"user.prompt", "user.steer", "channel.message"} and event.source == "channel_core" and (payload.get("conversation_id") == trigger["conversation_id"] or (binding is not None and binding.value.get("conversation") == trigger["conversation_id"]))
        if kind == "external_condition":
            return event.kind == "external.condition" and event.source in {"runtime", "awareness"} and payload.get("connector") == trigger["connector"] and all(canonical_json(payload.get(key)) == canonical_json(value) for key, value in trigger["matches"].items())
        return False

    def fire_waits(self, scope, *, now=None):
        now, result = self._now(now), []
        with self.journal.transaction():
            for row in self.journal.list("waits", scope):
                body = dict(row.value)
                if body["state"] == "armed":
                    if body["expires_at"] is not None and body["expires_at"] <= now:
                        self.journal.put("waits", row.id, scope, {**body, "state": "expired"}, row.revision)
                        continue
                    event_id = "timer" if body["trigger"]["kind"] == "time" and body["trigger"]["at"] <= now else None
                    cursor = body["after_seq"]
                    while event_id is None and (page := self.journal.events(scope, after=cursor)):
                        for event in page:
                            cursor = event.seq
                            if self._matches_wait(scope, body["trigger"], event):
                                event_id = event.id
                                break
                    if event_id is None:
                        continue
                    body.update(state="fired", fired_by=event_id)
                    row = self.journal.put("waits", row.id, scope, body, row.revision)
                if body["state"] == "fired" and body["work_id"] is None:
                    work = self.submit(scope, "wake", {"wait_id": row.id, "owner_id": body["owner_id"], "fired_by": body["fired_by"]}, dedupe_key=f"wake:{row.id}", now=now)
                    body["work_id"] = work.id
                    row = self.journal.put("waits", row.id, scope, body, row.revision)
                    owner = self.journal.get("work", body["owner_id"], scope)
                    if owner and owner.value["state"] == "waiting" and owner.value.get("wait_id") == row.id and not owner.value["revoked"]:
                        self._save(scope, owner, {**owner.value, "state": "queued", "wait_id": None, "not_before": now})
                    self.journal.append(scope, "wait.fired", {"wait_id": row.id, **body}, source="scheduler")
                    result.append(row)
            return result

    def schedule(self, scope, spec, kind, payload, *, resources=None, capabilities=(), goal_id=None, missed_policy="run_once", max_replay=10, now=None, schedule_id=None):
        now = self._now(now)
        spec = validate_schedule(spec)
        if missed_policy not in ("skip", "run_once", "replay_bounded") or type(max_replay) is not int or not 1 <= max_replay <= 100:
            raise CoreError("invalid_schedule", "Missed-run policy and replay bound are invalid")
        text(kind, "kind", 128)
        if not isinstance(payload, dict):
            raise CoreError("invalid_schedule", "Schedule payload must be an object")
        canonical_json(payload)
        body = {"spec": spec, "kind": kind, "payload": payload, "resources": amounts({} if resources is None else resources), "capabilities": _capabilities(capabilities), "goal_id": goal_id, "missed_policy": missed_policy, "max_replay": max_replay, "state": "active", "next_due": spec["at"] if spec["kind"] == "once" else next_occurrence(spec, now), "occurrences": 0}
        with self.journal.transaction():
            if goal_id:
                self.goals.get(scope, goal_id)
            row = self.journal.put("schedules", text(schedule_id, "schedule_id", 256) if schedule_id else new_id("schedule"), scope, body)
            self.journal.append(scope, "schedule.created", {"job_id": row.id, **body}, source="scheduler")
            return row

    def set_schedule_state(self, scope, schedule_id, state, *, expected_revision):
        if state not in ("active", "paused", "cancelled"):
            raise CoreError("invalid_schedule", "Unknown schedule lifecycle state")
        with self.journal.transaction():
            row = required(self.journal, "schedules", schedule_id, scope)
            if row.value["state"] in {"completed", "cancelled"}:
                raise CoreError("invalid_transition", "Terminal schedule cannot be restarted")
            updated = self.journal.put("schedules", schedule_id, scope, {**row.value, "state": state}, expected_revision)
            self.journal.append(scope, "schedule.changed", {"job_id": row.id, "state": state}, source="scheduler")
            return updated

    def tick(self, scope, *, now=None):
        now, result = self._now(now), []
        with self.journal.transaction():
            for row in self.journal.list("schedules", scope):
                body = dict(row.value)
                due = body["next_due"]
                if body["state"] != "active" or due is None or due > now:
                    continue
                occurrences = []
                if body["missed_policy"] != "skip" or now - due < 1:
                    occurrences.append(due)
                    if body["missed_policy"] == "replay_bounded":
                        while len(occurrences) < body["max_replay"]:
                            following = next_occurrence(body["spec"], occurrences[-1] + .001)
                            if following is None or following > now:
                                break
                            occurrences.append(following)
                for instant in occurrences:
                    work = self.submit(scope, body["kind"], {"input": body["payload"], "schedule_id": row.id, "scheduled_for": instant}, resources=body["resources"], capabilities=body["capabilities"], goal_id=body["goal_id"], dedupe_key=f"schedule:{row.id}:{instant}", now=now)
                    self.journal.append(scope, "schedule.due", {"job_id": row.id, "scheduled_for": instant, "work_id": work.id}, source="scheduler", dedupe_key=f"schedule-due:{row.id}:{instant}")
                    result.append(work)
                body["next_due"] = next_occurrence(body["spec"], now + .001)
                body["occurrences"] += len(occurrences)
                body["last_catchup"] = {"from": due, "through": now, "emitted": len(occurrences), "policy": body["missed_policy"]}
                if body["next_due"] is None:
                    body["state"] = "completed"
                self.journal.put("schedules", row.id, scope, body, row.revision)
            self.fire_waits(scope, now=now)
        return result


def validate_schedule(spec):
    if not isinstance(spec, dict) or not isinstance(spec.get("kind"), str):
        raise CoreError("invalid_schedule", "Schedule requires an explicit kind")
    kind = spec["kind"]
    if kind == "once":
        if set(spec) != {"kind", "at"}:
            raise CoreError("invalid_schedule", "One-shot schedule requires its due instant")
        timestamp(spec["at"])
    elif kind == "interval":
        if set(spec) != {"kind", "anchor", "every_seconds"} or type(spec.get("every_seconds")) not in (int, float) or not math.isfinite(spec["every_seconds"]) or spec["every_seconds"] < 1:
            raise CoreError("invalid_schedule", "Interval must be finite and at least one second")
        timestamp(spec["anchor"])
    elif kind == "calendar":
        if set(spec) != {"kind", "timezone", "hour", "minute", "weekdays", "fold"} or type(spec.get("hour")) is not int or not 0 <= spec["hour"] < 24 or type(spec.get("minute")) is not int or not 0 <= spec["minute"] < 60 or not isinstance(spec.get("weekdays"), list) or not spec["weekdays"] or any(type(day) is not int or not 0 <= day <= 6 for day in spec["weekdays"]) or spec.get("fold") not in ("first", "second", "both"):
            raise CoreError("invalid_schedule", "Calendar requires valid local time, weekdays and DST fold policy")
        try:
            ZoneInfo(spec["timezone"])
        except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
            raise CoreError("invalid_schedule", "Calendar timezone is unavailable") from exc
    else:
        raise CoreError("invalid_schedule", "Supported schedules are once, interval and local weekday calendar")
    return spec


def next_occurrence(spec, after):
    kind = spec["kind"]
    if kind == "once":
        return spec["at"] if spec["at"] >= after else None
    if kind == "interval":
        return spec["anchor"] + max(0, math.ceil((after - spec["anchor"]) / spec["every_seconds"])) * spec["every_seconds"]
    zone = ZoneInfo(spec["timezone"])
    start = datetime.fromtimestamp(after, zone).date()
    for offset in range(15):
        day = start + timedelta(days=offset)
        if day.weekday() not in spec["weekdays"]:
            continue
        wall = datetime(day.year, day.month, day.day, spec["hour"], spec["minute"])
        instants = sorted({wall.replace(tzinfo=zone, fold=fold).timestamp() for fold in (0, 1) if datetime.fromtimestamp(wall.replace(tzinfo=zone, fold=fold).timestamp(), zone).replace(tzinfo=None) == wall})
        if not instants:
            continue  # A nonexistent local time is explicitly skipped at the DST gap.
        if spec["fold"] != "both":
            instants = [instants[0] if spec["fold"] == "first" else instants[-1]]
        for instant in instants:
            if instant >= after:
                return instant
    raise CoreError("invalid_schedule", "Calendar has no valid occurrence within its bounded weekly horizon")
