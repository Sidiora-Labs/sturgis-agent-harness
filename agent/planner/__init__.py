"""Versioned dependency plans with explicit assignments and observed result checks."""

from agent.awareness import required, text
from agent.awareness.evidence import validate_checks, verify_checks
from agent.goals import amounts
from agent.protocol import CoreError, canonical_json, new_id
from agent.state import Journal


STEP_TRANSITIONS = {"pending": {"in_progress", "skipped"}, "in_progress": {"completed", "blocked"}, "blocked": {"in_progress", "skipped"}, "completed": set(), "skipped": set()}


def _steps(steps):
    if not isinstance(steps, list) or not steps or len(steps) > 256:
        raise CoreError("invalid_plan", "A plan needs a bounded nonempty step list")
    result, ids = [], set()
    for step in steps:
        if not isinstance(step, dict) or set(step) - {"id", "description", "dependencies", "assignee", "checks", "expectation", "budget"}:
            raise CoreError("invalid_plan", "Unknown step fields")
        key = text(step.get("id"), "step_id", 256)
        if key in ids:
            raise CoreError("invalid_plan", "Step identities must be unique")
        ids.add(key)
        dependencies, assignee = step.get("dependencies", []), step.get("assignee", {"kind": "agent"})
        if not isinstance(dependencies, list) or any(not isinstance(value, str) for value in dependencies) or len(set(dependencies)) != len(dependencies):
            raise CoreError("invalid_plan", "Step dependencies must be unique identities")
        if not isinstance(assignee, dict) or not isinstance(assignee.get("kind"), str) or assignee["kind"] not in {"agent", "user", "child"} or set(assignee) != ({"kind", "id"} if assignee.get("kind") == "child" else {"kind"}):
            raise CoreError("invalid_plan", "Assignee must be agent, user, or an identified child")
        if assignee["kind"] == "child":
            text(assignee["id"], "child_id", 256)
        expectation = step.get("expectation", {})
        if not isinstance(expectation, dict):
            raise CoreError("invalid_plan", "Expectation must be an object")
        canonical_json(expectation)
        result.append({"id": key, "description": text(step.get("description"), "description"), "dependencies": dependencies, "assignee": assignee, "checks": validate_checks(step.get("checks", [])), "expectation": expectation, "budget": amounts(step.get("budget", {})), "state": "pending", "evidence": {}, "reason": None})
    if any(not set(step["dependencies"]) <= ids for step in result):
        raise CoreError("invalid_plan", "Dependencies reference absent steps")
    visited = set()
    while len(visited) < len(result):
        ready = {step["id"] for step in result if step["id"] not in visited and set(step["dependencies"]) <= visited}
        if not ready:
            raise CoreError("dependency_cycle", "Plan dependencies contain a cycle")
        visited.update(ready)
    return result


class Planner:
    def __init__(self, journal: Journal):
        self.journal = journal

    @staticmethod
    def classify(request, *, tool_count=0, delegated=False, recurring=False, monitoring=False, step_count=1):
        text(request, "request")
        if type(tool_count) is not int or tool_count < 0 or type(step_count) is not int or step_count < 1 or any(type(value) is not bool for value in (delegated, recurring, monitoring)):
            raise CoreError("invalid_plan", "Routing requires validated capability and intent signals")
        if recurring:
            return "recurring"
        if monitoring:
            return "monitoring"
        if delegated:
            return "delegated"
        return "planned" if tool_count > 1 or step_count > 1 else "single_tool" if tool_count == 1 else "direct"

    def get(self, scope, plan_id):
        return required(self.journal, "plans", plan_id, scope)

    def _save(self, scope, plan_id, body, revision):
        row = self.journal.put("plans", plan_id, scope, body, revision)
        self.journal.put("plan_history", f"{plan_id}:{row.revision}", scope, body)
        self.journal.append(scope, "plan.changed", {"id": plan_id, "revision": row.revision, **body}, source="planner")
        return row

    def create(self, scope, steps, *, goal_id=None, commitment_id=None, budget=None):
        body = {"steps": _steps(steps), "goal_id": goal_id, "commitment_id": commitment_id, "budget": amounts({} if budget is None else budget), "state": "active", "reason": None}
        with self.journal.transaction():
            for namespace, key in (("goals", goal_id), ("commitments", commitment_id)):
                if key is not None:
                    required(self.journal, namespace, key, scope)
            return self._save(scope, new_id("plan"), body, 0)

    def revise(self, scope, plan_id, steps, *, expected_revision, reason):
        normalized, reason = _steps(steps), text(reason, "reason")
        with self.journal.transaction():
            row = self.get(scope, plan_id)
            if row.value["state"] not in {"active", "paused"} or any(step["state"] == "in_progress" for step in row.value["steps"]):
                raise CoreError("invalid_transition", "Revise only a live plan without in-flight steps")
            old = {step["id"]: step for step in row.value["steps"]}
            for step in normalized:
                prior = old.get(step["id"])
                if prior and all(step[key] == prior[key] for key in ("description", "dependencies", "assignee", "checks", "expectation", "budget")):
                    step.update({key: prior[key] for key in ("state", "evidence", "reason")})
            return self._save(scope, plan_id, {**row.value, "steps": normalized, "reason": reason}, expected_revision)

    def ready(self, scope, plan_id):
        row = self.get(scope, plan_id)
        if row.value["state"] != "active":
            return []
        completed = {step["id"] for step in row.value["steps"] if step["state"] == "completed"}
        return [step for step in row.value["steps"] if step["state"] == "pending" and set(step["dependencies"]) <= completed]

    def transition(self, scope, plan_id, state, *, expected_revision, reason=None):
        text(state, "state", 128)
        with self.journal.transaction():
            row = self.get(scope, plan_id)
            transitions = {"active": {"paused", "completed", "abandoned"}, "paused": {"active", "abandoned"}}
            if state not in transitions.get(row.value["state"], set()):
                raise CoreError("invalid_transition", "Invalid plan lifecycle transition")
            if state == "completed" and any(step["state"] not in {"completed", "skipped"} for step in row.value["steps"]):
                raise CoreError("incomplete_plan", "Plan has unfinished steps")
            if state == "abandoned":
                text(reason, "reason")
            return self._save(scope, plan_id, {**row.value, "state": state, "reason": reason}, expected_revision)

    def transition_step(self, scope, plan_id, step_id, state, *, expected_revision, evidence=None, reason=None):
        text(state, "state", 128)
        with self.journal.transaction():
            row = self.get(scope, plan_id)
            step = next((step for step in row.value["steps"] if step["id"] == step_id), None)
            if row.value["state"] != "active" or step is None or state not in STEP_TRANSITIONS[step["state"]]:
                raise CoreError("invalid_transition", "Step transition is not permitted")
            complete = {item["id"] for item in row.value["steps"] if item["state"] == "completed"}
            if state == "in_progress" and not set(step["dependencies"]) <= complete:
                raise CoreError("dependency_pending", "Step dependencies remain incomplete")
            if state == "completed":
                step["evidence"] = verify_checks(self.journal, scope, step["checks"], evidence)
            if state in {"blocked", "skipped"}:
                step["reason"] = text(reason, "reason")
            step["state"] = state
            return self._save(scope, plan_id, row.value, expected_revision)
