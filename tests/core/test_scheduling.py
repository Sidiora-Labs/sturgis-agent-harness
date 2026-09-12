"""Real journal, tools and native-memory integration for shared admission and children."""

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent.awareness import Awareness
from agent.commitments import CommitmentService
from agent.goals import GoalService
from agent.memory import NativeMemoryClient
from agent.profile import ProfileRegistry
from agent.protocol import CoreError, ToolCall
from agent.scheduler import CONTROL, Scheduler, next_occurrence, validate_schedule
from agent.state import Journal
from agent.subagents import Subagents
from agent.tool_runner import ExecutorResult, ProcessRunner, ToolDispatcher, WorkspaceFs, file_executor, process_executor


@pytest.fixture
def journal(tmp_path):
    value = Journal(tmp_path / "journal.db")
    yield value
    value.close()


@pytest.fixture
def scheduler(journal):
    return Scheduler(journal, clock=lambda: 1000, max_running=10)


def admitted(scheduler, *, scope="s", resources=None, capabilities=(), **options):
    row = scheduler.submit(scope, "model", {}, resources=resources or {}, capabilities=capabilities, **options)
    return scheduler.admit(scope, row.id, eligible_ids=[row.id])


def claim(scheduler, row, identifier="claim", resources=None, capabilities=(), tool="file"):
    grant = {"work_id": row.id, "generation": row.value["generation"], "claim_id": identifier,
             "resources": resources or {}, "required_capabilities": list(capabilities)}
    return scheduler.authorize(row.scope, grant, ToolCall(tool, {}))


def active_goal(scheduler, limits):
    goal = scheduler.goals.create("s", "Observe actual outcome", limits=limits)
    return scheduler.goals.transition("s", goal.id, "active", expected_revision=goal.revision)


def checks():
    return [{"id": "a", "event_kind": "tool.effect", "source": "tool_runner", "matches": {"data.path": "a"}},
            {"id": "b", "event_kind": "tool.effect", "source": "tool_runner", "matches": {"data.path": "b"}}]


def children(journal, scheduler, tmp_path):
    return Subagents(journal, scheduler, workspace_root=tmp_path)


def test_admission_readiness_priority_capacity_global_scope_and_recovery(journal):
    scheduler = Scheduler(journal, clock=lambda: 1000, max_running=1)
    earlier = scheduler.submit("s", "timer", {}, now=800)
    newest = scheduler.submit("s", "model", {}, priority=1)
    other = scheduler.submit("other", "model", {})
    assert scheduler.admit("s", newest.id) is None
    assert scheduler.admit("s", newest.id, eligible_ids=[newest.id]).id == newest.id
    assert scheduler.admit("other") is None
    assert scheduler.heartbeat("s", newest.id, 1).value["lease_until"] == 1030
    with pytest.raises(CoreError):
        scheduler.heartbeat("s", newest.id, 2)
    scheduler.finish("s", newest.id, 1)
    assert scheduler.admit("s").id == earlier.id
    assert scheduler.recover("s", now=1031)[0].value["generation"] == 2
    with pytest.raises(CoreError):
        scheduler.heartbeat("s", earlier.id, 1, now=1031)
    recovered = scheduler.admit("s", earlier.id, now=1031)
    scheduler.finish("s", recovered.id, 2, now=1031)
    assert scheduler.admit("other").id == other.id
    assert scheduler.admit("none") is None


def test_goal_child_grants_cumulative_usage_and_retention(journal, scheduler):
    goal = active_goal(scheduler, {"tokens": 100, "tool_calls": 10})
    parent = admitted(scheduler, resources={"tokens": 100, "tool_calls": 10}, capabilities=["read"], goal_id=goal.id)
    child = scheduler.submit("s", "child", {}, parent_id=parent.id, resources={"tokens": 60, "tool_calls": 3}, capabilities=["read"], retain_after_parent=True)
    child = scheduler.admit("s", child.id)
    assert scheduler.get("s", parent.id).value["child_reserved"]["tokens"] == 60
    authorization = claim(scheduler, child, resources={"tokens": 20, "tool_calls": 1}, capabilities=["read"])
    assert claim(scheduler, child, resources={"tokens": 20, "tool_calls": 1}, capabilities=["read"]) == authorization
    with pytest.raises(CoreError):
        claim(scheduler, child, resources={"tokens": 21})
    with pytest.raises(CoreError):
        claim(scheduler, parent, "parentclaim", resources={"tokens": 41})
    scheduler.record_usage("s", child.id, "actual", {"tokens": 15, "tool_calls": 1}, claim_id="claim")
    assert scheduler.record_usage("s", child.id, "actual", {"tokens": 15, "tool_calls": 1}, claim_id="claim").value["usage"]["tokens"] == 15
    assert scheduler.get("s", parent.id).value["usage"]["tokens"] == 15
    assert scheduler.goals.get("s", goal.id).value["usage"]["tokens"] == 15
    with pytest.raises(CoreError):
        scheduler.record_usage("s", child.id, "actual", {"tokens": 16}, claim_id="claim")
    with pytest.raises(CoreError):
        scheduler.record_usage("s", child.id, "different", {}, claim_id="claim")
    scheduler.finish("s", parent.id, 1)
    assert not scheduler.get("s", parent.id).value["allocation_closed"]
    claim(scheduler, child, "retained", resources={"tokens": 5})
    scheduler.record_usage("s", child.id, "retained-actual", {"tokens": 5}, claim_id="retained")
    scheduler.finish("s", child.id, 1)
    assert scheduler.get("s", parent.id).value["allocation_closed"]
    assert scheduler.goals.get("s", goal.id).value["reserved"]["tokens"] == 0
    with pytest.raises(CoreError):
        scheduler.submit("s", "child", {}, parent_id=parent.id, retain_after_parent=True)


def test_late_usage_overage_and_descendant_cancellation_preserve_claims(journal, scheduler):
    goal = active_goal(scheduler, {"tokens": 10})
    parent = admitted(scheduler, resources={"tokens": 10}, goal_id=goal.id)
    child = scheduler.submit("s", "child", {}, parent_id=parent.id, resources={"tokens": 5})
    child = scheduler.admit("s", child.id)
    grandchild = scheduler.submit("s", "child", {}, parent_id=child.id, resources={"tokens": 3})
    grandchild = scheduler.admit("s", grandchild.id)
    claim(scheduler, grandchild, resources={"tokens": 3})
    assert len(scheduler.cancel("s", parent.id)) == 3
    assert len(scheduler.cancel("s", parent.id)) == 3
    with pytest.raises(CoreError):
        claim(scheduler, grandchild, "denied")
    assert scheduler.goals.get("s", goal.id).value["reserved"]["tokens"] == 10
    scheduler.record_usage("s", grandchild.id, "late", {"tokens": 12}, claim_id="claim")
    assert scheduler.get("s", grandchild.id).value["usage"]["tokens"] == 12
    assert scheduler.get("s", parent.id).value["budget_overrun"]
    assert scheduler.goals.get("s", goal.id).value["usage"]["tokens"] == 12
    assert scheduler.goals.get("s", goal.id).value["reserved"]["tokens"] == 0
    assert scheduler.goals.get("s", goal.id).value["budget_overrun"]


def test_crash_unknown_claim_never_replayed_or_zero_charged(journal, scheduler):
    parent = admitted(scheduler, resources={"tokens": 20})
    child = scheduler.submit("s", "child", {}, parent_id=parent.id)
    claim(scheduler, parent, resources={"tokens": 8})
    changed = scheduler.recover("s", now=1040)
    assert changed[0].value["state"] == "recovery_required"
    assert scheduler.get("s", child.id).value["revoked"]
    assert scheduler.admit("s", parent.id, now=1040) is None
    assert scheduler.get("s", parent.id).value["claim_reserved"]["tokens"] == 8
    scheduler.cancel("s", parent.id)
    scheduler.record_usage("s", parent.id, "late", {"tokens": 5}, claim_id="claim")
    assert scheduler.get("s", parent.id).value["claim_reserved"]["tokens"] == 0


def test_paused_goal_fences_new_execution_but_allows_receipts_and_cleanup(scheduler):
    goal = active_goal(scheduler, {"tokens": 20})
    parent = admitted(scheduler, resources={"tokens": 20}, goal_id=goal.id)
    queued = scheduler.submit("s", "child", {}, parent_id=parent.id, resources={"tokens": 2})
    claim(scheduler, parent, resources={"tokens": 5})
    goal = scheduler.goals.get("s", goal.id)
    scheduler.goals.transition("s", goal.id, "paused", expected_revision=goal.revision)
    with pytest.raises(CoreError, match="goal state"):
        claim(scheduler, parent, "fresh")
    with pytest.raises(CoreError, match="goal state"):
        scheduler.heartbeat("s", parent.id, 1)
    with pytest.raises(CoreError, match="goal state"):
        scheduler.admit("s", queued.id)
    scheduler.record_usage("s", parent.id, "completed-before-pause", {"tokens": 3}, claim_id="claim")
    scheduler.finish("s", parent.id, 1)
    assert scheduler.goals.get("s", goal.id).value["usage"]["tokens"] == 3
    assert scheduler.goals.get("s", goal.id).value["reserved"]["tokens"] == 0
    assert scheduler.get("s", queued.id).value["revoked"]
    other_goal = active_goal(scheduler, {"tokens": 10})
    resumed = admitted(scheduler, resources={"tokens": 10}, goal_id=other_goal.id)
    other_goal = scheduler.goals.get("s", other_goal.id)
    scheduler.goals.transition("s", other_goal.id, "paused", expected_revision=other_goal.revision)
    scheduler.recover("s", now=1040)
    with pytest.raises(CoreError, match="goal state"):
        scheduler.admit("s", resumed.id, now=1040)
    scheduler.cancel("s", resumed.id)
    assert scheduler.goals.get("s", other_goal.id).value["reserved"]["tokens"] == 0


def test_submission_scope_constraints_and_invalid_input(journal, scheduler, tmp_path):
    for kwargs in ({"max_running": 0}, {"lease_seconds": float("nan")}):
        with pytest.raises(CoreError):
            Scheduler(journal, **kwargs)
    other = Journal(tmp_path / "other.db")
    try:
        with pytest.raises(CoreError):
            Scheduler(journal, goals=GoalService(other))
    finally:
        other.close()
    invalid = [dict(capabilities="read"), dict(priority=101), dict(resources={"bad": 1}),
               dict(constraints={"extra": 1}), dict(constraints={"delegated": 1}),
               dict(constraints={"delegated": True}), dict(constraints={"workspace_root": "relative"}),
               dict(constraints={"workspace_root": {}})]
    for kwargs in invalid:
        with pytest.raises(CoreError):
            scheduler.submit("s", "x", {}, **kwargs)
    with pytest.raises(CoreError):
        scheduler.submit(CONTROL, "x", {})
    row = scheduler.submit("s", "model", {}, dedupe_key="once", not_before=1002)
    assert scheduler.submit("s", "model", {}, dedupe_key="once", not_before=1002).id == row.id
    with pytest.raises(CoreError):
        scheduler.submit("s", "model", {}, dedupe_key="once", not_before=1003)
    assert scheduler.admit("s") is None
    for ready in ("x", [None], list(str(i) for i in range(4097))):
        with pytest.raises(CoreError):
            scheduler.admit("s", eligible_ids=ready)
    parent = admitted(scheduler, constraints={"workspace_root": str(tmp_path), "workspace_writable": False}, capabilities=["read"], resources={"tokens": 10})
    for kwargs in (dict(capabilities=["write"]), dict(constraints={"workspace_root": "/tmp"}),
                   dict(constraints={"workspace_root": str(tmp_path), "workspace_writable": True}), dict(goal_id="other")):
        with pytest.raises(CoreError):
            scheduler.submit("s", "child", {}, parent_id=parent.id, **kwargs)
    child = scheduler.submit("s", "child", {}, parent_id=parent.id, resources={"tokens": 11}, constraints={"workspace_root": str(tmp_path), "workspace_writable": False})
    with pytest.raises(CoreError):
        scheduler.admit("s", child.id, eligible_ids=[child.id])
    with pytest.raises(CoreError):
        scheduler.authorize("s", {}, ToolCall("tool", {}))
    with pytest.raises(CoreError):
        claim(scheduler, parent, capabilities=["write"])
    with pytest.raises(CoreError):
        scheduler.finish("s", parent.id, 2)
    with pytest.raises(CoreError):
        scheduler.finish("s", parent.id, 1, state=[])
    profiles = ProfileRegistry(journal)
    profile = profiles.register("disabled", tmp_path)
    profiles.update("disabled", expected_revision=profile.revision, enabled=False)
    with pytest.raises(CoreError):
        scheduler.admit("disabled")


def test_actual_commitment_wait_transaction_restart_and_expiry(journal, scheduler):
    commitments = CommitmentService(journal, cancel_wait=scheduler.cancel_wait)
    commitment = commitments.create("s", "Follow up", beneficiary="user")
    commitment = commitments.transition("s", commitment.id, "accepted", expected_revision=commitment.revision)
    waiting, yielding = commitments.begin_wait("s", commitment.id, {"kind": "time", "at": 1010}, registrar=scheduler.register_wait, after_seq=0, expected_revision=commitment.revision)
    assert yielding["release_compute"]
    restarted = Scheduler(journal, clock=lambda: 1011)
    fired = restarted.fire_waits("s")
    assert fired[0].id == waiting.value["wait_id"]
    assert restarted.fire_waits("s") == []
    assert commitments.resume_wait("s", commitment.id, wait_id=fired[0].id, expected_revision=waiting.revision).value["progress"] == "in_progress"
    restarted.cancel_wait("s", fired[0].id)
    assert restarted.cancel_wait("s", fired[0].id).value["state"] == "cancelled"
    work = admitted(scheduler)
    wait = scheduler.register_wait("s", work.id, {"kind": "time", "at": 2000}, wait_id="expires", after_seq=0, expires_at=1002)
    assert scheduler.register_wait("s", work.id, wait.value["trigger"], wait_id="expires", after_seq=0, expires_at=1002) == wait
    with pytest.raises(CoreError):
        scheduler.register_wait("s", work.id, {"kind": "time", "at": 2001}, wait_id="expires", after_seq=0, expires_at=1002)
    with pytest.raises(CoreError):
        scheduler.park("s", work.id, 2, wait_id="expires")
    scheduler.park("s", work.id, 1, wait_id="expires")
    scheduler.fire_waits("s", now=1003)
    assert journal.get("waits", "expires", "s").value["state"] == "expired"
    assert scheduler.cancel_wait("s", "expires").value["state"] == "expired"
    for kwargs in (dict(after_seq=-1), dict(after_seq=0, expires_at=999)):
        with pytest.raises(CoreError):
            scheduler.register_wait("s", work.id, {"kind": "time", "at": 2000}, wait_id="bad", **kwargs)
    with pytest.raises(CoreError):
        scheduler.register_wait("s", "missing", {"kind": "time", "at": 2000}, wait_id="missing", after_seq=0)


def test_all_scoped_wake_event_families(journal, scheduler):
    work = admitted(scheduler)
    cases = [({"kind": "schedule", "job_id": "j"}, "schedule.due", "scheduler", {"job_id": "j"}),
        ({"kind": "child_terminal", "child_id": "c"}, "child.terminal", "subagents", {"child_id": "c"}),
        ({"kind": "process_exit", "process_id": "p"}, "process.exited", "tool_runner", {"process_id": "p"}),
        ({"kind": "file_changed", "object_id": "f"}, "awareness.observed", "awareness", {"source_type": "file", "object_id": "f"}),
        ({"kind": "repository_changed", "object_id": "r"}, "awareness.observed", "awareness", {"source_type": "repository", "object_id": "r"}),
        ({"kind": "channel_message", "conversation_id": "chat"}, "user.prompt", "channel_core", {"conversation_id": "chat"}),
        ({"kind": "user_response", "conversation_id": "chat"}, "user.steer", "channel_core", {"binding_id": "binding"}),
        ({"kind": "external_condition", "connector": "mail", "matches": {"ready": True}}, "external.condition", "runtime", {"connector": "mail", "ready": True})]
    journal.put("bindings", "binding", "s", {"conversation": "chat"})
    for i, (trigger, kind, source, payload) in enumerate(cases):
        scheduler.register_wait("s", work.id, trigger, wait_id=f"w{i}", after_seq=0)
        journal.append("other", kind, payload, source=source)
        journal.append("s", kind, payload, source="model")
    assert scheduler.fire_waits("s") == []
    for trigger, kind, source, payload in cases:
        journal.append("s", kind, payload, source=source)
    assert len(scheduler.fire_waits("s")) == len(cases)
    assert scheduler.fire_waits("s") == []


def test_durable_park_wake_requeue_and_cancellation(journal, scheduler):
    row = admitted(scheduler)
    scheduler.register_wait("s", row.id, {"kind": "time", "at": 1001}, wait_id="w", after_seq=0)
    scheduler.park("s", row.id, 1, wait_id="w")
    assert scheduler.get("s", row.id).value["state"] == "waiting"
    scheduler.fire_waits("s", now=1001)
    assert scheduler.get("s", row.id).value["state"] == "queued"
    assert scheduler.admit("s", row.id, now=1001).value["generation"] == 1


def test_schedule_catchup_pause_resume_skip_and_bounded_replay(journal, scheduler):
    schedules = []
    for policy in ("skip", "run_once", "replay_bounded"):
        schedules.append(scheduler.schedule("s", {"kind": "interval", "anchor": 1000, "every_seconds": 10}, "maintenance", {}, missed_policy=policy, max_replay=3))
    once = scheduler.schedule("s", {"kind": "once", "at": 990}, "maintenance", {})
    assert len(scheduler.tick("s", now=1045)) == 5
    assert scheduler.tick("s", now=1045) == []
    assert journal.get("schedules", once.id, "s").value["state"] == "completed"
    with pytest.raises(CoreError):
        scheduler.set_schedule_state("s", once.id, "active", expected_revision=2)
    row = journal.get("schedules", schedules[0].id, "s")
    row = scheduler.set_schedule_state("s", row.id, "paused", expected_revision=row.revision)
    assert len(scheduler.tick("s", now=1050)) == 2
    row = scheduler.set_schedule_state("s", row.id, "active", expected_revision=row.revision)
    assert len(scheduler.tick("s", now=1050)) == 1
    scheduler.set_schedule_state("s", row.id, "cancelled", expected_revision=journal.get("schedules", row.id, "s").revision)
    future = scheduler.schedule("s", {"kind": "once", "at": 5000}, "maintenance", {})
    assert journal.get("schedules", future.id, "s").value["next_due"] == 5000
    for kwargs in (dict(missed_policy=[]), dict(max_replay=0)):
        with pytest.raises(CoreError):
            scheduler.schedule("s", {"kind": "once", "at": 1000}, "x", {}, **kwargs)
    with pytest.raises(CoreError):
        scheduler.schedule("s", {"kind": "once", "at": 1000}, "x", [])
    with pytest.raises(CoreError):
        scheduler.set_schedule_state("s", future.id, [], expected_revision=1)


def utc(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()


def test_calendar_dst_gap_fold_and_schedule_validation():
    spec = {"kind": "calendar", "timezone": "Europe/Berlin", "hour": 2, "minute": 30, "weekdays": [6], "fold": "both"}
    validate_schedule(spec)
    assert next_occurrence(spec, utc("2026-03-29T00:00:00")) == utc("2026-04-05T00:30:00")
    assert next_occurrence(spec, utc("2026-10-25T00:00:00")) == utc("2026-10-25T00:30:00")
    assert next_occurrence(spec, utc("2026-10-25T00:30:01")) == utc("2026-10-25T01:30:00")
    assert next_occurrence({**spec, "fold": "second"}, utc("2026-10-25T00:00:00")) == utc("2026-10-25T01:30:00")
    assert next_occurrence({**spec, "fold": "first"}, utc("2026-10-25T00:30:01")) == utc("2026-11-01T01:30:00")
    for bad in (None, {}, {"kind": "once"}, {"kind": "interval", "anchor": 0, "every_seconds": 0},
                {**spec, "weekdays": []}, {**spec, "timezone": "unknown/zone"}, {"kind": "cron"}):
        with pytest.raises(CoreError):
            validate_schedule(bad)


async def test_actual_tool_receipts_child_workspace_and_cancel_evidence(journal, scheduler, tmp_path):
    parent = admitted(scheduler, resources={"tool_calls": 4, "elapsed_ms": 200000}, capabilities=["files"])
    service = children(journal, scheduler, tmp_path)
    child = service.delegate("s", parent.id, "Write child output", capabilities=["files"], resources={"tool_calls": 2, "elapsed_ms": 100000})
    work = scheduler.admit("s", child.value["work_id"])
    dispatcher = ToolDispatcher(journal, authorize=scheduler.authorize)
    dispatcher.register("file", file_executor(WorkspaceFs(tmp_path)), input_schema={"type": "object"}, output_schema={"type": "object"}, required_capabilities=["files"])
    result = await dispatcher.invoke(ToolCall("file", {"operation": "write", "path": "child.txt", "content": "hello"}), scope="s", work_id=work.id, generation=1, request_id="write")
    assert result.outcome == "success"
    assert (Path(child.value["workspace_root"]) / "child.txt").read_text() == "hello"
    assert not (tmp_path / "child.txt").exists()
    assert len(scheduler.reconcile_usage("s")) == 1
    assert scheduler.reconcile_usage("s") == []
    assert scheduler.get("s", parent.id).value["usage"]["tool_calls"] == 1
    replay = await dispatcher.invoke(ToolCall("file", {"operation": "write", "path": "child.txt", "content": "hello"}), scope="s", work_id=work.id, generation=1, request_id="write")
    assert replay == result
    scheduler.cancel("s", parent.id)
    denied = await dispatcher.invoke(ToolCall("file", {"operation": "write", "path": "late.txt", "content": "no"}), scope="s", work_id=work.id, generation=1)
    assert denied.state == "rejected"
    assert not (Path(child.value["workspace_root"]) / "late.txt").exists()
    assert all(journal.events("s", kinds=["tool.effect"]))


def test_child_evidence_partial_rejected_accepted_and_idempotent_submission(journal, scheduler, tmp_path):
    parent = admitted(scheduler)
    service = children(journal, scheduler, tmp_path)
    stale = journal.append("s", "tool.effect", {"data": {"path": "a"}}, source="tool_runner")
    child = service.delegate("s", parent.id, "Two outputs", capabilities=[], resources={}, criteria=checks())
    scheduler.admit("s", child.value["work_id"])
    rejected = service.submit_result("s", child.id, evidence_event_ids=[stale.id], summary="Done", submission_id="stale")
    assert rejected.value["result"]["verdict"] == "rejected"
    rejected = service.submit_result("s", child.id, evidence_event_ids=["missing"], summary="Done", submission_id="missing")
    assert rejected.value["result"]["verdict"] == "rejected"
    a = journal.append("s", "tool.effect", {"data": {"path": "a"}}, source="tool_runner")
    partial = service.submit_result("s", child.id, evidence_event_ids=[a.id], summary="A complete", submission_id="partial")
    assert partial.value["result"]["verdict"] == "partial"
    assert service.submit_result("s", child.id, evidence_event_ids=[a.id], summary="A complete", submission_id="partial") == partial
    with pytest.raises(CoreError):
        service.submit_result("s", child.id, evidence_event_ids=[a.id], summary="Changed", submission_id="partial")
    b = journal.append("s", "tool.effect", {"data": {"path": "b"}}, source="tool_runner")
    accepted = service.submit_result("s", child.id, evidence_event_ids=[a.id, b.id], summary="Both complete", submission_id="accepted")
    assert accepted.value["state"] == "completed"
    assert service.archive("s", child.id).value["archived"]
    assert service.archive("s", child.id).value["archived"]
    assert service.cancel("s", child.id).value["state"] == "completed"


def test_child_messages_wait_cycles_resume_and_expiry(journal, scheduler, tmp_path):
    service = children(journal, scheduler, tmp_path)
    parent = admitted(scheduler)
    one = service.delegate("s", parent.id, "One", capabilities=[], resources={}, expires_at=1020)
    two = service.delegate("s", parent.id, "Two", capabilities=[], resources={})
    scheduler.admit("s", one.value["work_id"])
    scheduler.admit("s", two.value["work_id"])
    first = service.message("s", one.id, parent.id, "Check input")
    service.message("s", one.id, one.value["work_id"], "Observed input")
    assert len(service.messages("s", one.id)) == 2
    assert len(service.messages("s", one.id, after=first.value["seq"])) == 1
    with pytest.raises(CoreError):
        service.messages("s", one.id, after=-1)
    with pytest.raises(CoreError):
        service.message("s", one.id, two.value["work_id"], "unauthorized")
    assert service.heartbeat("s", one.id, now=1002).value["last_heartbeat"] == 1002
    service.wait_for("s", one.id, two.id)
    assert service.heartbeat("s", one.id).value["last_heartbeat"] == 1000
    with pytest.raises(CoreError):
        service.wait_for("s", two.id, one.id)
    with pytest.raises(CoreError):
        service.resume("s", one.id)
    service.cancel("s", two.id)
    scheduler.fire_waits("s")
    assert service.resume("s", one.id).value["wait_id"] is None
    scheduler.admit("s", one.value["work_id"], eligible_ids=[one.value["work_id"]])
    with pytest.raises(CoreError):
        service.archive("s", one.id)
    assert service.recover("s", now=1021)[0].value["reason"] == "expired"
    with pytest.raises(CoreError):
        service.heartbeat("s", one.id)
    assert service.recover("s", now=1022) == []


def test_child_constraints_depth_liveness_and_retained_identity(journal, scheduler, tmp_path):
    service = Subagents(journal, scheduler, workspace_root=tmp_path, max_depth=1)
    parent = admitted(scheduler, capabilities=["memory.read"])
    for kwargs in (dict(workspace_mode="bad"), dict(expires_at=999), dict(memory_scout=True)):
        with pytest.raises(CoreError):
            service.delegate("s", parent.id, "Invalid", capabilities=[], resources={}, **kwargs)
    shared = service.delegate("s", parent.id, "Shared", capabilities=[], resources={}, workspace_mode="shared", retained=True)
    scheduler.admit("s", shared.value["work_id"])
    assert shared.value["workspace_root"] == str(tmp_path)
    with pytest.raises(CoreError):
        service.delegate("s", shared.value["work_id"], "Too deep", capabilities=[], resources={})
    scheduler.finish("s", parent.id, 1)
    assert service.heartbeat("s", shared.id).value["state"] == "active"
    scheduler.cancel("s", parent.id)
    assert service.recover("s")[0].value["state"] == "cancelled"
    with pytest.raises(CoreError):
        Subagents(journal, scheduler, workspace_root=tmp_path, max_depth=0)
    file = tmp_path / "not_directory"
    file.write_text("x")
    with pytest.raises(CoreError):
        Subagents(journal, scheduler, workspace_root=file)


async def test_memory_scout_uses_real_native_reads_and_scope_fence(journal, scheduler, tmp_path):
    memory = NativeMemoryClient(tmp_path / "memory.db")
    assert await memory.start()
    try:
        record = {"id": "memory", "kind": "experience", "content": "Scoped compiler fact", "sources": [{"id": "source", "revision": 1, "start": 0, "end": 10}], "source_time": 100.0, "observed_at": 100.0, "valid_from": 100.0, "valid_to": None, "concepts": ["compiler"]}
        assert (await memory.ingest("s", record, operation_id="seed"))["status"] == "ok"
        assert (await memory.ingest("other", {**record, "id": "secret"}, operation_id="seed"))["status"] == "ok"
        service = Subagents(journal, scheduler, workspace_root=tmp_path, memory=memory)
        parent = admitted(scheduler, resources={"tool_calls": 4, "elapsed_ms": 30000}, capabilities=["memory.read"])
        child = service.delegate("s", parent.id, "Retrieve", capabilities=["memory.read"], resources={"tool_calls": 2, "elapsed_ms": 12000}, memory_scout=True)
        work = scheduler.admit("s", child.value["work_id"])
        response = await service.scout("s", child.id, text="compiler", at=200)
        assert [item["record"]["id"] for item in response["data"]["records"]] == ["memory"]
        assert scheduler.get("s", parent.id).value["usage"]["tool_calls"] == 1
        assert not work.value["constraints"]["workspace_writable"]
        with pytest.raises(CoreError):
            await service.scout("s", child.id, operation="ingest")
        with pytest.raises(CoreError):
            claim(scheduler, work, "bad", tool="file", capabilities=["memory.read"])
        normal = service.delegate("s", parent.id, "Normal", capabilities=[], resources={})
        scheduler.admit("s", normal.value["work_id"])
        with pytest.raises(CoreError):
            await service.scout("s", normal.id)
        with pytest.raises(CoreError):
            await Subagents(journal, scheduler, workspace_root=tmp_path).scout("s", child.id)
    finally:
        await memory.close()


async def test_process_exit_wait_uses_actual_executor_receipt_not_process_shaped_data(journal, scheduler, tmp_path):
    work = admitted(scheduler, resources={"tool_calls": 3, "elapsed_ms": 90000}, capabilities=["process"])
    dispatcher = ToolDispatcher(journal, authorize=scheduler.authorize)
    dispatcher.register("process", process_executor(ProcessRunner(tmp_path, [sys.executable])), input_schema={"type": "object"}, output_schema={"type": "object"}, required_capabilities=["process"])
    result = await dispatcher.invoke(ToolCall("process", {"argv": [sys.executable, "-c", "print('actual process')"]}), scope="s", work_id=work.id, generation=1)
    assert result.data["exit_code"] == 0
    wait = scheduler.register_wait("s", work.id, {"kind": "process_exit", "process_id": result.attempt_id}, wait_id="process-wake", after_seq=0)
    assert scheduler.fire_waits("s")[0].id == wait.id
    async def shaped(arguments, context):
        return ExecutorResult({"exit_code": 0})
    dispatcher.register("shaped", shaped, input_schema={"type": "object"}, output_schema={"type": "object"})
    false = await dispatcher.invoke(ToolCall("shaped", {}), scope="s", work_id=work.id, generation=1)
    scheduler.register_wait("s", work.id, {"kind": "process_exit", "process_id": false.attempt_id}, wait_id="false-process-wake", after_seq=0)
    assert scheduler.fire_waits("s") == []
