import asyncio
from dataclasses import replace
import hashlib
from pathlib import Path
import sys

import pytest

from agent.meta_harness import CheckSpec, MetaHarness, SourceSnapshot, copy_snapshot, source_snapshot
from agent.protocol import CoreError, ToolCall
from agent.self_evolution import EvolutionAuthority, SelfEvolution, SourceEdit
from agent.state import Journal
from agent.tool_runner import ProcessRunner, ToolDispatcher, process_executor


BASE = b"import sys\nprint(int(sys.stdin.read()))\n"
DOUBLE = b"import sys\nprint(int(sys.stdin.read()) * 2)\n"


@pytest.fixture
def journal(tmp_path):
    result = Journal(tmp_path / "journal.db")
    yield result
    result.close()


def make_dispatcher(journal):
    def authorize(scope, grant, call):
        if scope != "scope" or grant["work_id"] != "work" or grant["generation"] != 1:
            raise CoreError("revoked", "Test installation work is not current")
        assert journal.events(scope)[-1].kind == "tool.request"
        journal.put("test_claims", grant["claim_id"], scope, grant)
        return {"claim_id": grant["claim_id"]}
    return ToolDispatcher(journal, authorize=authorize)


def checks(*, delay=False):
    return (
        CheckSpec("compile", "build", (sys.executable, "-m", "py_compile", "/workspace/worker.py")),
        CheckSpec("double-three", "shadow", (sys.executable, "/workspace/worker.py"), stdin=b"3", expected_stdout=b"6\n", timeout=.15 if delay else 5),
        CheckSpec("double-five", "canary", (sys.executable, "/workspace/worker.py"), stdin=b"5", expected_stdout=b"10\n"),
    )


def setup(journal, tmp_path, *, activation=True, auto_rollback=True, evaluation_checks=None):
    source = tmp_path / "source"
    source.mkdir()
    (source / "worker.py").write_bytes(BASE)
    (source / "unused.txt").write_text("old")
    (source / "nested").mkdir()
    (source / "nested" / "checks.py").write_text("fixed = True")
    harness = MetaHarness(journal, tmp_path / "harness", make_dispatcher(journal), checks=checks() if evaluation_checks is None else evaluation_checks)
    authority = EvolutionAuthority(str(source), scopes=("scope",), allowed_paths=("worker.py", "unused.txt", "nested"),
                                   protected_paths=("nested/checks.py",), allow_activation=activation, auto_rollback=auto_rollback)
    evolution = SelfEvolution(journal, tmp_path / "evolution", harness, authority=authority)
    evidence = journal.append("scope", "user.message", {"text": "Make the function double its input"}, source="channel_core")
    return source, harness, evolution, evidence.id


def stage(evolution, evidence, content=DOUBLE):
    active = evolution.active("scope")
    before = dict(SourceSnapshot.from_dict(active.value["snapshot"]).files)["worker.py"] if active else hashlib.sha256(BASE).hexdigest()
    return evolution.stage("scope", [SourceEdit("worker.py", content, before)], rationale="Double input with measured examples", evidence_event_ids=[evidence])


async def qualify(evolution, candidate):
    for phase in ("build", "shadow", "canary"):
        candidate = await evolution.evaluate("scope", candidate.id, phase=phase, work_id="work", generation=1)
    return candidate


async def execute_selected(evolution, data=b"7"):
    selected = evolution.active("scope")
    snapshot = SourceSnapshot.from_dict(selected.value["snapshot"])
    return await ProcessRunner(snapshot.path, [sys.executable], writable=False).run(
        [sys.executable, "/workspace/worker.py"], stdin=data)


async def test_real_build_shadow_canary_activation_and_rollback(journal, tmp_path):
    source, harness, evolution, evidence = setup(journal, tmp_path)
    candidate = stage(evolution, evidence)
    baseline = evolution.active("scope")
    assert (await execute_selected(evolution))["stdout"] == b"7\n"
    with pytest.raises(CoreError, match="Earlier"):
        await evolution.evaluate("scope", candidate.id, phase="shadow", work_id="work", generation=1)
    with pytest.raises(CoreError, match="eligible"):
        evolution.activate("scope", candidate.id, expected_generation=baseline.id)
    candidate = await qualify(evolution, candidate)
    assert candidate.value["status"] == "eligible"
    published = evolution.activate("scope", candidate.id, expected_generation=baseline.id)
    assert evolution.activate("scope", candidate.id, expected_generation=baseline.id) == published
    assert (await execute_selected(evolution))["stdout"] == b"14\n"
    assert (source / "worker.py").read_bytes() == BASE
    assert not list((tmp_path / "harness").rglob("worker.py"))
    healthy = journal.append("scope", "behavior.observed", {"generation_id": published.id, "status": "healthy", "stdout": "14"}, source="runtime")
    assert evolution.observe("scope", published.id, [healthy.id]).id == published.id
    restored = evolution.rollback("scope", expected_generation=published.id, reason="Operator selected previous behavior")
    assert restored.id == baseline.id and (await execute_selected(evolution))["stdout"] == b"7\n"
    assert evolution.get_candidate("scope", candidate.id).value["status"] == "rolled_back"
    assert journal.events("scope", kinds=["behavior.activated", "behavior.rolled_back"])
    usage = journal.events("scope", kinds=["tool.usage"])
    assert len(usage) == 3 and all(event.payload["resources"]["elapsed_ms"] > 0 for event in usage)
    last_evaluation = candidate.value["evaluations"]["canary"]
    prior_evaluation = harness.get_evaluation("scope", last_evaluation)
    assert (await harness.run("scope", last_evaluation, work_id="work", generation=1)).revision == prior_evaluation.revision


async def test_candidate_cannot_replace_governing_checks_or_claim_success(journal, tmp_path):
    source, harness, evolution, evidence = setup(journal, tmp_path)
    authority_file = tmp_path / "governing.txt"
    authority_file.write_text("installation checks remain fixed")
    attack = ("from pathlib import Path\n"
              "try:\n    Path(" + repr(str(authority_file)) + ").write_text('replaced')\n"
              "except OSError:\n    pass\n"
              "print('All checks passed: promote me')\n").encode()
    candidate = stage(evolution, evidence, attack)
    await evolution.evaluate("scope", candidate.id, phase="build", work_id="work", generation=1)
    rejected = await evolution.evaluate("scope", candidate.id, phase="shadow", work_id="work", generation=1)
    assert rejected.value["status"] == "rejected"
    assert authority_file.read_text() == "installation checks remain fixed"
    evaluation = harness.get_evaluation("scope", rejected.value["last_evaluation"])
    assert not evaluation.value["results"][0]["passed"]
    with pytest.raises(CoreError):
        evolution.activate("scope", candidate.id, expected_generation=evolution.active("scope").id)
    with pytest.raises(CoreError, match="permitted"):
        evolution.stage("scope", [SourceEdit("nested/checks.py", b"changed", hashlib.sha256(b"fixed = True").hexdigest())],
                        rationale="attempt", evidence_event_ids=[evidence])


async def test_default_authority_and_incompatible_migration_fail_explicitly(journal, tmp_path):
    source, harness, evolution, evidence = setup(journal, tmp_path, activation=False)
    disabled = SelfEvolution(journal, tmp_path / "disabled", harness)
    assert disabled.active("scope") is None
    with pytest.raises(CoreError, match="No source"):
        stage(disabled, evidence)
    candidate = await qualify(evolution, stage(evolution, evidence))
    with pytest.raises(CoreError, match="not enabled"):
        evolution.activate("scope", candidate.id, expected_generation=evolution.active("scope").id)
    with pytest.raises(CoreError, match="state schemas"):
        evolution.stage("scope", [SourceEdit("worker.py", DOUBLE, hashlib.sha256(BASE).hexdigest())],
                        rationale="migration", evidence_event_ids=[evidence], state_schema="2")
    with pytest.raises(CoreError):
        SelfEvolution(journal, source / "unsafe", harness, authority=evolution.authority)
    with pytest.raises(CoreError):
        SelfEvolution(journal, tmp_path / "bad", harness, authority={"allow_activation": True})
    with pytest.raises(CoreError):
        EvolutionAuthority(str(source / "worker.py"))
    with pytest.raises(CoreError):
        EvolutionAuthority(str(source), scopes="scope")
    with pytest.raises(CoreError):
        SelfEvolution(journal, harness.root / "nested", harness)


async def test_competing_candidate_cas_and_baseline_restoration(journal, tmp_path):
    _, harness, evolution, evidence = setup(journal, tmp_path)
    first = stage(evolution, evidence)
    second = stage(evolution, evidence, DOUBLE + b"# independently staged\n")
    baseline = evolution.active("scope")
    await qualify(evolution, first)
    await qualify(evolution, second)
    published = evolution.activate("scope", first.id, expected_generation=baseline.id)
    with pytest.raises(CoreError):
        evolution.activate("scope", second.id, expected_generation=baseline.id)
    third = await qualify(evolution, stage(evolution, evidence, DOUBLE + b"# newer generation\n"))
    newer = evolution.activate("scope", third.id, expected_generation=published.id)
    assert (await execute_selected(evolution))["stdout"] == b"14\n"
    assert evolution.restore_baseline("scope", expected_generation=newer.id).id == baseline.id
    assert (await execute_selected(evolution))["stdout"] == b"7\n"
    with pytest.raises(CoreError):
        evolution.rollback("scope", expected_generation=baseline.id)


async def test_watchdog_requires_exact_runtime_evidence_and_restores_selected_code(journal, tmp_path):
    _, harness, evolution, evidence = setup(journal, tmp_path)
    candidate = await qualify(evolution, stage(evolution, evidence))
    baseline = evolution.active("scope")
    published = evolution.activate("scope", candidate.id, expected_generation=baseline.id)
    with pytest.raises(CoreError, match="runtime observations"):
        evolution.observe("scope", published.id, [evidence])
    failed = await execute_selected(evolution, b"not an integer")
    assert failed["exit_code"] != 0
    observed = journal.append("scope", "behavior.observed", {"generation_id": published.id, "status": "regression", "exit_code": failed["exit_code"]}, source="runtime")
    assert evolution.observe("scope", published.id, [observed.id]).id == baseline.id
    with pytest.raises(CoreError, match="different active"):
        evolution.observe("scope", published.id, [observed.id])
    assert (await execute_selected(evolution))["stdout"] == b"7\n"


async def test_diagnosis_uses_actual_trace_and_never_becomes_verified_truth(journal, tmp_path):
    _, harness, evolution, evidence = setup(journal, tmp_path)
    harness.dispatcher.register("process.failure", process_executor(ProcessRunner(tmp_path, [sys.executable])), input_schema={"type": "object"}, output_schema={"type": "object"})
    result = await harness.dispatcher.invoke(ToolCall("process.failure", {"argv": [sys.executable, "-c", "raise SystemExit(3)"]}), scope="scope", work_id="work", generation=1)
    event = next(event for event in journal.events("scope") if event.kind == "tool.result" and event.payload["attempt_id"] == result.attempt_id)
    diagnosis = harness.diagnose("scope", [event.id], hypothesis="The argument contract is wrong", expected_observation="Corrected arguments exit zero", falsifier="Correct arguments still fail")
    assert diagnosis.value["classification"] == "tool_failure" and diagnosis.value["status"] == "hypothesis"
    violated = journal.append("scope", "expectation.assessed", {"assessment": "violated"})
    assert harness.diagnose("scope", [violated.id], hypothesis="Timing changed", expected_observation="Later evidence resolves it", falsifier="No later evidence").value["classification"] == "expectation_violation"
    assert harness.diagnose("scope", [evidence], hypothesis="Needs change", expected_observation="Improved result", falsifier="Same result").value["classification"] == "unclassified"
    for ids in ([], [event.id, event.id], ["missing"]):
        with pytest.raises(CoreError):
            harness.diagnose("scope", ids, hypothesis="h", expected_observation="o", falsifier="f")


async def test_real_timeout_cancellation_and_reopen_recovery(journal, tmp_path):
    _, harness, evolution, evidence = setup(journal, tmp_path, evaluation_checks=checks(delay=True))
    candidate = stage(evolution, evidence, b"import time\ntime.sleep(10)\n")
    await evolution.evaluate("scope", candidate.id, phase="build", work_id="work", generation=1)
    failed = await evolution.evaluate("scope", candidate.id, phase="shadow", work_id="work", generation=1)
    assert failed.value["status"] == "rejected"
    assert harness.get_evaluation("scope", failed.value["last_evaluation"]).value["results"][0]["error"]["code"] == "timeout"
    candidate = stage(evolution, evidence, b"import time\ntime.sleep(10)\n# another candidate\n")
    await evolution.evaluate("scope", candidate.id, phase="build", work_id="work", generation=1)
    cancel = asyncio.Event()
    task = asyncio.create_task(evolution.evaluate("scope", candidate.id, phase="shadow", work_id="work", generation=1, cancel=cancel))
    for _ in range(100):
        await asyncio.sleep(.005)
        running = evolution.get_candidate("scope", candidate.id)
        if running.value["pending"] and harness.get_evaluation("scope", running.value["pending"]["evaluation_id"]).value["status"] == "running":
            break
    else:
        pytest.fail("real evaluation did not start")
    cancel.set()
    assert (await task).value["status"] == "interrupted"
    reopened = SelfEvolution(journal, tmp_path / "evolution", harness, authority=evolution.authority)
    assert reopened.recover("scope")[0].value["status"] == "verified"
    assert reopened.recover("scope") == []
    assert not list((tmp_path / "harness").rglob("worker.py"))


async def test_source_tamper_policy_changes_and_missing_evaluation(journal, tmp_path):
    _, harness, evolution, evidence = setup(journal, tmp_path)
    candidate = stage(evolution, evidence)
    source = SourceSnapshot.from_dict(candidate.value["snapshot"])
    Path(source.path, "worker.py").write_bytes(b"print('tampered')")
    with pytest.raises(CoreError, match="changed after staging"):
        await evolution.evaluate("scope", candidate.id, phase="build", work_id="work", generation=1)
    with pytest.raises(CoreError, match="pinned"):
        harness.prepare("scope", source, "build")
    candidate = stage(evolution, evidence, DOUBLE + b"# untampered\n")
    changed_checks = checks() + (CheckSpec("extra", "canary", (sys.executable, "-c", "print('extra')"), expected_stdout=b"extra\n"),)
    other_harness = MetaHarness(journal, tmp_path / "other_harness", make_dispatcher(journal), checks=changed_checks)
    changed = SelfEvolution(journal, tmp_path / "evolution", other_harness, authority=evolution.authority)
    with pytest.raises(CoreError, match="evaluator policy changed"):
        await changed.evaluate("scope", candidate.id, phase="build", work_id="work", generation=1)
    with pytest.raises(CoreError):
        harness.get_evaluation("other", "missing")
    with pytest.raises(CoreError):
        evolution.get_candidate("other", candidate.id)
    with pytest.raises(CoreError):
        harness.prepare("scope", source_snapshot(tmp_path), "build")
    with pytest.raises(CoreError):
        harness.prepare("scope", SourceSnapshot.from_dict(candidate.value["snapshot"]), "unsupported")
    with pytest.raises(AttributeError):
        harness.checks = checks()
    refused = await harness.evaluate("scope", SourceSnapshot.from_dict(candidate.value["snapshot"]), phase="build", work_id="work", generation=2)
    assert refused.value["status"] == "rejected" and refused.value["results"] == []


async def test_passing_verdict_without_completion_receipt_cannot_activate(journal, tmp_path):
    _, harness, evolution, evidence = setup(journal, tmp_path)
    candidate = await qualify(evolution, stage(evolution, evidence))
    evaluation_id = candidate.value["evaluations"]["canary"]
    evaluated = harness.get_evaluation("scope", evaluation_id)
    assert harness.verified(evaluated)
    # Restore the durable boundary after check verdict persistence, before receipt attachment.
    journal.put("harness_evaluations", evaluation_id, "scope", {**evaluated.value, "tool_result": None}, expected_revision=evaluated.revision)
    with pytest.raises(CoreError, match="independently evaluated"):
        evolution.activate("scope", candidate.id, expected_generation=evolution.active("scope").id)
    assert evolution.recover("scope")[0].value["status"] == "shadowed"
    assert harness.get_evaluation("scope", evaluation_id).value["status"] == "interrupted"
    with pytest.raises(CoreError, match="eligible"):
        evolution.activate("scope", candidate.id, expected_generation=evolution.active("scope").id)
    retried = await evolution.evaluate("scope", candidate.id, phase="canary", work_id="work", generation=1)
    assert retried.value["status"] == "eligible" and retried.value["evaluations"]["canary"] != evaluation_id


def test_edit_validation_scopes_evidence_preimages_and_deletion(journal, tmp_path):
    source, harness, evolution, evidence = setup(journal, tmp_path)
    for path in ("", ".", "../escape", "/absolute", "nul\x00"):
        with pytest.raises(CoreError):
            SourceEdit(path, b"x")
    with pytest.raises(CoreError):
        SourceEdit("worker.py", "not bytes")
    with pytest.raises(CoreError):
        SourceEdit("worker.py", b"x", "bad hash")
    with pytest.raises(CoreError):
        evolution.stage("wrong", [SourceEdit("worker.py", DOUBLE)], rationale="update", evidence_event_ids=[evidence])
    for ids in ([], [evidence, evidence], ["missing"]):
        with pytest.raises(CoreError):
            evolution.stage("scope", [SourceEdit("worker.py", DOUBLE)], rationale="update", evidence_event_ids=ids)
    for edits in ([], [SourceEdit("worker.py", DOUBLE)], [SourceEdit("worker.py", DOUBLE), SourceEdit("worker.py", DOUBLE)], [SourceEdit("ungranted.py", b"x")]):
        with pytest.raises(CoreError):
            evolution.stage("scope", edits, rationale="update", evidence_event_ids=[evidence])
    with pytest.raises(CoreError, match="does not change"):
        stage(evolution, evidence, BASE)
    with pytest.raises(CoreError, match="Parent"):
        evolution.stage("scope", [SourceEdit("worker.py", DOUBLE, hashlib.sha256(BASE).hexdigest())], rationale="update", evidence_event_ids=[evidence], expected_parent="stale")
    row = evolution.stage("scope", [SourceEdit("unused.txt", None, hashlib.sha256(b"old").hexdigest()), SourceEdit("nested//added.py", b"value = 1")], rationale="remove obsolete file", evidence_event_ids=[evidence])
    snapshot = SourceSnapshot.from_dict(row.value["snapshot"])
    assert not Path(snapshot.path, "unused.txt").exists() and Path(snapshot.path, "nested/added.py").exists()
    changed_policy = replace(evolution.authority, allowed_paths=("worker.py",))
    changed = SelfEvolution(journal, tmp_path / "evolution", harness, authority=changed_policy)
    with pytest.raises(CoreError, match="different installation"):
        stage(changed, evidence)


def test_snapshot_bounds_and_copy_integrity(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "one.py").write_bytes(BASE)
    snap = source_snapshot(source)
    copied = copy_snapshot(snap, tmp_path / "copy")
    assert copied.sha256 == snap.sha256
    with pytest.raises(CoreError):
        copy_snapshot(snap, tmp_path / "copy")
    (source / "one.py").write_bytes(DOUBLE)
    with pytest.raises(CoreError):
        copy_snapshot(snap, tmp_path / "changed")
    with pytest.raises(CoreError):
        source_snapshot(source / "one.py")
    (source / "link").symlink_to(tmp_path)
    with pytest.raises(CoreError):
        source_snapshot(source)


@pytest.mark.parametrize("change", [{"phase": "unknown"}, {"name": ""}, {"argv": []}, {"timeout": 0}, {"stdin": "not bytes"}, {"expected_stdout": "not bytes"}, {"expected_exit": True}, {"output_limit": 0}])
def test_invalid_immutable_checks(change):
    with pytest.raises(CoreError):
        CheckSpec(**{"name": "check", "phase": "build", "argv": (sys.executable, "-c", "pass"), **change})


def test_harness_requires_real_checks_and_allowed_programs(journal, tmp_path):
    with pytest.raises(CoreError):
        MetaHarness(journal, tmp_path / "empty", make_dispatcher(journal), checks=())
    with pytest.raises(CoreError):
        MetaHarness(journal, tmp_path / "duplicate", make_dispatcher(journal), checks=(checks()[0], checks()[0]))
    with pytest.raises(CoreError):
        MetaHarness(journal, tmp_path / "programs", make_dispatcher(journal), checks=checks(), allowed_programs=("/usr/bin/true",))
