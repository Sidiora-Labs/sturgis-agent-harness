"""Consumer work projections and dependency execution through core scheduling."""
from __future__ import annotations

import asyncio
import threading
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agent.goals import amounts
from agent.protocol import CoreError, canonical_json, new_id
from consumer.contracts import identifier, public_record

RUNS = "consumer.workflow_runs"
ATTENTION = "consumer.attention"
INBOX = "consumer.inbox"
TERMINAL = {"completed", "failed", "cancelled"}


class WorkViews:
    def __init__(self, core, *, poll_interval=.1):
        if type(poll_interval) not in {int, float} or not .01 <= poll_interval <= 10:
            raise CoreError("invalid_configuration", "Workflow polling interval must be bounded")
        self.core, self.journal, self.scope = core, core.journal, core.scope
        self.poll_interval, self._task = poll_interval, None
        self._advance_lock, self._paused = threading.RLock(), False

    def _rows(self, namespace):
        return [public_record(row) for row in self.journal.list(namespace, self.scope)]

    def _row(self, namespace, id):
        row = self.journal.get(namespace, identifier(id), self.scope)
        if row is None:
            raise CoreError("not_found", "Work item is unavailable")
        return row

    def overview(self):
        return {"work": self.list_work(), "goals": self.list_goals(), "commitments": self.list_commitments(),
                "schedules": self.list_schedules(), "workflows": self.list_workflows(),
                "children": self.list_children(), "inbox": self.inbox()}

    def list_work(self):
        return self._rows("work")

    def get_work(self, id):
        return public_record(self._row("work", id))

    def _cancel(self, id, reason):
        rows = self.core.scheduler.cancel(self.scope, id, reason=reason)
        ids = {row.id for row in rows}
        for child in self.journal.list("subagents", self.scope):
            if child.value["work_id"] in ids and child.value["state"] == "active":
                self.core.subagents.cancel(self.scope, child.id, reason=reason)
        for work in rows:
            turn = self.journal.get("runtime_turns", work.id, self.scope)
            if turn and turn.value["state"] not in {"completed", "failed", "interrupted"}:
                self.journal.put(turn.namespace, turn.id, self.scope, {**turn.value, "cancel_requested": True}, turn.revision)
        return [row.id for row in rows]

    def cancel_work(self, id, reason="owner_cancelled"):
        with self.journal.transaction():
            self._row("work", id)
            affected = self._cancel(id, reason)
            return {"work_ids": affected, "effects": "New execution fenced; in-flight cancellation requested"}

    def list_goals(self):
        return [public_record(row) for row in self.core.goals.list(self.scope)]

    def create_goal(self, objective, limits=None, criteria=None, parent_id=None):
        return public_record(self.core.goals.create(self.scope, objective, limits=limits, criteria=criteria, parent_id=parent_id))

    def edit_goal(self, id, expected_revision, objective=None, limits=None, criteria=None):
        return public_record(self.core.goals.edit(self.scope, id, expected_revision=expected_revision,
            objective=objective, limits=limits, criteria=criteria))

    def transition_goal(self, id, state, expected_revision, evidence=None, reason=None):
        with self.journal.transaction():
            row = self.core.goals.transition(self.scope, id, state, expected_revision=expected_revision, evidence=evidence, reason=reason)
            if state != "active":
                for work in self.journal.list("work", self.scope):
                    if work.value["goal_id"] == id and work.value["state"] not in TERMINAL:
                        self._cancel(work.id, "goal_" + state)
                for schedule in self.journal.list("schedules", self.scope):
                    if schedule.value["goal_id"] == id and schedule.value["state"] == "active":
                        self.core.scheduler.set_schedule_state(self.scope, schedule.id, "paused", expected_revision=schedule.revision)
                row = self.core.goals.get(self.scope, id)
            return public_record(row)

    def archive_goal(self, id, expected_revision):
        return public_record(self.core.goals.archive(self.scope, id, expected_revision=expected_revision))

    def list_commitments(self):
        return self.core.commitments.list(self.scope)

    def create_commitment(self, description, beneficiary="owner", criteria=None, expires_at=None):
        return public_record(self.core.commitments.create(self.scope, description, beneficiary=beneficiary,
            criteria=criteria, expires_at=expires_at))

    def transition_commitment(self, id, state, expected_revision, evidence=None, reason=None):
        return public_record(self.core.commitments.transition(self.scope, id, state,
            expected_revision=expected_revision, evidence=evidence, reason=reason))

    def commitment_progress(self, id, progress, expected_revision, reason=None):
        return public_record(self.core.commitments.set_progress(self.scope, id, progress,
            expected_revision=expected_revision, reason=reason))

    def wait_commitment(self, id, trigger, expected_revision, after_seq=0, expires_at=None):
        row, wait = self.core.commitments.begin_wait(self.scope, id, trigger,
            registrar=self.core.scheduler.register_wait, after_seq=after_seq,
            expected_revision=expected_revision, expires_at=expires_at)
        return {**public_record(row), "wait": wait}

    def resume_commitment(self, id, expected_revision):
        row = self.core.commitments.get(self.scope, id)
        return public_record(self.core.commitments.resume_wait(self.scope, id,
            wait_id=row.value["wait_id"], expected_revision=expected_revision))

    def _conversation(self, id):
        row = self._row("consumer.conversations", id)
        if row.value["archived"]:
            raise CoreError("conversation_archived", "Automation requires an active conversation")
        return row

    @staticmethod
    def _resources(resources):
        amounts(resources)
        if not resources or resources.get("tokens", 0) <= 0:
            raise CoreError("invalid_budget", "Model work requires an explicit positive token allocation")
        return resources

    def list_schedules(self):
        return self._rows("schedules")

    def create_schedule(self, conversation_id, text, spec, resources, goal_id=None, missed_policy="run_once"):
        self._resources(resources)
        with self.journal.transaction():
            conversation = self._conversation(conversation_id)
            return public_record(self.core.schedule_prompt(conversation.value["binding_id"], text, spec,
                resources=resources, goal_id=goal_id, missed_policy=missed_policy))

    def schedule_state(self, id, state, expected_revision):
        with self.journal.transaction():
            row = self.core.scheduler.set_schedule_state(self.scope, id, state, expected_revision=expected_revision)
            if state != "active":
                for work in self.journal.list("work", self.scope):
                    if work.value["payload"].get("schedule_id") == id and work.value["state"] not in TERMINAL:
                        self._cancel(work.id, "schedule_" + state)
            return public_record(row)

    @staticmethod
    def _steps(steps):
        if not isinstance(steps, list) or not steps or len(steps) > 256:
            raise CoreError("invalid_plan", "A workflow needs a bounded nonempty step list")
        result = []
        for step in steps:
            if not isinstance(step, dict):
                raise CoreError("invalid_plan", "Workflow steps must be objects")
            step = dict(step)
            if not step.get("checks"):
                step["checks"] = [{"id": "execution", "event_kind": "runtime.turn_completed", "source": "runtime",
                    "matches": {"external_outcome": "not_asserted"}}]
            result.append(step)
        return result

    def create_workflow(self, steps, budget, goal_id=None, commitment_id=None):
        self._resources(budget)
        return public_record(self.core.planner.create(self.scope, self._steps(steps), goal_id=goal_id,
            commitment_id=commitment_id, budget=budget))

    def revise_workflow(self, id, steps, expected_revision, reason):
        return public_record(self.core.planner.revise(self.scope, id, self._steps(steps), expected_revision=expected_revision, reason=reason))

    def get_workflow(self, id):
        row = self.core.planner.get(self.scope, identifier(id))
        run = self.journal.get(RUNS, id, self.scope)
        return {**public_record(row), "execution": public_record(run) if run else None,
                "completion_basis": {step["id"]: "model_turn" if step["checks"] == [{"id": "execution",
                    "event_kind": "runtime.turn_completed", "source": "runtime", "matches": {"external_outcome": "not_asserted"}}]
                    else "outcome_checks" for step in row.value["steps"]}}

    def list_workflows(self):
        return [self.get_workflow(row.id) for row in self.journal.list("plans", self.scope)]

    def run_workflow(self, id, conversation_id, expected_revision):
        with self._advance_lock:
            if self._paused:
                raise CoreError("runtime_unavailable", "Workflow admission is paused")
            return self._run_workflow(id, conversation_id, expected_revision)

    def _run_workflow(self, id, conversation_id, expected_revision):
        with self.journal.transaction():
            row = self.core.planner.get(self.scope, identifier(id))
            if row.revision != expected_revision:
                raise CoreError("conflict", "Workflow revision changed")
            if row.value["state"] != "active":
                raise CoreError("invalid_transition", "Only active workflows can execute")
            conversation = self._conversation(conversation_id)
            prior = self.journal.get(RUNS, id, self.scope)
            if prior and prior.value["conversation_id"] != conversation.id:
                raise CoreError("conflict", "A workflow cannot change its execution conversation")
            body = prior.value if prior else {"conversation_id": conversation.id, "attempts": [], "allocated": {}, "error": None}
            self.journal.put(RUNS, id, self.scope, {**body, "state": "running", "error": None}, prior.revision if prior else 0)
            self._advance_workflow(id)
        return self.get_workflow(id)

    def _stop_assignments(self, id, reason):
        run = self.journal.get(RUNS, id, self.scope)
        if run:
            for attempt in run.value["attempts"]:
                self._stop_attempt(attempt, reason)

    def _stop_attempt(self, attempt, reason):
        if attempt.get("schedule_id"):
            schedule = self.journal.get("schedules", attempt["schedule_id"], self.scope)
            if schedule.value["state"] in {"active", "paused"}:
                self.core.scheduler.set_schedule_state(self.scope, schedule.id, "cancelled", expected_revision=schedule.revision)
        for work in self.journal.list("work", self.scope):
            if (work.id == attempt.get("work_id") or attempt.get("schedule_id") and work.value["payload"].get("schedule_id") == attempt["schedule_id"]) and work.value["state"] not in TERMINAL:
                self._cancel(work.id, reason)

    def workflow_state(self, id, state, expected_revision, reason=None):
        with self.journal.transaction():
            self.core.planner.transition(self.scope, id, state, expected_revision=expected_revision, reason=reason)
            if state in {"paused", "abandoned"}:
                self._stop_assignments(id, "workflow_" + state)
            return self.get_workflow(id)

    def workflow_step(self, id, step_id, state, expected_revision, evidence=None, reason=None):
        with self.journal.transaction():
            row = self.core.planner.get(self.scope, id)
            step = next((item for item in row.value["steps"] if item["id"] == step_id), None)
            if step is None:
                raise CoreError("not_found", "Workflow step is unavailable")
            if state == "completed" and step["assignee"]["kind"] != "user":
                raise CoreError("invalid_transition", "Agent step completion is derived from its actual execution")
            if state == "in_progress" and step["assignee"]["kind"] != "user":
                raise CoreError("invalid_transition", "Run the workflow to admit agent steps")
            self.core.planner.transition_step(self.scope, id, step_id, state, expected_revision=expected_revision,
                evidence=evidence, reason=reason)
            run = self.journal.get(RUNS, id, self.scope)
            if run and state in {"blocked", "skipped"}:
                for attempt in run.value["attempts"]:
                    if attempt["step_id"] == step_id:
                        self._stop_attempt(attempt, "workflow_step_" + state)
            return self.get_workflow(id)

    def _evidence(self, work_id):
        evidence, cursor = [], 0
        attempts = [row.value for row in self.journal.list("tool_requests", self.scope) if row.value["work_id"] == work_id]
        action_ids = {row["result"]["action_id"] for row in attempts}
        while page := self.journal.events(self.scope, after=cursor, limit=1000):
            for event in page:
                if (event.source == "runtime" and event.kind.startswith("runtime.turn_") and event.payload.get("work_id") == work_id
                        or event.source == "tool_runner" and event.payload.get("action_id") in action_ids):
                    evidence.append(event.id)
            cursor = page[-1].seq
        return evidence

    def _advance_workflow(self, id):
        run = self.journal.get(RUNS, id, self.scope)
        plan = self.core.planner.get(self.scope, id)
        if not run or run.value["state"] != "running" or plan.value["state"] != "active":
            return
        body = dict(run.value)
        body["attempts"] = [dict(item) for item in body["attempts"]]
        # Resolve scheduler-created work from durable schedule identities after restart.
        for attempt in body["attempts"]:
            if attempt["state"] != "running":
                continue
            if not attempt.get("work_id") and attempt.get("schedule_id"):
                work = next((row for row in self.journal.list("work", self.scope)
                    if row.value["payload"].get("schedule_id") == attempt["schedule_id"]), None)
                if work:
                    attempt["work_id"] = work.id
                else:
                    schedule = self.journal.get("schedules", attempt["schedule_id"], self.scope)
                    if schedule.value["state"] not in {"cancelled", "completed"}:
                        continue
                    attempt["state"] = "failed"
            work = self.core.scheduler.get(self.scope, attempt["work_id"]) if attempt.get("work_id") else None
            if work and work.value["state"] not in TERMINAL:
                continue
            plan = self.core.planner.get(self.scope, id)
            step = next((step for step in plan.value["steps"] if step["id"] == attempt["step_id"]), None)
            if step is None or step["state"] != "in_progress":
                attempt["state"] = "superseded"
                continue
            try:
                if work is None or work.value["state"] != "completed":
                    raise CoreError("work_failed", "Step execution did not complete")
                self.core.planner.transition_step(self.scope, id, step["id"], "completed",
                    expected_revision=plan.revision, evidence=self._evidence(work.id))
                attempt["state"] = "completed"
            except CoreError as exc:
                self.core.planner.transition_step(self.scope, id, step["id"], "blocked",
                    expected_revision=plan.revision, reason=exc.code)
                attempt["state"], body["error"] = "failed", {"code": exc.code, "step_id": step["id"]}
        plan = self.core.planner.get(self.scope, id)
        for step in self.core.planner.ready(self.scope, id):
            if step["assignee"]["kind"] == "user":
                continue
            try:
                attempt, allocated = self._dispatch_step(step, plan, body)
            except CoreError as exc:
                if not body["attempts"]:
                    raise
                body.update(state="blocked", error={"code": exc.code, "step_id": step["id"]})
                break  # Preserve already observed completions when later admission fails.
            plan = self.core.planner.transition_step(self.scope, id, step["id"], "in_progress", expected_revision=plan.revision)
            body["allocated"] = {**body["allocated"], **allocated}
            body["attempts"].append(attempt)
        if all(step["state"] in {"completed", "skipped"} for step in plan.value["steps"]):
            self.core.planner.transition(self.scope, id, "completed", expected_revision=plan.revision)
            body["state"] = "completed"
        self.journal.put(RUNS, id, self.scope, body, run.revision)

    def _dispatch_step(self, step, plan, body):
        with self.journal.transaction():
            allocation = step["budget"] or {key: amount // len(plan.value["steps"]) for key, amount in plan.value["budget"].items()}
            self._resources(allocation)
            allocated = {key: body["allocated"].get(key, 0) + amount for key, amount in allocation.items()}
            if any(amount > plan.value["budget"].get(key, 0) for key, amount in allocated.items()):
                raise CoreError("budget_exceeded", "Cumulative workflow attempt allocations exceed its budget")
            attempt = {"id": new_id("step_run"), "step_id": step["id"], "state": "running", "work_id": None, "schedule_id": None, "allocation": allocation}
            if step["assignee"]["kind"] == "child":
                child = self.core.subagents.get(self.scope, step["assignee"]["id"])
                child_work = self.core.scheduler.get(self.scope, child.value["work_id"])
                if child_work.value["goal_id"] != plan.value["goal_id"] or any(amount > allocation.get(key, 0) for key, amount in child_work.value["resources"].items()):
                    raise CoreError("budget_exceeded", "Existing child allocation is outside the workflow grant")
                attempt["work_id"] = child_work.id
            else:
                scheduled = self.create_schedule(body["conversation_id"], step["description"],
                    {"kind": "once", "at": time.time()}, allocation, goal_id=plan.value["goal_id"])
                attempt["schedule_id"] = scheduled["id"]
            return attempt, allocated

    def list_children(self):
        return self._rows("subagents")

    def get_child(self, id):
        return public_record(self.core.subagents.get(self.scope, identifier(id)))

    def cancel_child(self, id, reason="owner_cancelled"):
        return public_record(self.core.subagents.cancel(self.scope, id, reason=reason))

    async def delegate(self, parent_work_id, objective, capabilities, resources, criteria=None, workspace_mode="isolated", retained=False):
        return public_record(await self.core.delegate(parent_work_id, objective, capabilities=capabilities,
            resources=resources, criteria=criteria, workspace_mode=workspace_mode, retained=retained))

    def propose_initiative(self, source_event_id, action, signals, expires_at, dedupe_key, channel="default"):
        return public_record(self.core.initiative.propose(self.scope, source_event_id, action, signals=signals,
            expires_at=expires_at, dedupe_key=dedupe_key, channel=channel))

    def attention_policy(self, policy, expected_revision=0):
        allowed = {"mode", "quiet_hours", "notification_limit", "minimum_score", "duplicate_seconds", "conversation_id", "resources"}
        if not isinstance(policy, dict) or set(policy) - allowed or not isinstance(policy.get("mode", "suggest"), str) or policy.get("mode", "suggest") not in {"disabled", "remember", "suggest", "bounded"}:
            raise CoreError("invalid_policy", "Unknown attention policy")
        canonical_json(policy)
        if (type(policy.get("notification_limit", 20)) is not int or not 0 <= policy.get("notification_limit", 20) <= 1000
                or type(policy.get("minimum_score", .5)) not in {int, float} or not 0 <= policy.get("minimum_score", .5) <= 1
                or type(policy.get("duplicate_seconds", 3600)) not in {int, float} or not 0 <= policy.get("duplicate_seconds", 3600) <= 86400 * 30):
            raise CoreError("invalid_policy", "Attention limits must be bounded")
        hours = policy.get("quiet_hours")
        if hours is not None:
            if not isinstance(hours, dict) or set(hours) != {"timezone", "start_minute", "end_minute"} or any(type(hours[key]) is not int or not 0 <= hours[key] < 1440 for key in ("start_minute", "end_minute")):
                raise CoreError("invalid_policy", "Quiet hours require valid local minute boundaries")
            try:
                ZoneInfo(hours["timezone"])
            except (ZoneInfoNotFoundError, TypeError, ValueError):
                raise CoreError("invalid_policy", "Quiet hours timezone is unavailable") from None
        if policy.get("mode") == "bounded" or "conversation_id" in policy or "resources" in policy:
            self._conversation(policy.get("conversation_id"))
            self._resources(policy.get("resources"))
        return public_record(self.journal.put(ATTENTION, "policy", self.scope, {"policy": policy}, expected_revision))

    def _candidate(self, candidate, policy):
        decision = self.core.attention.evaluate_initiative(self.scope, candidate.id,
            policy={key: value for key, value in policy.items() if key not in {"conversation_id", "resources"}},
            workload="interactive" if any(row.value["state"] == "running" for row in self.journal.list("work", self.scope)) else "idle")
        self.journal.put(INBOX, candidate.id, self.scope, {"decision": "pending", "attention": public_record(decision), "schedule_id": None})
        if decision.value["eligible"]:
            self.decide_inbox(candidate.id, "accept", policy.get("conversation_id"), policy.get("resources"))

    def inbox(self):
        return [{**public_record(row), "candidate": public_record(self.core.initiative.get(self.scope, row.id))} for row in self.journal.list(INBOX, self.scope)]

    def decide_inbox(self, id, decision, conversation_id=None, resources=None):
        if not isinstance(decision, str) or decision not in {"accept", "dismiss"}:
            raise CoreError("invalid_decision", "Initiative decision must be accept or dismiss")
        with self.journal.transaction():
            row = self._row(INBOX, id)
            if row.value["decision"] != "pending":
                raise CoreError("invalid_transition", "Initiative already has a decision")
            candidate = self.core.initiative.get(self.scope, id)
            body = {**row.value, "decision": "dismissed"}
            if decision == "accept":
                if candidate.value["expires_at"] <= time.time():
                    raise CoreError("candidate_expired", "Initiative candidate has expired")
                action = candidate.value["action"]
                prompt = action if isinstance(action, str) else action.get("text")
                if not isinstance(prompt, str):
                    raise CoreError("invalid_candidate", "This initiative does not contain an executable prompt")
                schedule = self.create_schedule(conversation_id, prompt, {"kind": "once", "at": time.time()}, resources)
                body.update(decision="started", schedule_id=schedule["id"])
            return public_record(self.journal.put(INBOX, id, self.scope, body, row.revision))

    def advance(self):
        with self._advance_lock:
            if not self._paused:
                self._advance()

    def _advance(self):
        for run in self.journal.list(RUNS, self.scope):
            try:
                with self.journal.transaction():
                    self._advance_workflow(run.id)
            except CoreError as exc:
                with self.journal.transaction():
                    current = self.journal.get(RUNS, run.id, self.scope)
                    self.journal.put(RUNS, run.id, self.scope, {**current.value, "state": "blocked", "error": {"code": exc.code}}, current.revision)
        row = self.journal.get(ATTENTION, "policy", self.scope)
        policy = row.value["policy"] if row else {}
        for candidate in self.journal.list("initiative", self.scope):
            if self.journal.get(INBOX, candidate.id, self.scope) is None:
                try:
                    with self.journal.transaction():
                        self._candidate(candidate, policy)
                except CoreError as exc:
                    self.journal.put(INBOX, candidate.id, self.scope, {"decision": "unavailable",
                        "attention": None, "schedule_id": None, "error": {"code": exc.code}})

    async def start(self):
        if self._task is None:
            await self.resume()
            await asyncio.to_thread(self.advance)
            self._task = asyncio.create_task(self._monitor(), name="consumer:workflows")

    async def _monitor(self):
        while True:
            await asyncio.sleep(self.poll_interval)
            await asyncio.to_thread(self.advance)

    async def close(self):
        await self.pause()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    def _set_paused(self, paused):
        with self._advance_lock:
            self._paused = paused

    async def pause(self):
        await asyncio.to_thread(self._set_paused, True)

    async def resume(self):
        await asyncio.to_thread(self._set_paused, False)
