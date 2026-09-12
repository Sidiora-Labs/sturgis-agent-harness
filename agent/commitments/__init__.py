"""Accepted responsibilities with evidence and a durable wake-before-yield seam."""

from agent.awareness import required, text, timestamp
from agent.awareness.evidence import validate_checks, verify_checks
from agent.protocol import CoreError, canonical_json, new_id
from agent.state import Journal, Record


TRANSITIONS = {"proposed": {"accepted", "cancelled"}, "accepted": {"fulfilled", "cancelled", "failed"}, "fulfilled": set(), "cancelled": set(), "failed": set()}
TRIGGERS = {"time": {"at"}, "schedule": {"job_id"}, "child_terminal": {"child_id"}, "process_exit": {"process_id"}, "file_changed": {"object_id"}, "repository_changed": {"object_id"}, "channel_message": {"conversation_id"}, "external_condition": {"connector", "matches"}, "user_response": {"conversation_id"}}


def validate_trigger(trigger):
    if not isinstance(trigger, dict) or not isinstance(trigger.get("kind"), str) or trigger["kind"] not in TRIGGERS:
        raise CoreError("invalid_trigger", "Unknown durable wake trigger")
    fields = TRIGGERS[trigger["kind"]]
    if set(trigger) != fields | {"kind"}:
        raise CoreError("invalid_trigger", "Wake trigger fields do not match its kind")
    for key in fields:
        if key == "at":
            timestamp(trigger[key])
        elif key == "matches":
            if not isinstance(trigger[key], dict) or not trigger[key]:
                raise CoreError("invalid_trigger", "External condition needs exact matching fields")
            canonical_json(trigger[key])
        else:
            text(trigger[key], key, 512)
    return trigger


class CommitmentService:
    def __init__(self, journal: Journal, *, cancel_wait=None):
        self.journal, self.cancel_wait = journal, cancel_wait

    def get(self, scope, commitment_id):
        return required(self.journal, "commitments", commitment_id, scope)

    def list(self, scope, *, now=None):
        now = timestamp(now)
        return [{"id": row.id, "revision": row.revision, **row.value, "overdue": row.value["state"] == "accepted" and row.value["expires_at"] is not None and row.value["expires_at"] <= now} for row in self.journal.list("commitments", scope)]

    def _save(self, scope, key, body, revision):
        row = self.journal.put("commitments", key, scope, body, revision)
        self.journal.append(scope, "commitment.changed", {"id": key, "revision": row.revision, **body}, source="commitments")
        return row

    def create(self, scope, description, *, beneficiary, criteria=None, expires_at=None, now=None):
        now = timestamp(now)
        if expires_at is not None and timestamp(expires_at) <= now:
            raise CoreError("invalid_time", "Commitment expiry must follow its creation")
        body = {"description": text(description, "description"), "beneficiary": text(beneficiary, "beneficiary"), "criteria": validate_checks([] if criteria is None else criteria), "expires_at": expires_at, "state": "proposed", "progress": "pending", "wait_id": None, "trigger": None, "evidence": {}, "created_at": now, "reason": None}
        with self.journal.transaction():
            return self._save(scope, new_id("commitment"), body, 0)

    def transition(self, scope, commitment_id, state, *, expected_revision, evidence=None, reason=None):
        text(state, "state", 128)
        with self.journal.transaction():
            row = self.get(scope, commitment_id)
            if state not in TRANSITIONS[row.value["state"]]:
                raise CoreError("invalid_transition", "Commitment transition is not permitted")
            body = {**row.value, "state": state}
            if state == "fulfilled":
                body["evidence"] = verify_checks(self.journal, scope, body["criteria"], evidence)
            if state in {"cancelled", "failed"}:
                body["reason"] = text(reason, "reason")
            if row.value["wait_id"] is not None:
                if self.cancel_wait is None:
                    raise CoreError("wait_service_unavailable", "An armed wake must be cancelled transactionally")
                self.cancel_wait(scope, row.value["wait_id"])
                wait = required(self.journal, "waits", row.value["wait_id"], scope)
                if wait.value.get("state") not in {"cancelled", "expired"}:
                    raise CoreError("invalid_wait", "Wake cancellation was not durably confirmed")
            body["progress"] = "in_progress" if state == "accepted" else "terminal"
            return self._save(scope, commitment_id, body, expected_revision)

    def set_progress(self, scope, commitment_id, progress, *, expected_revision, reason=None):
        if not isinstance(progress, str) or progress not in {"pending", "in_progress", "blocked"}:
            raise CoreError("invalid_transition", "Unknown commitment progress")
        with self.journal.transaction():
            row = self.get(scope, commitment_id)
            if row.value["state"] != "accepted":
                raise CoreError("invalid_transition", "Only accepted responsibilities can progress")
            if progress == "blocked":
                text(reason, "reason")
            return self._save(scope, commitment_id, {**row.value, "progress": progress, "reason": reason}, expected_revision)

    def begin_wait(self, scope, commitment_id, trigger, *, registrar, after_seq, expected_revision, expires_at=None):
        validate_trigger(trigger)
        if not callable(registrar) or type(after_seq) is not int or after_seq < 0:
            raise CoreError("invalid_wait", "A durable registrar and source cursor are required")
        if expires_at is not None:
            timestamp(expires_at)
        with self.journal.transaction():
            row = self.get(scope, commitment_id)
            if row.value["state"] != "accepted" or row.value["wait_id"] is not None:
                raise CoreError("invalid_transition", "Responsibility is terminal or already waiting")
            wait_id = new_id("wait")
            wait = registrar(scope, commitment_id, trigger, wait_id=wait_id, after_seq=after_seq, expires_at=expires_at)
            persisted = self.journal.get("waits", wait_id, scope)
            if not isinstance(wait, Record) or wait.scope != scope or wait.id != wait_id or persisted != wait or wait.value.get("state") != "armed" or wait.value.get("owner_id") != commitment_id:
                raise CoreError("invalid_wait", "Registrar did not durably arm the scoped responsibility")
            updated = self._save(scope, commitment_id, {**row.value, "wait_id": wait_id, "trigger": trigger, "progress": "waiting"}, expected_revision)
            return updated, {"release_compute": True, "wait_id": wait_id}

    def resume_wait(self, scope, commitment_id, *, wait_id, expected_revision):
        with self.journal.transaction():
            row = self.get(scope, commitment_id)
            wait = required(self.journal, "waits", wait_id, scope)
            if row.value["state"] != "accepted" or row.value["wait_id"] != wait_id or wait.value.get("state") != "fired" or wait.value.get("owner_id") != commitment_id:
                raise CoreError("invalid_wait", "A matching fired wake is required")
            return self._save(scope, commitment_id, {**row.value, "wait_id": None, "trigger": None, "progress": "in_progress"}, expected_revision)
