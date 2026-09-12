"""Actual scheduler, filesystem effects, approval rollback and workflow execution."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import threading
import time

import pytest

from agent.protocol import CoreError, ToolCall, canonical_json
from agent.runtime import CoreRuntime
from agent.tool_runner import WorkspaceFs, file_executor
from consumer.connections import ConnectionService
from consumer.network import NetworkPolicy
from consumer.permissions import Permissions, DECISIONS
from consumer.vault import CredentialVault
from consumer.work import WorkViews, RUNS, INBOX
from tests.consumer.test_connections import wire, ready
from tests.consumer.test_identity import error
from tests.integration.test_continuity import harness, proposal, eventually


def register_files(core, path):
    executor = file_executor(WorkspaceFs(path))
    core.tools.register("workspace.write", executor, input_schema={"type": "object"},
        output_schema={"type": "object"}, required_capabilities=["workspace.write"])
    core.tools.register("workspace.read", executor, input_schema={"type": "object"},
        output_schema={"type": "object"}, required_capabilities=["workspace.read"], read_only=True)


def conversation(core, binding=None):
    if binding is None:
        binding = core.channels.bind(core.scope, "consumer", "owner", "conversation", "owner").id
    return core.journal.put("consumer.conversations", "conversation", core.scope,
        {"binding_id": binding, "archived": False, "title": "Workflow"}).id


@pytest.fixture
async def core(tmp_path):
    path = tmp_path / "workspace"
    path.mkdir()
    runtime = CoreRuntime(tmp_path / "runtime", scope="work-agent", workspace=path)
    register_files(runtime, path)
    conversation(runtime)
    yield runtime
    await runtime.close()


def admitted(core, *, caps=None, resources=None):
    row = core.scheduler.submit(core.scope, "manual-test", {}, capabilities=core.capabilities if caps is None else caps,
        resources=resources or {"tokens": 100000, "tool_calls": 10, "elapsed_ms": 600000})
    return core.scheduler.admit(core.scope, row.id, eligible_ids=[row.id])


def write_call(path="result.txt", content="actual"):
    return ToolCall("workspace.write", {"operation": "write", "path": path, "content": content})


async def invoke(core, work, call=None, request_id=None):
    return await core.tools.invoke(call or write_call(), scope=core.scope, work_id=work.id,
        generation=work.value["generation"], request_id=request_id)


async def test_exact_approval_rollback_no_automatic_effect_and_replay(core, tmp_path):
    permissions = Permissions(core)
    permissions.install()
    permissions.install()  # Idempotent installation does not add another authority wrapper.
    work = admitted(core)
    rejected = await invoke(core, work)
    assert rejected.state == "rejected" and rejected.error["code"] == "approval_required"
    assert not (tmp_path / "workspace/result.txt").exists()
    assert not core.journal.list("work_claims", core.scope)
    assert not core.journal.list("tool_requests", core.scope)
    assert not [e for e in core.journal.events(core.scope) if e.kind == "tool.request"]
    pending = permissions.pending()
    assert len(pending) == 1 and pending[0]["call"] == write_call().as_dict()
    request = pending[0]
    assert len(canonical_json(rejected.error).encode()) < 4096
    approved = permissions.approve(request["id"])
    assert permissions.approve(request["id"], expires_in=600) == approved
    assert not (tmp_path / "workspace/result.txt").exists()
    completed = await invoke(core, work, request_id="approved-invocation")
    assert completed.state == "completed" and completed.outcome == "success"
    assert (tmp_path / "workspace/result.txt").read_text() == "actual"
    assert permissions.request(request["id"])["state"] == "consumed"
    replay = await invoke(core, work, request_id="approved-invocation")
    assert replay == completed
    assert len(core.journal.list("work_claims", core.scope)) == 1
    again = await invoke(core, work)
    assert again.state == "rejected" and again.error["code"] == "approval_required"
    assert len(permissions.pending()) == 1
    read = await invoke(core, work, ToolCall("workspace.read", {"operation": "read", "path": "result.txt"}))
    assert read.data["content"] == "actual"


async def test_approval_is_one_use_under_concurrency_and_exact_arguments(core, tmp_path):
    permissions = Permissions(core)
    permissions.install()
    work = admitted(core)
    await invoke(core, work)
    request = permissions.pending()[0]
    permissions.approve(request["id"])
    changed = await invoke(core, work, write_call("different.txt"))
    assert changed.state == "rejected" and not (tmp_path / "workspace/different.txt").exists()
    results = await asyncio.gather(invoke(core, work), invoke(core, work))
    assert sorted(result.state for result in results) == ["completed", "rejected"]
    consumed = core.journal.list(DECISIONS, core.scope)
    assert len(consumed) == 1 and consumed[0].value["state"] == "consumed"
    assert len([event for event in core.journal.events(core.scope) if event.kind == "tool.effect"]) == 1


async def test_child_approval_cannot_expand_into_parent_workspace(core, tmp_path):
    permissions = Permissions(core)
    permissions.install()
    parent = admitted(core)
    child = core.subagents.delegate(core.scope, parent.id, "Scoped write", capabilities=["workspace.write"],
        resources={"tool_calls": 2, "elapsed_ms": 60000})
    child_work = core.scheduler.admit(core.scope, child.value["work_id"], eligible_ids=[child.value["work_id"]])
    rejected = await invoke(core, child_work)
    assert rejected.error["code"] == "approval_required"
    request = permissions.pending()[0]
    assert request["delegated"]
    permissions.approve(request["id"])
    parent_result = await invoke(core, parent)
    assert parent_result.error["code"] == "approval_required"
    assert not (tmp_path / "workspace/result.txt").exists()
    child_result = await invoke(core, child_work)
    assert child_result.outcome == "success"
    from pathlib import Path
    assert (Path(child.value["workspace_root"]) / "result.txt").read_text() == "actual"
    assert WorkViews(core).cancel_work(parent.id)["work_ids"]
    assert core.subagents.get(core.scope, child.id).value["state"] == "cancelled"


async def test_policy_revocation_fences_work_children_and_schedules(core):
    permissions = Permissions(core)
    permissions.install()
    policy = permissions.set("workspace.write", "allow")
    parent = admitted(core)
    child = core.subagents.delegate(core.scope, parent.id, "child", capabilities=["workspace.write"],
        resources={"tool_calls": 1, "elapsed_ms": 30000})
    scheduled = WorkViews(core).create_schedule("conversation", "later", {"kind": "once", "at": time.time() + 60},
        {"tokens": 100000, "tool_calls": 2, "elapsed_ms": 60000})
    # A queued runtime turn is durably marked for cancellation as well as fenced.
    core.journal.put("runtime_turns", parent.id, core.scope, {"state": "running", "cancel_requested": False})
    restricted = permissions.set("workspace.write", "deny", policy["revision"])
    assert parent.id in restricted["fenced"]["work_ids"]
    assert child.value["work_id"] in restricted["fenced"]["work_ids"]
    assert scheduled["id"] in restricted["fenced"]["schedule_ids"]
    assert core.journal.get("runtime_turns", parent.id, core.scope).value["cancel_requested"]
    assert "workspace.write" not in core.capabilities
    stale = await invoke(core, parent)
    assert stale.error["code"] == "permission_denied"
    with error("conflict"):
        permissions.set("workspace.write", "ask", 1)
    with error("forbidden"):
        permissions.set("workspace.write", "allow", 2, reviewer="model")
    with error("invalid_policy"):
        permissions.set("workspace.write", [])
    with error("invalid_tool"):
        permissions.set("bad tool", "deny")
    with error("tool_unavailable"):
        permissions.set("unknown", "deny")
    # The synthetic queued-view row is removed; no model execution was started.
    row = core.journal.get("runtime_turns", parent.id, core.scope)
    core.journal.delete(row.namespace, row.id, core.scope, row.revision)


async def test_decision_expiry_denial_source_checks_and_scope(core):
    permissions = Permissions(core)
    permissions.install()
    work = admitted(core)
    await invoke(core, work)
    request = permissions.pending()[0]
    for expiry in (0, 3601, True):
        with error("invalid_approval"):
            permissions.approve(request["id"], expiry)
    with error("forbidden"):
        permissions.approve(request["id"], reviewer="model")
    approved = permissions.approve(request["id"])
    row = core.journal.get(DECISIONS, request["id"], core.scope)
    core.journal.put(DECISIONS, row.id, core.scope, {**row.value, "expires_at": 0}, row.revision)
    assert permissions.request(request["id"])["state"] == "expired"
    with error("invalid_approval"):
        permissions.approve(request["id"])
    await invoke(core, work, write_call("second"))
    next_request = next(item for item in permissions.pending() if item["id"] != request["id"])
    assert permissions.deny(next_request["id"])["state"] == "denied"
    with error("invalid_approval"):
        permissions.deny(next_request["id"])
    with error("invalid_approval"):
        permissions.request("missing")
    original = core.journal.event(core.scope, request["id"])
    forged = core.journal.append(core.scope, "tool.result", original.payload, source="model")
    with error("invalid_approval"):
        permissions.request(forged.id)
    other = core.journal.append("outside", "tool.result", original.payload, source="tool_runner")
    with error("invalid_approval"):
        permissions.request(other.id)
    incomplete = core.journal.append(core.scope, "tool.result", {"state": "rejected", "error": {"code": "approval_required"}}, source="tool_runner")
    with error("invalid_approval"):
        permissions.request(incomplete.id)
    with error("scope_denied"):
        core.tools.authorize("outside", {}, write_call())


async def test_large_approval_bound_and_changed_tool_descriptor(core):
    permissions = Permissions(core)
    permissions.install()
    work = admitted(core)
    result = await invoke(core, work, write_call(content="x" * 262144))
    assert result.error["code"] == "approval_too_large" and not core.journal.list("work_claims", core.scope)
    await invoke(core, work)
    request = permissions.pending()[0]
    permissions.approve(request["id"])
    core.tools.registry.pop(("workspace.write", "1"))
    assert permissions.request(request["id"])["state"] == "stale"
    # Reinstalling a changed schema at the same version also invalidates a grant.
    workspace = core.profiles.get(core.scope).value["workspace_root"]
    core.tools.register("workspace.write", file_executor(WorkspaceFs(workspace)), input_schema={"type": "object", "required": ["path"]},
        output_schema={"type": "object"}, required_capabilities=["workspace.write"])
    assert permissions.request(request["id"])["state"] == "stale"
    assert (await invoke(core, work)).error["code"] == "approval_required"


async def test_connection_revision_invalidates_permission_and_approval(core, tmp_path):
    async with wire() as (url, requests):
        vault = CredentialVault(tmp_path / "vault")
        service = ConnectionService(core, vault, NetworkPolicy(private_origins=[url]))
        connection = await ready(service, url)
        permissions = Permissions(core)
        permissions.install()
        work = admitted(core)
        call = ToolCall("connection.remote.fetch", {}, tool_version=str(connection["revision"]))
        rejected = await invoke(core, work, call)
        assert rejected.error["code"] == "approval_required"
        request = permissions.pending()[0]
        permissions.approve(request["id"])
        policy = permissions.set("connection.remote.fetch", "allow")
        assert permissions.request(request["id"])["state"] == "stale"
        assert (await invoke(core, work, call)).state == "completed"
        changed = service.grant("remote", ["fetch"], expected_revision=connection["revision"])
        changed = await service.probe("remote", expected_revision=changed["revision"])
        assert next(item for item in permissions.list() if item["id"] == "connection.remote.fetch")["stale"]
        permissions.refresh_capabilities()
        assert "connection:remote:fetch" not in core.capabilities
        fresh = ToolCall("connection.remote.fetch", {}, tool_version=str(changed["revision"]))
        assert (await invoke(core, work, fresh)).error["code"] == "permission_denied"
        permissions.set("connection.remote.fetch", "ask", policy["revision"])
        assert "connection:remote:fetch" in core.capabilities
        # Probe, actual fetch and new probe are the only HTTP requests.
        assert len(requests) == 3


async def test_goal_commitment_and_schedule_owner_projections(core):
    view = WorkViews(core)
    check = {"id": "read", "event_kind": "file.observed", "source": "workspace", "matches": {"sha256": hashlib.sha256(b"data").hexdigest()}}
    goal = view.create_goal("Verify output", limits={"tokens": 1000}, criteria=[check])
    goal = view.edit_goal(goal["id"], goal["revision"], objective="Verify exact output", limits={"tokens": 2000})
    goal = view.transition_goal(goal["id"], "active", goal["revision"])
    work = core.scheduler.submit(core.scope, "goal-work", {}, resources={"tokens": 100}, goal_id=goal["id"])
    core.scheduler.admit(core.scope, work.id)
    schedule = view.create_schedule("conversation", "work", {"kind": "once", "at": time.time() + 60}, {"tokens": 100}, goal_id=goal["id"])
    current = core.goals.get(core.scope, goal["id"])
    paused = view.transition_goal(goal["id"], "paused", current.revision)
    assert view.get_work(work.id)["state"] == "cancelled"
    assert next(item for item in view.list_schedules() if item["id"] == schedule["id"])["state"] == "paused"
    goal = view.transition_goal(goal["id"], "active", paused["revision"])
    with error("missing_evidence"):
        view.transition_goal(goal["id"], "achieved", goal["revision"])
    # This is an actual filesystem observation, recorded as scoped operational evidence.
    path = core.profiles.get(core.scope).value["workspace_root"]
    fs = WorkspaceFs(path)
    fs.write("proof.txt", b"data")
    observed = core.journal.append(core.scope, "file.observed", {"sha256": hashlib.sha256(fs.read("proof.txt")).hexdigest()}, source="workspace")
    goal = view.transition_goal(goal["id"], "achieved", goal["revision"], evidence=[observed.id])
    assert view.archive_goal(goal["id"], goal["revision"])["archived"]
    responsibility = view.create_commitment("Read the evidence", criteria=[check], expires_at=time.time() + 60)
    responsibility = view.transition_commitment(responsibility["id"], "accepted", responsibility["revision"])
    responsibility = view.commitment_progress(responsibility["id"], "blocked", responsibility["revision"], reason="Awaiting owner")
    responsibility = view.wait_commitment(responsibility["id"], {"kind": "time", "at": time.time()}, responsibility["revision"])
    assert responsibility["wait"]["release_compute"]
    core.scheduler.fire_waits(core.scope)
    responsibility = view.resume_commitment(responsibility["id"], responsibility["revision"])
    responsibility = view.transition_commitment(responsibility["id"], "fulfilled", responsibility["revision"], evidence=[observed.id])
    assert responsibility["evidence"]["read"] == [observed.id]
    assert view.list_commitments()[0]["state"] == "fulfilled"
    assert view.overview()["goals"][0]["archived"]


async def test_schedule_and_work_cancellation_validation(core):
    view = WorkViews(core)
    with error("not_found"):
        view.get_work("absent")
    with error("invalid_budget"):
        view.create_schedule("conversation", "text", {"kind": "once", "at": time.time()}, {})
    row = view.create_schedule("conversation", "later", {"kind": "interval", "anchor": time.time(), "every_seconds": 60}, {"tokens": 1000})
    core.scheduler.tick(core.scope, now=row["next_due"] + .1)
    due = next(item for item in view.list_work() if item["payload"].get("schedule_id") == row["id"])
    current = core.journal.get("schedules", row["id"], core.scope)
    assert view.schedule_state(row["id"], "paused", current.revision)["state"] == "paused"
    assert view.get_work(due["id"])["state"] == "cancelled"
    assert view.cancel_work(due["id"])["work_ids"] == [due["id"]]
    room = core.journal.get("consumer.conversations", "conversation", core.scope)
    core.journal.put(room.namespace, room.id, core.scope, {**room.value, "archived": True}, room.revision)
    with error("conversation_archived"):
        view.create_schedule("conversation", "text", {"kind": "once", "at": time.time()}, {"tokens": 1})
    with error("invalid_configuration"):
        WorkViews(core, poll_interval=0)


@asynccontextmanager
async def workflow_harness(tmp_path, scripts):
    async with harness(tmp_path, scripts) as (runtime, binding, model, received):
        register_files(runtime, tmp_path / "workspace")
        conversation(runtime, binding)
        permissions = Permissions(runtime)
        permissions.install()
        permissions.set("workspace.write", "allow")
        view = WorkViews(runtime, poll_interval=.02)
        await runtime.start()
        await view.start()
        try:
            yield runtime, view, model, received
        finally:
            await view.close()


BUDGET = {"tokens": 200000, "tool_calls": 10, "elapsed_ms": 600000, "iterations": 16}


async def test_workflow_executes_real_dependencies_and_scoped_tool_checks(tmp_path):
    scripts = [[proposal("workspace.write", {"operation": "write", "path": "first.txt", "content": "proof"})],
               ["File observed"], ["Second step executed"]]
    async with workflow_harness(tmp_path, scripts) as (core, view, model, received):
        goal = view.create_goal("Independent outcome remains unclaimed")
        plan = view.create_workflow([
            {"id": "write", "description": "Write first.txt", "checks": [{"id": "file", "event_kind": "tool.effect", "source": "tool_runner", "matches": {"data.path": "first.txt"}}]},
            {"id": "report", "description": "Report the observed file", "dependencies": ["write"]}], BUDGET)
        started = view.run_workflow(plan["id"], "conversation", plan["revision"])
        assert len(started["execution"]["attempts"]) == 1
        final = await eventually(lambda: view.get_workflow(plan["id"]) if view.get_workflow(plan["id"])["state"] == "completed" else None, timeout=10)
        assert (tmp_path / "workspace/first.txt").read_text() == "proof"
        assert len(model.requests) == 3 and len(final["execution"]["attempts"]) == 2
        assert final["completion_basis"] == {"write": "outcome_checks", "report": "model_turn"}
        assert final["steps"][0]["evidence"]["file"]
        assert view.list_goals()[0]["state"] == "proposed"
        first, second = final["execution"]["attempts"]
        assert core.scheduler.get(core.scope, first["work_id"]).value["created_at"] <= core.scheduler.get(core.scope, second["work_id"]).value["created_at"]
        assert all(item["state"] == "completed" for item in final["steps"])
        assert received


async def test_workflow_narrative_does_not_satisfy_outcome_checks_and_restart_dedup(tmp_path):
    async with workflow_harness(tmp_path, [["I have created proof.txt"]]) as (core, view, model, _):
        plan = view.create_workflow([{"id": "write", "description": "Create proof.txt", "checks": [
            {"id": "proof", "event_kind": "tool.effect", "source": "tool_runner", "matches": {"data.path": "proof.txt"}}]}], BUDGET)
        view.run_workflow(plan["id"], "conversation", 1)
        blocked = await eventually(lambda: view.get_workflow(plan["id"]) if view.get_workflow(plan["id"])["steps"][0]["state"] == "blocked" else None)
        assert blocked["execution"]["error"]["code"] == "check_failed"
        assert not (tmp_path / "workspace/proof.txt").exists()
        await view.close()
        restarted = WorkViews(core)
        await restarted.start()
        try:
            restarted.advance()
            assert len(restarted.get_workflow(plan["id"])["execution"]["attempts"]) == 1
            assert len(model.requests) == 1
        finally:
            await restarted.close()


async def test_later_budget_failure_preserves_prior_evidence_and_can_be_revised(tmp_path):
    async with workflow_harness(tmp_path, [["First complete"], ["Second complete"]]) as (core, view, model, _):
        steps = [{"id": "first", "description": "First", "budget": {"tokens": 100000, "iterations": 1, "elapsed_ms": 300000}},
                 {"id": "second", "description": "Second", "dependencies": ["first"], "budget": {"tokens": 150000, "iterations": 1, "elapsed_ms": 300000}}]
        plan = view.create_workflow(steps, {"tokens": 200000, "iterations": 2, "elapsed_ms": 600000})
        view.run_workflow(plan["id"], "conversation", plan["revision"])
        blocked = await eventually(lambda: view.get_workflow(plan["id"]) if view.get_workflow(plan["id"])["execution"]["state"] == "blocked" else None)
        assert blocked["steps"][0]["state"] == "completed"
        assert blocked["steps"][0]["evidence"] and blocked["execution"]["allocated"]["tokens"] == 100000
        assert len(model.requests) == 1
        steps[1]["budget"]["tokens"] = 100000
        revised = view.revise_workflow(plan["id"], steps, blocked["revision"], "Fit remaining allocation")
        view.run_workflow(plan["id"], "conversation", revised["revision"])
        final = await eventually(lambda: view.get_workflow(plan["id"]) if view.get_workflow(plan["id"])["state"] == "completed" else None)
        assert len(model.requests) == 2 and final["execution"]["allocated"]["tokens"] == 200000


async def test_consumer_child_delegation_has_real_isolated_effect_and_result(tmp_path):
    scripts = [[proposal("workspace.write", {"operation": "write", "path": "child.txt", "content": "child proof"})], ["Observed"]]
    async with workflow_harness(tmp_path, scripts) as (core, view, model, _):
        parent = admitted(core, resources=BUDGET)
        checks = [{"id": "child-file", "event_kind": "tool.effect", "source": "tool_runner", "matches": {"data.path": "child.txt"}}]
        child = await view.delegate(parent.id, "Write isolated child.txt", ["workspace.write"],
            {"tokens": 100000, "tool_calls": 2, "elapsed_ms": 60000, "iterations": 8}, criteria=checks)
        result = await eventually(lambda: view.get_child(child["id"]) if view.get_child(child["id"])["state"] == "completed" else None)
        assert result["result"]["verdict"] == "accepted"
        from pathlib import Path
        assert (Path(result["workspace_root"]) / "child.txt").read_text() == "child proof"
        assert not (tmp_path / "workspace/child.txt").exists()
        too_small = view.create_workflow([{"id": "child", "description": "Use result", "assignee": {"kind": "child", "id": child["id"]}}], {"tokens": 1})
        with error("budget_exceeded"):
            view.run_workflow(too_small["id"], "conversation", 1)
        plan = view.create_workflow([{"id": "child", "description": "Use observed child result",
            "assignee": {"kind": "child", "id": child["id"]}, "checks": checks}], BUDGET)
        view.run_workflow(plan["id"], "conversation", 1)
        await eventually(lambda: view.get_workflow(plan["id"])["state"] == "completed")
        assert len(model.requests) == 2


async def test_pause_fences_timer_work_before_assignment_projection(core):
    view = WorkViews(core)
    plan = view.create_workflow([{"id": "first", "description": "Stop before execution"}], BUDGET)
    started = view.run_workflow(plan["id"], "conversation", 1)
    assert started["execution"]["attempts"][0]["work_id"] is None
    core._tick()  # The actual runtime consumes the due schedule and creates its turn.
    work = next(row for row in core.journal.list("work", core.scope) if row.value["kind"] == "scheduled_prompt")
    view.workflow_state(plan["id"], "paused", started["revision"])
    assert core.scheduler.get(core.scope, work.id).value["state"] == "cancelled"
    assert core.journal.get("runtime_turns", work.id, core.scope).value["cancel_requested"]
    # Teardown remains a real unstarted runtime; cancelled view rows need no inference.


async def test_suspend_waits_for_actual_inflight_journal_projection(core):
    view = WorkViews(core)
    entered, release = threading.Event(), threading.Event()
    def block_journal():
        with core.journal.transaction():
            entered.set()
            assert release.wait(3)
    blocker = asyncio.create_task(asyncio.to_thread(block_journal))
    assert await asyncio.to_thread(entered.wait, 2)
    advance = asyncio.create_task(asyncio.to_thread(view.advance))
    def advance_owns_lock():
        if view._advance_lock.acquire(blocking=False):
            view._advance_lock.release()
            return False
        return True
    try:
        await eventually(advance_owns_lock)
        pause = asyncio.create_task(view.pause())
        await asyncio.sleep(.03)
        assert not pause.done()
        release.set()
        await asyncio.gather(blocker, advance, pause)
        assert view._paused
        view.advance()
        await view.resume()
        assert not view._paused
    finally:
        release.set()
        await asyncio.gather(blocker, advance, return_exceptions=True)
        await view.close()


async def test_workflow_budgets_revisions_manual_steps_and_monitor_pause(core):
    view = WorkViews(core)
    plan = view.create_workflow([{"id": "a", "description": "first", "budget": {"tokens": 101}}], {"tokens": 100})
    with error("budget_exceeded"):
        view.run_workflow(plan["id"], "conversation", 1)
    assert not core.journal.list(RUNS, core.scope)
    with error("conflict"):
        view.run_workflow(plan["id"], "conversation", 0)
    for steps in ([], [None]):
        with error("invalid_plan"):
            view.create_workflow(steps, {"tokens": 100})
    plan = view.revise_workflow(plan["id"], [{"id": "a", "description": "Manual review", "assignee": {"kind": "user"},
        "checks": [{"id": "check", "event_kind": "review.recorded", "source": "consumer", "matches": {"accepted": True}}]}], 1, "Human step")
    run = view.run_workflow(plan["id"], "conversation", plan["revision"])
    assert not run["execution"]["attempts"]
    manual = view.workflow_step(plan["id"], "a", "in_progress", plan["revision"])
    evidence = core.journal.append(core.scope, "review.recorded", {"accepted": True}, source="consumer")
    view.workflow_step(plan["id"], "a", "completed", manual["revision"], evidence=[evidence.id])
    await view.start()
    await view.start()
    assert view.get_workflow(plan["id"])["state"] == "completed"
    await view.pause()
    next_plan = view.create_workflow([{"id": "one", "description": "Queued"}], {"tokens": 1000})
    with error("runtime_unavailable"):
        view.run_workflow(next_plan["id"], "conversation", 1)
    await view.resume()
    started = view.run_workflow(next_plan["id"], "conversation", 1)
    with error("invalid_transition"):
        view.workflow_step(next_plan["id"], "one", "completed", started["revision"], evidence=[evidence.id])
    with error("invalid_transition"):
        view.workflow_step(next_plan["id"], "one", "in_progress", started["revision"])
    with error("not_found"):
        view.workflow_step(next_plan["id"], "missing", "skipped", started["revision"])
    paused = view.workflow_state(next_plan["id"], "paused", started["revision"])
    assert core.journal.get("schedules", started["execution"]["attempts"][0]["schedule_id"], core.scope).value["state"] == "cancelled"
    with error("invalid_transition"):
        view.run_workflow(next_plan["id"], "conversation", paused["revision"])
    view.workflow_state(next_plan["id"], "active", paused["revision"])
    view.advance()
    assert view.get_workflow(next_plan["id"])["steps"][0]["state"] == "blocked"
    await view.close()
    await view.close()


async def test_initiative_manual_and_bounded_execution_attention_limits(core):
    view = WorkViews(core)
    event = core.journal.append(core.scope, "external.observed", {"value": "observed"}, source="connector")
    signals = {"urgency": 1, "expected_value": 1, "confidence": 1, "interruption_cost": 0, "resource_cost": 0, "duplication_penalty": 0}
    candidate = view.propose_initiative(event.id, "Review the observation", signals, time.time() + 60, "first")
    view.advance()
    assert view.inbox()[0]["attention"]["decision"] == "ask"
    started = view.decide_inbox(candidate["id"], "accept", "conversation", {"tokens": 1000})
    assert started["decision"] == "started" and core.journal.get("schedules", started["schedule_id"], core.scope)
    with error("invalid_transition"):
        view.decide_inbox(candidate["id"], "dismiss")
    second = view.propose_initiative(event.id, {"text": "Do bounded follow-up"}, signals, time.time() + 60, "second")
    view.attention_policy({"mode": "bounded", "conversation_id": "conversation", "resources": {"tokens": 1000}})
    view.advance()
    assert core.journal.get(INBOX, second["id"], core.scope).value["decision"] == "started"
    policy = view.attention_policy({"mode": "remember"}, 1)
    third = view.propose_initiative(event.id, "Remember this", signals, time.time() + .01, "third")
    view.advance()
    await asyncio.sleep(.02)
    with error("candidate_expired"):
        view.decide_inbox(third["id"], "accept", "conversation", {"tokens": 1})
    assert view.decide_inbox(third["id"], "dismiss")["decision"] == "dismissed"
    with error("invalid_decision"):
        view.decide_inbox(third["id"], "execute")
    malformed = view.propose_initiative(event.id, {"tool": "unmapped"}, signals, time.time() + 60, "fourth")
    view.attention_policy({"mode": "bounded", "conversation_id": "conversation", "resources": {"tokens": 1000}}, policy["revision"])
    view.advance()
    assert core.journal.get(INBOX, malformed["id"], core.scope).value["decision"] == "unavailable"
    for policy in ({"unknown": True}, {"mode": "unknown"}, {"notification_limit": -1}, {"minimum_score": 2},
                   {"quiet_hours": {}}, {"quiet_hours": {"timezone": "Unknown/Zone", "start_minute": 0, "end_minute": 1}}):
        with error("invalid_policy"):
            view.attention_policy(policy)
    assert view.attention_policy({"quiet_hours": {"timezone": "UTC", "start_minute": 0, "end_minute": 1}}, 3)


async def test_child_scope_views_delegation_and_cancel(core):
    view = WorkViews(core)
    parent = admitted(core, caps=["workspace.write"])
    with error("not_running"):
        await view.delegate(parent.id, "child", ["workspace.write"], {"tokens": 1000})
    # Library delegation uses the actual scheduler; no substitute child records.
    child = core.subagents.delegate(core.scope, parent.id, "child objective", capabilities=["workspace.write"], resources={"tokens": 1000})
    assert view.get_child(child.id)["work_id"] == child.value["work_id"]
    assert view.list_children()[0]["id"] == child.id
    assert view.cancel_child(child.id)["state"] == "cancelled"
    with error("not_found"):
        view.get_child("outside")
