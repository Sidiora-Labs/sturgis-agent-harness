"""Reconciliation admits trusted causal observations of real uncertain effects."""
import asyncio
from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from agent.protocol import CoreError, ToolCall, ToolResult
from agent.scheduler import Scheduler
from agent.state import Journal
from agent.tool_runner import ToolDispatcher, WorkspaceFs, digest, file_executor


def observer_payload(attempt, *, usage=...):
    return {"schema_version": "tool-observation/1", "request_id": "request",
            "work_id": attempt.work.id, "request_event_id": attempt.request_event.id,
            "result": attempt.complete.as_dict(), "usage": attempt.resources if usage is ... else usage}


def observe(attempt, payload=None, *, scope="scope", kind="tool.observation", source="tool_observer"):
    return attempt.journal.append(scope, kind, observer_payload(attempt) if payload is None else payload, source=source)


def inspect_attempt(journal, scheduler, dispatcher, workspace):
    row = journal.get("tool_requests", "request", "scope")
    result = ToolResult(**row.value["result"])
    request = next(event for event in journal.events("scope", kinds=["tool.request"])
                   if event.payload["attempt_id"] == result.attempt_id)
    # An independent read supplies actual effect evidence; the discarded executor
    # acknowledgement is not used to assert that the file has the expected content.
    content = WorkspaceFs(workspace).read("effect.txt")
    assert content == b"actual effect"
    complete = ToolResult(result.action_id, result.attempt_id, result.tool, "completed", "success",
                          {"path": "effect.txt", "sha256": digest(content)},
                          effects={"certainty": "observed", "evidence_event_ids": []})
    return SimpleNamespace(journal=journal, scheduler=scheduler, dispatcher=dispatcher,
                           work=scheduler.get("scope", row.value["work_id"]), result=result,
                           complete=complete, request_event=request,
                           resources=row.value.get("observed_resources"))


@pytest.fixture
async def uncertain_effect(tmp_path):
    journal = Journal(tmp_path / "operations.sqlite")
    scheduler = Scheduler(journal, max_running=1)
    dispatcher = ToolDispatcher(journal, authorize=scheduler.authorize)
    fs = WorkspaceFs(tmp_path)
    executor = file_executor(fs)
    written, cancel = asyncio.Event(), asyncio.Event()

    async def lost_acknowledgement(arguments, context):
        await executor(arguments, context)
        written.set()
        await asyncio.Event().wait()

    lost_acknowledgement.supports_constraints = True
    dispatcher.register("file.write", lost_acknowledgement, input_schema={"type": "object"},
                        output_schema={"type": "object"}, required_capabilities=["workspace.write"], timeout=2)
    work = scheduler.submit("scope", "effect", {}, capabilities=["workspace.write"],
                            resources={"tool_calls": 1, "elapsed_ms": 2000})
    admitted = scheduler.admit("scope", work.id)
    pending = asyncio.create_task(dispatcher.invoke(ToolCall("file.write", {
        "operation": "write", "path": "effect.txt", "content": "actual effect"}),
        scope="scope", work_id=work.id, generation=admitted.value["generation"], request_id="request", cancel=cancel))
    try:
        await asyncio.wait_for(written.wait(), 2)
        cancel.set()
        result = await asyncio.wait_for(pending, 2)
        assert result.state == "indeterminate" and result.effects["certainty"] == "unknown"
        yield inspect_attempt(journal, scheduler, dispatcher, tmp_path)
    finally:
        cancel.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        journal.close()


def reject(attempt, evidence_ids, *, result=None, usage=None, code="invalid_evidence"):
    before = attempt.journal.get("tool_requests", "request", "scope")
    sequence = attempt.journal.events("scope")[-1].seq
    with pytest.raises(CoreError) as error:
        attempt.dispatcher.reconcile("scope", "request", attempt.complete if result is None else result,
                                     evidence_event_ids=evidence_ids, usage=usage)
    assert error.value.code == code
    assert attempt.journal.get("tool_requests", "request", "scope") == before
    assert attempt.journal.events("scope", after=sequence) == []
    assert attempt.journal.get("work_claims", attempt.result.attempt_id, "scope").value["state"] == "claimed"
    assert not attempt.journal.events("scope", kinds=["tool.usage", "action.reconciled"])


async def test_observed_real_effect_reconciles_exact_attempt_and_settles_attested_usage(uncertain_effect):
    a = uncertain_effect
    evidence = observe(a)
    admitted = a.dispatcher.reconcile("scope", "request", a.complete, evidence_event_ids=[evidence.id])
    assert admitted == replace(a.complete, effects={"certainty": "observed", "evidence_event_ids": [evidence.id]})
    stored = a.journal.get("tool_requests", "request", "scope")
    assert stored.value["reconciliation"]["prior_result"] == a.result.as_dict()
    assert stored.value["result"] == admitted.as_dict()
    a.scheduler.reconcile_usage("scope")
    claim = a.journal.get("work_claims", a.result.attempt_id, "scope")
    assert claim.value["state"] == "settled" and claim.value["actual"] == a.resources
    assert a.scheduler.get("scope", a.work.id).value["usage"] == a.resources
    history = a.journal.events("scope", kinds=["tool.result"])
    assert [event.payload["state"] for event in history] == ["indeterminate", "completed"]


@pytest.mark.parametrize("source,kind", [
    ("model", "tool.observation"), ("memory", "tool.observation"),
    ("memory_sleep", "tool.observation"), ("subagents", "tool.observation"),
    ("runtime", "tool.observation"), ("tool_runner", "tool.observation"),
    ("delivery", "output.persisted"), ("subagents", "child.terminal"),
    ("tool_observer", "external.observation"), ("tool_observer", "tool.result"),
])
async def test_narrative_or_untrusted_source_cannot_authorize_completion(uncertain_effect, source, kind):
    a = uncertain_effect
    evidence = observe(a, source=source, kind=kind)
    reject(a, [evidence.id])


@pytest.mark.parametrize("field,value", [
    ("action_id", "another-action"), ("attempt_id", "another-attempt"),
    ("tool", "different.tool"), ("state", "cancelled"), ("outcome", "target_error"),
    ("data", {"path": "another-file"}), ("error", {"code": "different"}),
    ("completeness", {"observation_complete": False, "truncated": True, "missing_ranges": [[0, 10]]}),
    ("effects", {"certainty": "unknown", "evidence_event_ids": []}),
    ("effects", {"certainty": "observed", "evidence_event_ids": ["unrelated"]}),
])
async def test_observation_must_attest_every_proposed_result_field(uncertain_effect, field, value):
    a = uncertain_effect
    payload = observer_payload(a)
    payload["result"][field] = value
    reject(a, [observe(a, payload).id])


@pytest.mark.parametrize("field,value", [
    ("schema_version", "tool-observation/0"), ("request_id", "another-request"),
    ("work_id", "another-work"), ("request_event_id", "missing"),
    ("request_event_id", None), ("usage", None), ("extra", "narrative"),
])
async def test_observation_envelope_and_usage_are_exact(uncertain_effect, field, value):
    a = uncertain_effect
    payload = observer_payload(a)
    payload[field] = value
    reject(a, [observe(a, payload).id])


async def test_unrelated_causal_event_and_cross_scope_observation_are_rejected(uncertain_effect):
    a = uncertain_effect
    unrelated = a.journal.append("scope", "external.observation", {"job": "terminated"}, source="runtime")
    reject(a, [unrelated.id])
    reject(a, [observe(a, scope="other").id])
    for changes in ({"attempt_id": "other-attempt"}, {"call": {"tool": "other.tool"}}):
        request = a.journal.append("scope", "tool.request", {**a.request_event.payload, **changes}, source="tool_runner")
        payload = observer_payload(a)
        payload["request_event_id"] = request.id
        reject(a, [observe(a, payload).id])
    for source, kind in (("model", "tool.request"), ("tool_runner", "tool.result")):
        request = a.journal.append("scope", kind, a.request_event.payload, source=source)
        payload = observer_payload(a)
        payload["request_event_id"] = request.id
        reject(a, [observe(a, payload).id])


async def test_mixed_missing_duplicate_and_malformed_evidence_reject_atomically(uncertain_effect):
    a = uncertain_effect
    valid = observe(a)
    invalid = observe(a, source="memory")
    for identifiers in ([valid.id, invalid.id], [valid.id, "missing"], [valid.id, valid.id], [],
                        "not-a-list", [1], [""], ["x" * 257], [str(i) for i in range(65)]):
        reject(a, identifiers)
    reject(a, [valid.id], result=replace(a.complete, action_id="other"))
    reject(a, [valid.id], result=ToolResult(a.result.action_id, a.result.attempt_id, a.result.tool, "running", None))
    reject(a, [valid.id], result=replace(a.complete, effects={"certainty": "observed", "evidence_event_ids": [invalid.id]}))
    reject(a, [valid.id], result=replace(a.complete, effects={"certainty": "observed", "evidence_event_ids": [], "extra": True}))
    reject(a, [valid.id], result={"not": "a ToolResult"})


async def test_unattested_or_invalid_usage_cannot_settle_the_attempt(uncertain_effect):
    a = uncertain_effect
    evidence = observe(a)
    reject(a, [evidence.id], usage={"tool_calls": 1, "elapsed_ms": a.resources["elapsed_ms"] + 1})
    for resources in ([], {"tool_calls": 1}, {"tool_calls": 2, "elapsed_ms": 1},
                      {"tool_calls": True, "elapsed_ms": 1}, {"tool_calls": 1, "elapsed_ms": -1}):
        reject(a, [evidence.id], usage=resources, code="invalid_usage")


async def test_terminal_observation_can_preserve_unknown_effect_certainty(uncertain_effect):
    a = uncertain_effect
    terminal = replace(a.complete, outcome="target_error", data={"exit_code": 2},
                       effects={"certainty": "unknown", "evidence_event_ids": []})
    payload = observer_payload(a)
    payload["result"] = terminal.as_dict()
    evidence = observe(a, payload)
    admitted = a.dispatcher.reconcile("scope", "request", terminal, evidence_event_ids=(evidence.id,), usage=a.resources)
    assert admitted.outcome == "target_error" and admitted.effects["certainty"] == "unknown"
    assert admitted.effects["evidence_event_ids"] == [evidence.id]


CRASH_AFTER_EFFECT = r'''
import asyncio
import os
import sys
from pathlib import Path
from agent.protocol import ToolCall
from agent.scheduler import Scheduler
from agent.state import Journal
from agent.tool_runner import ToolDispatcher, WorkspaceFs, file_executor

async def main():
    root = Path(sys.argv[1])
    journal = Journal(root / "operations.sqlite")
    scheduler = Scheduler(journal)
    dispatcher = ToolDispatcher(journal, authorize=scheduler.authorize)
    execute = file_executor(WorkspaceFs(root))
    async def crash(arguments, context):
        await execute(arguments, context)
        os._exit(23)
    crash.supports_constraints = True
    dispatcher.register("file.write", crash, input_schema={"type": "object"}, output_schema={"type": "object"},
                        required_capabilities=["workspace.write"], timeout=2)
    work = scheduler.submit("scope", "effect", {}, capabilities=["workspace.write"],
                            resources={"tool_calls": 1, "elapsed_ms": 2000})
    admitted = scheduler.admit("scope", work.id)
    await dispatcher.invoke(ToolCall("file.write", {"operation": "write", "path": "effect.txt", "content": "actual effect"}),
        scope="scope", work_id=work.id, generation=admitted.value["generation"], request_id="request")

asyncio.run(main())
'''


async def test_observed_effect_after_actual_owner_crash_keeps_unmeasured_usage_unsettled(tmp_path):
    child = await asyncio.create_subprocess_exec(sys.executable, "-c", CRASH_AFTER_EFFECT, str(tmp_path),
        cwd=str(Path(__file__).resolve().parents[2]), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        _, errors = await asyncio.wait_for(child.communicate(), 5)
        assert child.returncode == 23, errors.decode()
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()
    journal = Journal(tmp_path / "operations.sqlite")
    try:
        scheduler = Scheduler(journal)
        dispatcher = ToolDispatcher(journal, authorize=scheduler.authorize)
        assert dispatcher.recover("scope")[0].state == "indeterminate"
        a = inspect_attempt(journal, scheduler, dispatcher, tmp_path)
        assert a.resources is None
        evidence = observe(a)
        admitted = dispatcher.reconcile("scope", "request", a.complete, evidence_event_ids=[evidence.id])
        assert admitted.state == "completed" and admitted.effects["certainty"] == "observed"
        scheduler.reconcile_usage("scope")
        assert journal.get("work_claims", a.result.attempt_id, "scope").value["state"] == "claimed"
        assert not journal.events("scope", kinds=["tool.usage"])
        assert journal.get("tool_requests", "request", "scope").value["reconciliation"]["prior_result"]["state"] == "indeterminate"
    finally:
        journal.close()
