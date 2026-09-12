from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent.attention import Attention
from agent.awareness import Awareness, timestamp
from agent.awareness.evidence import resolve_evidence, validate_checks, verify_checks
from agent.commitments import CommitmentService, validate_trigger
from agent.goals import GoalService, amounts
from agent.initiative import Initiative, score
from agent.planner import Planner
from agent.profile import ProfileRegistry
from agent.protocol import CoreError
from agent.state import Journal


CHECK = {"id": "ready", "event_kind": "awareness.observed", "source": "awareness", "matches": {"properties.ready": True}}
SIGNALS = {"urgency": 1, "expected_value": 1, "confidence": 1, "interruption_cost": 0, "resource_cost": 0, "duplication_penalty": 0}


@pytest.fixture
def journal(tmp_path):
    journal = Journal(tmp_path / "state.db")
    yield journal
    journal.close()


def observation(journal, tmp_path, *, scope="s", key="ready", value=True):
    path = tmp_path / key
    path.write_text("ready" if value else "pending")
    awareness = Awareness(journal)
    if journal.get("watchers", "files", scope) is None:
        awareness.register_watcher(scope, "files", "file", source_identity=str(tmp_path))
    row = awareness.observe(scope, "files", str(path), {"ready": path.read_text() == "ready"}, dedupe_key=key)
    return row.id


def active_goal(journal, *, limits=None, criteria=None):
    service = GoalService(journal)
    goal = service.create("s", "Produce the checked result", limits=limits, criteria=[CHECK] if criteria is None else criteria, now=1)
    return service, service.transition("s", goal.id, "active", expected_revision=goal.revision)


def candidate(journal, *, name="a", signals=None, expires=100, action=None, now=1):
    event = journal.append("s", "runtime.changed", {"name": name})
    return Initiative(journal).propose("s", event.id, {"topic": name} if action is None else action, signals=SIGNALS if signals is None else signals, expires_at=expires, dedupe_key=name, now=now)


def step(id="one", **kwargs):
    return {"id": id, "description": "Observe the artifact", "checks": [CHECK], **kwargs}


def test_profiles_roots_revisions_and_restart(journal, tmp_path):
    service = ProfileRegistry(journal)
    row = service.register("s", tmp_path, config={"display_name": "Sturgis", "channels": ["local"]})
    assert service.resources("s")["memory"].startswith(str(tmp_path))
    assert Path(row.value["resource_roots"]["schedules"]).is_dir()
    other = service.register("partition", tmp_path)
    assert other.value["resource_roots"]["memory"] != row.value["resource_roots"]["memory"]
    with pytest.raises(CoreError, match="revision"):
        service.update("s", 0, config={})
    disabled = service.update("s", row.revision, enabled=False)
    with pytest.raises(CoreError, match="disabled"):
        service.resources("s")
    enabled = service.update("s", disabled.revision, enabled=True, config={"display_name": "Entity"})
    assert enabled.value["configuration"]["display_name"] == "Entity"
    assert service.update("s", enabled.revision).value == enabled.value
    with pytest.raises(CoreError):
        service.get("other")
    journal.close()
    reopened = Journal(tmp_path / "state.db")
    assert ProfileRegistry(reopened).get("s").value["enabled"]
    reopened.close()


def test_profile_invalid_boundaries(journal, tmp_path):
    service = ProfileRegistry(journal)
    for config in [[], {"self_evolution_authority": True}, {"installation_authority": "all"}]:
        with pytest.raises(CoreError):
            service.register("bad", tmp_path, config=config)
    for workspace, roots in [(tmp_path / "absent", None), (tmp_path / "state.db", None), (None, None), (tmp_path, {}), (tmp_path, {"memory": tmp_path.parent, "schedules": "schedules"}), (tmp_path, {"memory": "same", "schedules": "same/sub"})]:
        with pytest.raises(CoreError):
            service.register("bad", workspace, resource_roots=roots)
    root = tmp_path / "workspace"
    root.mkdir()
    escape = root / "escape"
    escape.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(CoreError):
        service.register("bad", root, resource_roots={"memory": "escape/m", "schedules": "s"})
    row = service.register("s", root, resource_roots={"memory": "memory", "schedules": "schedules"})
    for kwargs in [{"origin": "inferred"}, {"enabled": "yes"}, {"config": {"governing_policy": "new"}}]:
        with pytest.raises(CoreError):
            service.update("s", row.revision, **kwargs)
    path = Path(row.value["resource_roots"]["memory"])
    path.rmdir()
    path.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(CoreError):
        service.resources("s")
    path.unlink()
    alternate = root / "alternate"
    alternate.mkdir()
    path.symlink_to(alternate, target_is_directory=True)
    with pytest.raises(CoreError, match="changed"):
        service.resources("s")
    path.unlink()
    path.write_text("not a directory")
    with pytest.raises(CoreError, match="directories"):
        service.resources("s")
    path.unlink()
    with pytest.raises(CoreError, match="unavailable"):
        service.resources("s")
    assert not path.exists()


def test_awareness_lifecycle_deduplication_and_stale_observations(journal):
    awareness = Awareness(journal)
    assert awareness.snapshot("s")["through_event_seq"] == 0
    watcher = awareness.register_watcher("s", "w", "process")
    row = awareness.observe("s", "w", "process:1", {"state": "running"}, dedupe_key="1", now=10, generation=1)
    assert awareness.observe("s", "w", "process:1", {"state": "running"}, dedupe_key="1", now=10) == row
    with pytest.raises(CoreError, match="reused"):
        awareness.observe("s", "w", "process:1", {"state": "changed"}, dedupe_key="1", now=10)
    stale = awareness.observe("s", "w", "process:1", {"state": "created"}, dedupe_key="0", now=5)
    assert not stale.value["applied"]
    assert awareness.snapshot("s")["objects"][0]["properties"]["state"] == "running"
    current = awareness.observe("s", "w", "process:1", {"state": "completed"}, dedupe_key="2", now=15)
    assert current.value["applied"]
    stopped = awareness.set_watcher_active("s", "w", False, expected_revision=watcher.revision)
    with pytest.raises(CoreError):
        awareness.observe("s", "w", "process:1", {}, dedupe_key="3")
    awareness.set_watcher_active("s", "w", True, expected_revision=stopped.revision)
    with pytest.raises(CoreError):
        awareness.observe("s", "w", "process:1", {}, dedupe_key="3", generation=1)
    assert awareness.snapshot("other")["objects"] == []
    with pytest.raises(CoreError):
        awareness.set_watcher_active("s", "w", 1, expected_revision=3)
    for value in [None, [], {"huge": "x" * 50}]:
        with pytest.raises(CoreError):
            Awareness(journal, max_observation_bytes=10).observe("s", "w", "o", value, dedupe_key="bad")
    for limit in [0, -1, True]:
        with pytest.raises(CoreError):
            Awareness(journal, max_observation_bytes=limit)
    for now in [-1, float("nan"), True, "now", 1e100]:
        with pytest.raises(CoreError):
            timestamp(now)


def test_scoped_outcome_checks_and_paged_evidence(journal, tmp_path):
    good = observation(journal, tmp_path)
    bad = observation(journal, tmp_path, key="pending", value=False)
    assert verify_checks(journal, "s", [CHECK], [good]) == {"ready": [good]}
    assert len(resolve_evidence(journal, "s", [good, good])) == 1
    for checks, evidence in [([], [good]), ([CHECK], None), ([CHECK], []), ([CHECK], ["missing"]), ([CHECK], [bad]), ([{**CHECK, "matches": {"missing.path": True}}], [good]), ([{**CHECK, "matches": {"properties.ready": 1}}], [good])]:
        with pytest.raises(CoreError):
            verify_checks(journal, "s", checks, evidence)
    with pytest.raises(CoreError):
        verify_checks(journal, "other", [CHECK], [good])
    for checks in [None, [1], [{}], [CHECK, CHECK], [{**CHECK, "source": "model"}], [{**CHECK, "event_kind": "child.claimed"}], [{**CHECK, "matches": {}}], [{**CHECK, "matches": {"": True}}], [{**CHECK, "id": ""}]]:
        with pytest.raises(CoreError):
            validate_checks(checks)
    with pytest.raises(CoreError):
        resolve_evidence(journal, "s", [None])
    for number in range(1001):
        journal.append("s", "noise", {"n": number})
    late = journal.append("s", "last", {})
    assert resolve_evidence(journal, "s", [late.id])[0] == late


def test_goal_states_evidence_and_atomic_revision(journal, tmp_path):
    service, goal = active_goal(journal, limits={"tokens": 100})
    evidence = observation(journal, tmp_path)
    for state in ["waiting", "blocked", "paused", "reviewing"]:
        goal = service.transition("s", goal.id, state, expected_revision=goal.revision, reason="Needs input")
        goal = service.transition("s", goal.id, "active", expected_revision=goal.revision)
    with pytest.raises(CoreError):
        service.transition("s", goal.id, "blocked", expected_revision=goal.revision)
    with pytest.raises(CoreError):
        service.transition("s", goal.id, "achieved", expected_revision=goal.revision, evidence=["fake"])
    goal = service.edit("s", goal.id, expected_revision=goal.revision, objective="Checked artifact", limits={"tokens": 120}, criteria=[CHECK])
    child = service.create("s", "Related objective", parent_id=goal.id)
    assert child.value["parent_id"] == goal.id
    with pytest.raises(CoreError):
        service.create("other", "Cross scope", parent_id=goal.id)
    event_count = len(journal.events("s"))
    with pytest.raises(CoreError):
        service.transition("s", goal.id, "achieved", expected_revision=1, evidence=[evidence])
    assert len(journal.events("s")) == event_count
    goal = service.transition("s", goal.id, "achieved", expected_revision=goal.revision, evidence=[evidence])
    assert goal.value["evidence"] == {"ready": [evidence]}
    assert service.archive("s", goal.id, expected_revision=goal.revision).value["archived"]
    for method in [lambda: service.transition("s", goal.id, "active", expected_revision=goal.revision), lambda: service.edit("s", goal.id, expected_revision=goal.revision, objective="reset"), lambda: service.archive("s", child.id, expected_revision=child.revision)]:
        with pytest.raises(CoreError):
            method()
    assert len(service.list("s")) == 2
    assert Awareness(journal).snapshot("s")["through_event_seq"] == journal.events("s")[-1].seq


def test_goal_reservations_cumulative_usage_and_overage(journal):
    service, goal = active_goal(journal, limits={"tokens": 10, "tool_calls": 2})
    reservation = service.reserve("s", goal.id, "r1", {"tokens": 6, "tool_calls": 1})
    assert service.reserve("s", goal.id, "r1", {"tokens": 6, "tool_calls": 1}) == reservation
    for rid, allocation in [("r1", {"tokens": 5}), ("r2", {"tokens": 5})]:
        with pytest.raises(CoreError):
            service.reserve("s", goal.id, rid, allocation)
    charged = service.charge("s", goal.id, "r1", "u1", {"tokens": 4})
    assert charged.value["usage"] == {"tokens": 4}
    assert charged.value["reserved"]["tokens"] == 2
    assert service.charge("s", goal.id, "r1", "u1", {"tokens": 4}) == charged
    with pytest.raises(CoreError):
        service.charge("s", goal.id, "r1", "u1", {"tokens": 5})
    with pytest.raises(CoreError):
        service.edit("s", goal.id, expected_revision=charged.revision, limits={"tokens": 5})
    service.reserve("s", goal.id, "r2", {"tokens": 4})
    released = service.release("s", goal.id, "r1")
    assert released.value["reserved"] == {"tokens": 4, "tool_calls": 0}
    assert service.release("s", goal.id, "r1") == released
    overrun = service.charge("s", goal.id, "r2", "u2", {"tokens": 8})
    assert overrun.value["usage"]["tokens"] == 12
    assert overrun.value["reserved"]["tokens"] == 0
    assert overrun.value["state"] == "blocked" and overrun.value["budget_overrun"]
    with pytest.raises(CoreError):
        service.reserve("s", goal.id, "r3", {"tokens": 1})
    with pytest.raises(CoreError):
        service.transition("s", goal.id, "active", expected_revision=overrun.revision)
    revised = service.edit("s", goal.id, expected_revision=overrun.revision, limits={"tokens": 20})
    resumed = service.transition("s", goal.id, "active", expected_revision=revised.revision)
    assert not resumed.value["budget_overrun"]
    late = service.charge("s", goal.id, "r1", "u3", {"tokens": 1})
    assert late.value["usage"]["tokens"] == 13 and late.value["budget_overrun"]
    service.release("s", goal.id, "r2")
    _, other = active_goal(journal)
    for call in [lambda: service.release("s", other.id, "r1"), lambda: service.charge("s", other.id, "r1", "wrong", {}), lambda: service.reserve("s", other.id, "r1", {"tokens": 6, "tool_calls": 1})]:
        with pytest.raises(CoreError):
            call()
    for invalid in [[], {"tokens": True}, {"tokens": -1}, {"nonsense": 1}, {"tokens": 1.5}]:
        with pytest.raises(CoreError):
            amounts(invalid)


def test_goal_competing_reservations_and_reopen(journal, tmp_path):
    service, goal = active_goal(journal, limits={"tokens": 10})
    def reserve(key):
        try:
            service.reserve("s", goal.id, key, {"tokens": 7})
            return "reserved"
        except CoreError as error:
            return error.code
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(reserve, ["a", "b"])) == ["budget_exceeded", "reserved"]
    journal.close()
    reopened = Journal(tmp_path / "state.db")
    assert GoalService(reopened).get("s", goal.id).value["reserved"]["tokens"] == 7
    reopened.close()


def test_commitments_evidence_progress_and_overdue(journal, tmp_path):
    service = CommitmentService(journal)
    row = service.create("s", "Deliver the result", beneficiary="user", criteria=[CHECK], expires_at=10, now=1)
    with pytest.raises(CoreError):
        service.set_progress("s", row.id, "blocked", expected_revision=row.revision, reason="no input")
    row = service.transition("s", row.id, "accepted", expected_revision=row.revision)
    row = service.set_progress("s", row.id, "blocked", expected_revision=row.revision, reason="Waiting for evidence")
    assert service.list("s", now=11)[0]["overdue"]
    assert service.get("s", row.id).value["state"] == "accepted"
    for kwargs in [{"progress": "unknown"}, {"progress": "blocked"}]:
        with pytest.raises(CoreError):
            service.set_progress("s", row.id, expected_revision=row.revision, **kwargs)
    row = service.set_progress("s", row.id, "in_progress", expected_revision=row.revision)
    evidence = observation(journal, tmp_path)
    with pytest.raises(CoreError):
        service.transition("s", row.id, "fulfilled", expected_revision=row.revision, evidence=[])
    row = service.transition("s", row.id, "fulfilled", expected_revision=row.revision, evidence=[evidence])
    assert not service.list("s", now=20)[0]["overdue"]
    with pytest.raises(CoreError):
        service.transition("s", row.id, "accepted", expected_revision=row.revision)
    with pytest.raises(CoreError):
        service.create("s", "Too late", beneficiary="user", expires_at=1, now=2)
    cancelled = service.create("s", "Optional", beneficiary="user")
    assert service.transition("s", cancelled.id, "cancelled", expected_revision=cancelled.revision, reason="Withdrawn").value["state"] == "cancelled"


def test_commitment_registrar_contract_is_atomic_before_release(journal):
    # This supplied registrar tests the domain's Journal port, not scheduler qualification.
    def register(scope, owner_id, trigger, *, wait_id, after_seq, expires_at):
        return journal.put("waits", wait_id, scope, {"state": "armed", "owner_id": owner_id, "trigger": trigger, "after_seq": after_seq, "expires_at": expires_at})
    def cancel(scope, wait_id):
        row = journal.get("waits", wait_id, scope)
        return journal.put("waits", wait_id, scope, {**row.value, "state": "cancelled"}, row.revision)
    service = CommitmentService(journal, cancel_wait=cancel)
    row = service.create("s", "Continue after reply", beneficiary="user", now=1)
    row = service.transition("s", row.id, "accepted", expected_revision=row.revision)
    trigger = {"kind": "user_response", "conversation_id": "conversation"}
    for registrar in [None, lambda *args, **kwargs: None]:
        with pytest.raises(CoreError):
            service.begin_wait("s", row.id, trigger, registrar=registrar, after_seq=0, expected_revision=row.revision)
    with pytest.raises(CoreError):
        service.begin_wait("s", row.id, trigger, registrar=register, after_seq=0, expected_revision=0)
    assert journal.list("waits", "s") == []
    waiting, release = service.begin_wait("s", row.id, trigger, registrar=register, after_seq=0, expected_revision=row.revision, expires_at=10)
    assert release["release_compute"] and waiting.value["progress"] == "waiting"
    with pytest.raises(CoreError):
        service.begin_wait("s", row.id, trigger, registrar=register, after_seq=0, expected_revision=waiting.revision)
    with pytest.raises(CoreError):
        service.resume_wait("s", row.id, wait_id=release["wait_id"], expected_revision=waiting.revision)
    with pytest.raises(CoreError, match="cancelled transactionally"):
        CommitmentService(journal).transition("s", row.id, "cancelled", expected_revision=waiting.revision, reason="Stopped")
    with pytest.raises(CoreError, match="durably confirmed"):
        CommitmentService(journal, cancel_wait=lambda *args: None).transition("s", row.id, "cancelled", expected_revision=waiting.revision, reason="Stopped")
    wait = journal.get("waits", release["wait_id"], "s")
    journal.put("waits", wait.id, "s", {**wait.value, "state": "fired"}, wait.revision)
    resumed = service.resume_wait("s", row.id, wait_id=wait.id, expected_revision=waiting.revision)
    assert resumed.value["wait_id"] is None
    waiting, release = service.begin_wait("s", row.id, trigger, registrar=register, after_seq=0, expected_revision=resumed.revision)
    service.transition("s", row.id, "failed", expected_revision=waiting.revision, reason="Unavailable")
    assert journal.get("waits", release["wait_id"], "s").value["state"] == "cancelled"


def test_all_trigger_shapes():
    for trigger in [{"kind": "time", "at": 10}, {"kind": "schedule", "job_id": "j"}, {"kind": "child_terminal", "child_id": "c"}, {"kind": "process_exit", "process_id": "p"}, {"kind": "file_changed", "object_id": "f"}, {"kind": "repository_changed", "object_id": "r"}, {"kind": "channel_message", "conversation_id": "c"}, {"kind": "external_condition", "connector": "x", "matches": {"ready": True}}, {"kind": "user_response", "conversation_id": "c"}]:
        assert validate_trigger(trigger) == trigger
    for trigger in [None, {}, {"kind": []}, {"kind": "bad"}, {"kind": "time"}, {"kind": "external_condition", "connector": "x", "matches": {}}, {"kind": "process_exit", "process_id": ""}]:
        with pytest.raises(CoreError):
            validate_trigger(trigger)


def test_initiative_evidence_scores_expiry_and_deduplication(journal):
    assert score(SIGNALS) == 1
    assert score({**SIGNALS, "interruption_cost": 1, "resource_cost": 1}) < score(SIGNALS)
    for values in [None, {}, {**SIGNALS, "urgency": 2}, {**SIGNALS, "urgency": True}, {**SIGNALS, "confidence": float("nan")}]:
        with pytest.raises(CoreError):
            score(values)
    service = Initiative(journal)
    row = candidate(journal)
    assert service.propose("s", row.value["source_event_id"], row.value["action"], signals=SIGNALS, expires_at=100, dedupe_key="a", now=2) == row
    for kwargs in [{"action": "changed", "expires_at": 100, "dedupe_key": "a"}, {"action": [], "expires_at": 100, "dedupe_key": "new"}, {"action": "expired", "expires_at": 1, "dedupe_key": "old"}]:
        with pytest.raises(CoreError):
            service.propose("s", row.value["source_event_id"], signals=SIGNALS, now=2, **kwargs)
    with pytest.raises(CoreError):
        service.propose("other", row.value["source_event_id"], "action", signals=SIGNALS, expires_at=100, dedupe_key="a", now=1)


def test_attention_proactive_policies_and_notification_accounting(journal):
    service = Attention(journal)
    row = candidate(journal)
    for policy, workload, now, expected in [({}, "idle", 101, "ignore"), ({"mode": "disabled"}, "idle", 2, "remember"), ({"mode": "remember"}, "idle", 2, "remember"), ({"notification_limit": 0}, "idle", 2, "batch"), ({}, "interactive", 2, "schedule"), ({"mode": "bounded"}, "idle", 2, "start_bounded_work")]:
        result = service.evaluate_initiative("s", row.id, policy=policy, workload=workload, now=now)
        assert result.value["decision"] == expected
        assert "selected" not in result.value
    suggestion = candidate(journal, name="suggest")
    decision = service.evaluate_initiative("s", suggestion.id, now=2)
    assert decision.value["decision"] == "ask"
    assert service.evaluate_initiative("s", suggestion.id, now=3) == decision
    assert journal.list("attention_notifications", "s")[0].value["count"] == 1
    duplicate = candidate(journal, name="duplicate", action=suggestion.value["action"])
    assert service.evaluate_initiative("s", duplicate.id, now=3).value["reason"] == "duplicate"
    low = candidate(journal, name="low", signals={key: 0 for key in SIGNALS})
    assert service.evaluate_initiative("s", low.id, now=2).value["decision"] == "remember"
    medium = candidate(journal, name="medium", signals={**SIGNALS, "urgency": .3, "expected_value": .3, "confidence": .3})
    assert service.evaluate_initiative("s", medium.id, now=2).value["decision"] == "notify"
    assert journal.list("attention_notifications", "s")[0].value["count"] == 2
    assert service.evaluate_initiative("s", candidate(journal, name="budget").id, policy={"notification_limit": 2}, now=2).value["reason"] == "notification budget"


def test_attention_quiet_hours_and_validation(journal):
    attention = Attention(journal)
    row = candidate(journal, expires=200000)
    for start, end, now, expected in [(0, 60, 10, "batch"), (23 * 60, 60, 10, "batch"), (0, 60, 7200, "ask")]:
        policy = {"quiet_hours": {"timezone": "UTC", "start_minute": start, "end_minute": end}}
        assert attention.evaluate_initiative("s", row.id, policy=policy, now=now).value["decision"] == expected
    for policy in [[], {"unknown": True}, {"mode": "unlimited"}, {"notification_limit": -1}, {"minimum_score": float("inf")}, {"duplicate_seconds": -1}, {"quiet_hours": {}}, {"quiet_hours": {"timezone": "Mars/Base", "start_minute": 0, "end_minute": 60}}]:
        with pytest.raises(CoreError):
            attention.evaluate_initiative("s", row.id, policy=policy, now=2)
    with pytest.raises(CoreError):
        attention.evaluate_initiative("s", row.id, workload="unbounded", now=2)


def test_context_attention_preserves_required_inputs_and_explicit_deferrals(journal):
    attention = Attention(journal)
    items = [{"id": "rules", "kind": "instructions", "tokens": 3}, {"id": "input", "kind": "user_input", "tokens": 4, "priority": -100}, {"id": "memory", "kind": "memory", "tokens": 5, "priority": 100, "source_revision": 2, "range": [0, 20]}, {"id": "tool", "kind": "tool_observation", "tokens": 2, "required": True}]
    selection = attention.select_context("s", items, budget=9, work_id="w")
    assert {item["id"] for item in selection.value["selected"]} == {"rules", "input", "tool"}
    assert selection.value["can_invoke"] and selection.value["used_tokens"] == 9
    assert selection.value["deferred"] == [{"id": "memory", "reason": "token_budget", "required": False}]
    missing = attention.select_context("s", items, budget=3, work_id="w")
    assert not missing.value["can_invoke"] and any(item["required"] for item in missing.value["deferred"])
    for source, budget, required_ids in [(items, -1, []), ({}, 10, []), ([{}], 10, []), (items + [items[0]], 10, []), ([{**items[0], "kind": "made-up"}], 10, []), ([{**items[0], "tokens": True}], 10, []), ([{**items[0], "priority": float("nan")}], 10, []), (items, 10, ["absent"])]:
        with pytest.raises(CoreError):
            attention.select_context("s", source, budget=budget, required_ids=required_ids, work_id="w")
    with pytest.raises(CoreError):
        attention.select_context("s", items, budget=10, required_ids=None, work_id="w")


def test_planner_routes_dependencies_assignments_and_history(journal, tmp_path):
    planner = Planner(journal)
    for kwargs, expected in [({}, "direct"), ({"tool_count": 1}, "single_tool"), ({"step_count": 2}, "planned"), ({"tool_count": 2}, "planned"), ({"delegated": True}, "delegated"), ({"monitoring": True}, "monitoring"), ({"recurring": True}, "recurring")]:
        assert planner.classify("Work", **kwargs) == expected
    with pytest.raises(CoreError):
        planner.classify("Work", tool_count=-1)
    _, goal = active_goal(journal)
    commitment = CommitmentService(journal).create("s", "Report result", beneficiary="user")
    steps = [step("one"), step("two", dependencies=["one"], assignee={"kind": "child", "id": "child_1"}, expectation={"ready": True}, budget={"tokens": 10})]
    plan = planner.create("s", steps, goal_id=goal.id, commitment_id=commitment.id, budget={"tokens": 30})
    assert [item["id"] for item in planner.ready("s", plan.id)] == ["one"]
    with pytest.raises(CoreError):
        planner.transition_step("s", plan.id, "two", "in_progress", expected_revision=plan.revision)
    with pytest.raises(CoreError):
        planner.transition("s", plan.id, "completed", expected_revision=plan.revision)
    plan = planner.transition_step("s", plan.id, "one", "in_progress", expected_revision=plan.revision)
    with pytest.raises(CoreError):
        planner.revise("s", plan.id, steps, expected_revision=plan.revision, reason="Changed")
    with pytest.raises(CoreError):
        planner.transition_step("s", plan.id, "one", "completed", expected_revision=plan.revision, evidence=[])
    evidence = observation(journal, tmp_path)
    plan = planner.transition_step("s", plan.id, "one", "completed", expected_revision=plan.revision, evidence=[evidence])
    assert planner.ready("s", plan.id)[0]["id"] == "two"
    plan = planner.revise("s", plan.id, steps, expected_revision=plan.revision, reason="Preserve successful observation")
    assert plan.value["steps"][0]["state"] == "completed"
    plan = planner.transition("s", plan.id, "paused", expected_revision=plan.revision)
    assert planner.ready("s", plan.id) == []
    plan = planner.transition("s", plan.id, "active", expected_revision=plan.revision)
    plan = planner.transition_step("s", plan.id, "two", "in_progress", expected_revision=plan.revision)
    plan = planner.transition_step("s", plan.id, "two", "blocked", expected_revision=plan.revision, reason="Child unavailable")
    plan = planner.transition_step("s", plan.id, "two", "skipped", expected_revision=plan.revision, reason="Optional result withdrawn")
    plan = planner.transition("s", plan.id, "completed", expected_revision=plan.revision)
    assert GoalService(journal).get("s", goal.id).value["state"] == "active"
    assert len(journal.list("plan_history", "s")) == plan.revision
    for action in [lambda: planner.transition("s", plan.id, "active", expected_revision=plan.revision), lambda: planner.transition_step("s", plan.id, "one", "in_progress", expected_revision=plan.revision), lambda: planner.revise("s", plan.id, steps, expected_revision=plan.revision, reason="reset")]:
        with pytest.raises(CoreError):
            action()


def test_planner_invalid_graphs_and_revision_changes(journal):
    planner = Planner(journal)
    invalid = [None, [], [1], [{**step(), "unknown": 1}], [step(), step()], [step(dependencies=["missing"])], [step(dependencies=["one"])], [step("a", dependencies=["b"]), step("b", dependencies=["a"])], [step(dependencies="one")], [step(dependencies=[1])], [step(assignee={"kind": "child"})], [step(assignee={"kind": "child", "id": ""})], [step(expectation=[])], [step(checks=[{}])], [step(description="")]]
    for steps in invalid:
        with pytest.raises(CoreError):
            planner.create("s", steps)
    row = planner.create("s", [step(assignee={"kind": "user"})])
    revised = planner.revise("s", row.id, [step(description="New explicit result")], expected_revision=row.revision, reason="User changed scope")
    assert revised.value["steps"][0]["state"] == "pending"
    with pytest.raises(CoreError):
        planner.revise("s", row.id, [step()], expected_revision=row.revision, reason="Stale")
    assert planner.transition("s", row.id, "abandoned", expected_revision=revised.revision, reason="Withdrawn").value["state"] == "abandoned"
