"""Human policy and one-use exact-call grants around existing effect authority."""
from __future__ import annotations

import hashlib
import re
import time

from agent.protocol import CoreError, ToolCall, canonical_json, strict_loads
from consumer.contracts import identifier, public_record

POLICIES = "consumer.permissions"
DECISIONS = "consumer.permission_decisions"
BASELINE = {"content.read", "workspace.read"}
MAX_CALL_BYTES = 262144


def _hash(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


class Permissions:
    def __init__(self, core):
        self.core, self.journal, self.scope = core, core.journal, core.scope
        self._installed = False

    @staticmethod
    def _reviewer(reviewer):
        if reviewer != "owner":
            raise CoreError("forbidden", "An authenticated owner decision is required")

    def _descriptor(self, tool, version=None):
        if not isinstance(tool, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", tool):
            raise CoreError("invalid_tool", "A bounded registered tool name is required")
        matches = [row for row in self.core.tools.descriptors() if row["name"] == tool and (version is None or row["version"] == version)]
        if len(matches) != 1:
            raise CoreError("tool_unavailable", "Select an unambiguous current tool version")
        descriptor = matches[0]
        connection = None
        if tool.startswith("connection."):
            parts = tool.split(".")
            row = self.journal.get("consumer.connections", parts[1], self.scope) if len(parts) == 3 else None
            if row is None or str(row.revision) != descriptor["version"]:
                raise CoreError("stale_connection", "Tool does not match its current connection")
            connection = {"id": row.id, "revision": row.revision, "credential_ref": row.value["credential_ref"]}
        return descriptor, {"descriptor_hash": _hash(descriptor), "tool_version": descriptor["version"], "connection": connection}

    def _policy(self, tool, binding):
        row = self.journal.get(POLICIES, tool, self.scope)
        if row is None:
            return {"id": tool, "revision": 0, "mode": "allow" if tool in BASELINE else "ask", "binding": binding, "stale": False}
        stale = row.value["binding"] != binding
        return {**public_record(row), "mode": "deny" if stale else row.value["mode"], "stale": stale}

    def list(self):
        result = []
        for descriptor in self.core.tools.descriptors():
            _, binding = self._descriptor(descriptor["name"], descriptor["version"])
            result.append({**self._policy(descriptor["name"], binding), "tool": descriptor})
        return result

    def _fence(self, capabilities, reason, tool=None):
        affected, schedules = [], []
        executing = {row.value["work_id"] for row in self.journal.list("tool_requests", self.scope)
                     if tool is not None and row.value["call"]["tool"] == tool and row.value["result"]["state"] == "running"}
        for row in self.journal.list("work", self.scope):
            if row.value["state"] not in {"completed", "failed", "cancelled"} and (row.id in executing or set(row.value["capabilities"]) & capabilities):
                affected.extend(item.id for item in self.core.scheduler.cancel(self.scope, row.id, reason=reason))
        for row in self.journal.list("schedules", self.scope):
            if row.value["state"] == "active" and set(row.value["capabilities"]) & capabilities:
                self.core.scheduler.set_schedule_state(self.scope, row.id, "paused", expected_revision=row.revision)
                schedules.append(row.id)
        for work_id in set(affected):
            row = self.journal.get("runtime_turns", work_id, self.scope)
            if row and row.value["state"] not in {"completed", "failed", "interrupted"}:
                self.journal.put(row.namespace, row.id, self.scope, {**row.value, "cancel_requested": True}, row.revision)
        for child in self.journal.list("subagents", self.scope):
            if child.value["work_id"] in affected and child.value["state"] == "active":
                self.core.subagents.cancel(self.scope, child.id, reason=reason)
        return {"work_ids": sorted(set(affected)), "schedule_ids": schedules,
                "effects": "New claims fenced; in-flight cancellation requested"}

    def refresh_capabilities(self):
        with self.journal.transaction():
            capabilities = set()
            for item in self.list():
                if item["mode"] != "deny":
                    capabilities.update(item["tool"]["required_capabilities"])
            previous = set(self.core.capabilities)
            fenced = self._fence(previous - capabilities, "permission_or_connection_revoked")
            self.core.capabilities = tuple(sorted(capabilities))
            return {"capabilities": list(self.core.capabilities), "fenced": fenced}

    def set(self, tool, mode, expected_revision=0, *, reviewer="owner"):
        self._reviewer(reviewer)
        if not isinstance(mode, str) or mode not in {"allow", "ask", "deny"}:
            raise CoreError("invalid_policy", "Permission mode must be allow, ask or deny")
        with self.journal.transaction():
            descriptor, binding = self._descriptor(tool)
            previous = self._policy(tool, binding)
            row = self.journal.put(POLICIES, tool, self.scope, {"mode": mode, "binding": binding,
                "reviewer": reviewer, "changed_at": time.time()}, expected_revision)
            fenced = self._fence(set(descriptor["required_capabilities"]), "permission_restricted", tool) if (
                previous["mode"] == "allow" and mode != "allow" or previous["mode"] == "ask" and mode == "deny") else {"work_ids": [], "schedule_ids": []}
            refreshed = self.refresh_capabilities()
            self.journal.append(self.scope, "permission.changed", {"tool": tool, "revision": row.revision,
                "mode": mode, "binding": binding}, source="consumer.permissions")
            return {**public_record(row), "fenced": fenced, "capabilities": refreshed["capabilities"]}

    def _fingerprint(self, call, constraints=None):
        _, binding = self._descriptor(call.tool, call.tool_version)
        policy = self._policy(call.tool, binding)
        fingerprint = {"scope": self.scope, "call": call.as_dict(), "binding": binding,
                       "policy_revision": policy["revision"], "constraints": constraints or {}}
        return _hash(fingerprint), policy

    def install(self):
        if self._installed:
            return self.refresh_capabilities()
        previous = self.core.tools.authorize

        def authorize(scope, grant, call):
            if scope != self.scope:
                raise CoreError("scope_denied", "Permission authority belongs to another runtime")
            digest, policy = self._fingerprint(call)
            if policy["mode"] == "deny":
                raise CoreError("permission_denied", "Current owner policy denies this tool")
            # Existing connection and scheduler authority must succeed first. The
            # dispatcher's surrounding transaction rolls this claim back on rejection.
            authority = previous(scope, grant, call)
            if policy["mode"] == "allow":
                return authority
            digest, _ = self._fingerprint(call, authority.get("constraints", {}))
            for row in self.journal.list(DECISIONS, self.scope):
                if row.value["digest"] == digest and row.value["state"] == "approved" and row.value["expires_at"] > time.time():
                    self.journal.put(DECISIONS, row.id, self.scope, {**row.value, "state": "consumed",
                        "claim_id": grant["claim_id"], "work_id": grant["work_id"], "consumed_at": time.time()}, row.revision)
                    self.journal.append(self.scope, "permission.consumed", {"request_event_id": row.id,
                        "claim_id": grant["claim_id"], "work_id": grant["work_id"], "digest": digest}, source="consumer.permissions")
                    return authority
            raw = canonical_json(call.as_dict()).encode()
            if len(raw) > MAX_CALL_BYTES:
                raise CoreError("approval_too_large", "Call exceeds the bounded human approval representation")
            artifact = self.core.artifacts.put(self.scope, raw, "application/json")
            raise CoreError("approval_required", "Owner approval is required for this exact invocation",
                permission_request={"digest": digest, "artifact_id": artifact.id, "tool": call.tool,
                    "tool_version": call.tool_version, "policy_revision": policy["revision"],
                    "work_id": grant["work_id"], "generation": grant["generation"]})

        self.core.tools.authorize = authorize
        self._installed = True
        return self.refresh_capabilities()

    def request(self, event_id):
        event = self.journal.event(self.scope, identifier(event_id))
        if (event is None or event.kind != "tool.result" or event.source != "tool_runner"
                or event.payload.get("state") != "rejected" or (event.payload.get("error") or {}).get("code") != "approval_required"):
            raise CoreError("invalid_approval", "Approval requires a trusted rejected invocation")
        try:
            request = event.payload["error"]["details"]["permission_request"]
            raw = self.core.artifacts.get(self.scope, request["artifact_id"])
            if len(raw) > MAX_CALL_BYTES:
                raise CoreError("invalid_approval", "Approval call exceeds its bound")
            call = ToolCall.from_dict(strict_loads(raw))
            work = self.core.scheduler.get(self.scope, request["work_id"])
            digest, policy = self._fingerprint(call, work.value["constraints"])
            stale = digest != request["digest"] or policy["mode"] != "ask"
        except CoreError as exc:
            if exc.code in {"tool_unavailable", "stale_connection"}:
                stale = True
            else:
                raise
        except (KeyError, TypeError):
            raise CoreError("invalid_approval", "Rejected invocation evidence is incomplete") from None
        decision = self.journal.get(DECISIONS, event_id, self.scope)
        state = decision.value["state"] if decision else "pending"
        if stale:
            state = "stale"
        elif event.created_at + 86400 <= time.time() or decision and decision.value["state"] == "approved" and decision.value["expires_at"] <= time.time():
            state = "expired"
        return {"id": event.id, "created_at": event.created_at, "state": state, "call": call.as_dict(),
                "digest": request["digest"], "work_id": request["work_id"],
                "delegated": bool(work.value["constraints"].get("delegated")),
                "decision": public_record(decision) if decision else None}

    def pending(self):
        items, cursor = [], 0
        while page := self.journal.events(self.scope, after=cursor, limit=1000, kinds=["tool.result"]):
            for event in page:
                if event.source == "tool_runner" and (event.payload.get("error") or {}).get("code") == "approval_required":
                    item = self.request(event.id)
                    if item["state"] == "pending":
                        items.append(item)
            cursor = page[-1].seq
        return items[-100:]

    def approve(self, event_id, expires_in=300, *, reviewer="owner"):
        self._reviewer(reviewer)
        if type(expires_in) is not int or not 1 <= expires_in <= 3600:
            raise CoreError("invalid_approval", "Approval expiry must be 1 to 3600 seconds")
        with self.journal.transaction():
            request = self.request(event_id)
            if request["state"] == "approved":
                return request["decision"]
            if request["state"] != "pending":
                raise CoreError("invalid_approval", "Only a current pending invocation can be approved")
            row = self.journal.put(DECISIONS, event_id, self.scope, {"digest": request["digest"],
                "state": "approved", "reviewer": reviewer, "expires_at": time.time() + expires_in})
            self.journal.append(self.scope, "permission.approved", {"request_event_id": event_id,
                "digest": request["digest"], "expires_at": row.value["expires_at"]}, source="consumer.permissions")
            return public_record(row)

    def deny(self, event_id, *, reviewer="owner"):
        self._reviewer(reviewer)
        with self.journal.transaction():
            request = self.request(event_id)
            if request["state"] not in {"pending", "approved"}:
                raise CoreError("invalid_approval", "Only pending or unused approvals can be denied")
            prior = self.journal.get(DECISIONS, event_id, self.scope)
            row = self.journal.put(DECISIONS, event_id, self.scope, {"digest": request["digest"],
                "state": "denied", "reviewer": reviewer, "expires_at": time.time()}, prior.revision if prior else 0)
            return public_record(row)
