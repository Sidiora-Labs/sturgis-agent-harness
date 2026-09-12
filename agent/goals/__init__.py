"""Evidence-bound goals and cumulative, idempotent resource accounting."""

from agent.awareness import required, text, timestamp
from agent.awareness.evidence import validate_checks, verify_checks
from agent.protocol import CoreError, new_id
from agent.state import Journal


DIMENSIONS = {"tokens", "elapsed_ms", "iterations", "turns", "children", "tool_calls", "reviews", "retries", "processes", "storage_bytes", "cost_microunits"}
TRANSITIONS = {"proposed": {"active", "abandoned"}, "active": {"paused", "waiting", "reviewing", "blocked", "achieved", "failed", "abandoned"}, "paused": {"active", "abandoned"}, "waiting": {"active", "blocked", "paused", "failed", "abandoned"}, "reviewing": {"active", "achieved", "blocked", "paused", "failed", "abandoned"}, "blocked": {"active", "paused", "failed", "abandoned"}, "achieved": set(), "failed": set(), "abandoned": set()}


def amounts(values):
    if not isinstance(values, dict) or set(values) - DIMENSIONS or any(type(value) is not int or value < 0 for value in values.values()):
        raise CoreError("invalid_budget", "Resource amounts must be nonnegative integer values of supported dimensions")
    return values


class GoalService:
    def __init__(self, journal: Journal):
        self.journal = journal

    def get(self, scope, goal_id):
        return required(self.journal, "goals", goal_id, scope)

    def list(self, scope):
        return self.journal.list("goals", scope)

    def _save(self, scope, goal_id, body, revision):
        row = self.journal.put("goals", goal_id, scope, body, revision)
        self.journal.append(scope, "goal.changed", {"id": goal_id, "revision": row.revision, **body}, source="goals")
        return row

    def create(self, scope, objective, *, limits=None, criteria=None, parent_id=None, now=None):
        body = {"objective": text(objective, "objective"), "state": "proposed", "limits": amounts({} if limits is None else limits), "usage": {}, "reserved": {}, "criteria": validate_checks([] if criteria is None else criteria), "parent_id": parent_id, "budget_overrun": False, "evidence": {}, "stop_reason": None, "archived": False, "created_at": timestamp(now), "continuation_key": new_id("continuation")}
        with self.journal.transaction():
            if parent_id is not None:
                self.get(scope, parent_id)
            return self._save(scope, new_id("goal"), body, 0)

    def transition(self, scope, goal_id, state, *, expected_revision, evidence=None, reason=None):
        text(state, "state", 128)
        with self.journal.transaction():
            row = self.get(scope, goal_id)
            if row.value["archived"] or state not in TRANSITIONS[row.value["state"]]:
                raise CoreError("invalid_transition", "Goal transition is not permitted")
            if state == "active" and row.value["budget_overrun"]:
                raise CoreError("budget_exceeded", "Resolve observed overage before admitting new work")
            body = {**row.value, "state": state, "stop_reason": None}
            if state == "achieved":
                body["evidence"] = verify_checks(self.journal, scope, body["criteria"], evidence)
            if state in {"blocked", "failed", "abandoned"}:
                body["stop_reason"] = text(reason, "reason")
            return self._save(scope, goal_id, body, expected_revision)

    def edit(self, scope, goal_id, *, expected_revision, objective=None, limits=None, criteria=None):
        with self.journal.transaction():
            row = self.get(scope, goal_id)
            if not TRANSITIONS[row.value["state"]] or row.value["archived"]:
                raise CoreError("invalid_transition", "Terminal goals cannot be edited")
            body = dict(row.value)
            if objective is not None:
                body["objective"] = text(objective, "objective")
            if criteria is not None:
                body["criteria"] = validate_checks(criteria)
            if limits is not None:
                amounts(limits)
                if any(body["usage"].get(key, 0) + body["reserved"].get(key, 0) > limit for key, limit in limits.items()):
                    raise CoreError("budget_exceeded", "Limits cannot be below usage plus outstanding reservations")
                body["limits"], body["budget_overrun"] = limits, False
            return self._save(scope, goal_id, body, expected_revision)

    def archive(self, scope, goal_id, *, expected_revision):
        with self.journal.transaction():
            row = self.get(scope, goal_id)
            if TRANSITIONS[row.value["state"]]:
                raise CoreError("invalid_transition", "Only terminal goals can be archived")
            return self._save(scope, goal_id, {**row.value, "archived": True}, expected_revision)

    def reserve(self, scope, goal_id, reservation_id, allocation):
        amounts(allocation)
        text(reservation_id, "reservation_id", 256)
        with self.journal.transaction():
            row = self.get(scope, goal_id)
            prior = self.journal.get("goal_reservations", reservation_id, scope)
            if prior:
                if prior.value["goal_id"] != goal_id or prior.value["allocation"] != allocation:
                    raise CoreError("dedupe_conflict", "Reservation identity was reused")
                return prior
            if row.value["state"] != "active" or row.value["archived"] or row.value["budget_overrun"]:
                raise CoreError("goal_inactive", "Goal cannot admit resource reservations")
            reserved = {**row.value["reserved"]}
            for key, value in allocation.items():
                reserved[key] = reserved.get(key, 0) + value
                if key in row.value["limits"] and reserved[key] + row.value["usage"].get(key, 0) > row.value["limits"][key]:
                    raise CoreError("budget_exceeded", "Cumulative goal budget would be exceeded", dimension=key)
            reservation = self.journal.put("goal_reservations", reservation_id, scope, {"goal_id": goal_id, "allocation": allocation, "spent": {}, "state": "reserved"})
            self._save(scope, goal_id, {**row.value, "reserved": reserved}, row.revision)
            return reservation

    def charge(self, scope, goal_id, reservation_id, usage_id, actual):
        amounts(actual)
        text(usage_id, "usage_id", 256)
        body = {"goal_id": goal_id, "reservation_id": reservation_id, "actual": actual}
        with self.journal.transaction():
            row = self.get(scope, goal_id)
            previous = self.journal.get("goal_usage", usage_id, scope)
            if previous:
                if previous.value != body:
                    raise CoreError("dedupe_conflict", "Usage identity was reused")
                return row
            reservation = required(self.journal, "goal_reservations", reservation_id, scope)
            if reservation.value["goal_id"] != goal_id:
                raise CoreError("scope_denied", "Reservation belongs to another goal")
            usage, reserved, spent = dict(row.value["usage"]), dict(row.value["reserved"]), dict(reservation.value["spent"])
            overrun = row.value["budget_overrun"]
            for key, value in actual.items():
                previous_spent, allocation = spent.get(key, 0), reservation.value["allocation"].get(key, 0)
                usage[key], spent[key] = usage.get(key, 0) + value, previous_spent + value
                if reservation.value["state"] == "reserved":
                    reserved[key] = reserved.get(key, 0) - (min(spent[key], allocation) - min(previous_spent, allocation))
                elif value:
                    overrun = True
                overrun |= spent[key] > allocation or (key in row.value["limits"] and usage[key] > row.value["limits"][key])
            self.journal.put("goal_usage", usage_id, scope, body)
            self.journal.put("goal_reservations", reservation_id, scope, {**reservation.value, "spent": spent}, reservation.revision)
            state = "blocked" if overrun and TRANSITIONS[row.value["state"]] else row.value["state"]
            updated = {**row.value, "usage": usage, "reserved": reserved, "budget_overrun": overrun, "state": state}
            if overrun:
                updated["stop_reason"] = "Observed usage exceeded its reservation or goal limit"
            return self._save(scope, goal_id, updated, row.revision)

    def release(self, scope, goal_id, reservation_id):
        with self.journal.transaction():
            row = self.get(scope, goal_id)
            reservation = required(self.journal, "goal_reservations", reservation_id, scope)
            if reservation.value["goal_id"] != goal_id:
                raise CoreError("scope_denied", "Reservation belongs to another goal")
            if reservation.value["state"] == "released":
                return row
            reserved = dict(row.value["reserved"])
            for key, value in reservation.value["allocation"].items():
                reserved[key] -= max(0, value - reservation.value["spent"].get(key, 0))
            self.journal.put("goal_reservations", reservation_id, scope, {**reservation.value, "state": "released"}, reservation.revision)
            return self._save(scope, goal_id, {**row.value, "reserved": reserved}, row.revision)
