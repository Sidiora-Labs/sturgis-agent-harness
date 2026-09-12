"""Consumer commands drive the real work, authority and confined extension owners."""
import asyncio
import base64
import hashlib
import hmac
import time
from dataclasses import asdict

import pytest

from agent.protocol import CoreError, ToolCall, canonical_json
from consumer.application import AgentApplication
from consumer.device import device_signing_bytes
from tests.consumer.test_application import configuration
from tests.consumer.test_identity import error
from tests.consumer_integration.test_application import application
from tests.core.test_capabilities import manifest, package
from tests.integration.test_continuity import eventually, proposal


BUDGET = {"tokens": 200000, "tool_calls": 10, "elapsed_ms": 600000, "iterations": 16}


@pytest.fixture
async def app(tmp_path):
    instance = AgentApplication(configuration(tmp_path))
    await instance.start()
    try:
        yield instance
    finally:
        await instance.close()


def admit(app, resources=None):
    row = app.core.scheduler.submit(app.scope, "consumer-domain-test", {},
        capabilities=app.core.capabilities, resources=resources or BUDGET)
    return app.core.scheduler.admit(app.scope, row.id, eligible_ids=[row.id])


async def invoke(app, work, call):
    return await app.core.tools.invoke(call, scope=app.scope, work_id=work.id,
        generation=work.value["generation"])


def signed(secret, action, revision, nonce, body=b""):
    timestamp = int(time.time())
    signature = hmac.new(secret.encode(), device_signing_bytes(action, "device", revision,
        timestamp, nonce, body), hashlib.sha256).hexdigest()
    return {"connection_id": "device", "timestamp": timestamp, "nonce": nonce, "signature": signature}


async def test_domain_allowlists_and_artifact_ranges_are_scoped(app):
    assert {"work", "goals", "commitments", "schedules", "workflows", "children", "inbox"} == set(
        await app.command("work.overview", {}))
    for method, params in (
        ("permissions.set", {"tool": "workspace.write", "mode": "allow", "reviewer": "owner"}),
        ("permissions.approve", {"event_id": "missing", "reviewer": "owner"}),
        ("plugins.activate", {"id": "missing", "expected_revision": 1, "operation": "uninstall"}),
        ("goals.create", {"objective": "Injected scope", "scope": "another-agent"}),
        ("devices.list", {"root": "/tmp"}),
    ):
        with error("invalid_request"):
            await app.command(method, params)
    body = b"<script>untrusted source</script>\x00exact bytes"
    artifact = app.core.artifacts.put(app.scope, body, "text/html")
    full = await app.command("artifacts.read", {"id": artifact.id})
    assert base64.b64decode(full["data_base64"]) == body and full["complete"]
    assert full["sha256"] == hashlib.sha256(body).hexdigest()
    part = await app.command("artifacts.read", {"id": artifact.id, "offset": 8, "limit": 7})
    assert base64.b64decode(part["data_base64"]) == body[8:15]
    assert (part["start"], part["end"], part["total_bytes"], part["complete"]) == (8, 15, len(body), False)
    end = await app.command("artifacts.read", {"id": artifact.id, "offset": len(body)})
    assert end["data_base64"] == "" and not end["complete"]
    for bounds in ({"offset": -1}, {"offset": True}, {"offset": len(body) + 1},
                   {"limit": 0}, {"limit": True}, {"limit": 4 * 1024 * 1024 + 1}):
        with error("invalid_content"):
            await app.command("artifacts.read", {"id": artifact.id, **bounds})
    foreign = app.core.artifacts.put("another-agent", b"private source")
    with error("not_found"):
        await app.command("artifacts.read", {"id": foreign.id})
    diagnostic = await app.command("diagnostics.get", {})
    assert diagnostic["scope"] == app.scope and diagnostic["mode"] == "self_hosted"
    assert diagnostic["capabilities"] == list(app.core.capabilities)
    assert not diagnostic["paused"]


async def test_goal_commitment_wait_and_schedule_commands_use_current_revisions(app):
    room = await app.command("conversations.create", {"title": "Responsibilities"})
    goal = await app.command("goals.create", {"objective": "Prepare report", "limits": BUDGET})
    goal = await app.command("goals.edit", {"id": goal["id"], "expected_revision": goal["revision"],
        "objective": "Prepare sourced report"})
    with error("conflict"):
        await app.command("goals.edit", {"id": goal["id"], "expected_revision": 1, "objective": "stale"})
    goal = await app.command("goals.transition", {"id": goal["id"], "state": "active", "expected_revision": goal["revision"]})
    scheduled = await app.command("schedules.create", {"conversation_id": room["id"], "text": "Prepare report",
        "spec": {"kind": "once", "at": time.time() + 3600}, "resources": BUDGET, "goal_id": goal["id"]})
    goal = next(row for row in await app.command("goals.list", {}) if row["id"] == goal["id"])
    goal = await app.command("goals.transition", {"id": goal["id"], "state": "paused", "expected_revision": goal["revision"]})
    paused = (await app.command("schedules.list", {}))[0]
    assert paused["id"] == scheduled["id"] and paused["state"] == "paused"
    cancelled = await app.command("schedules.state", {"id": paused["id"], "state": "cancelled", "expected_revision": paused["revision"]})
    assert cancelled["state"] == "cancelled"
    goal = await app.command("goals.transition", {"id": goal["id"], "state": "abandoned", "expected_revision": goal["revision"], "reason": "Owner changed priority"})
    assert (await app.command("goals.archive", {"id": goal["id"], "expected_revision": goal["revision"]}))["archived"]
    commitment = await app.command("commitments.create", {"description": "Return when the timer fires"})
    commitment = await app.command("commitments.transition", {"id": commitment["id"], "state": "accepted", "expected_revision": commitment["revision"]})
    commitment = await app.command("commitments.progress", {"id": commitment["id"], "progress": "blocked", "reason": "Waiting for the agreed time", "expected_revision": commitment["revision"]})
    waiting = await app.command("commitments.wait", {"id": commitment["id"], "trigger": {"kind": "time", "at": time.time() + .1}, "expected_revision": commitment["revision"]})
    assert waiting["progress"] == "waiting" and waiting["wait"]["release_compute"]
    await eventually(lambda: app.journal.get("waits", waiting["wait_id"], app.scope).value["state"] == "fired")
    resumed = await app.command("commitments.resume", {"id": commitment["id"], "expected_revision": waiting["revision"]})
    assert resumed["progress"] == "in_progress" and resumed["wait_id"] is None
    cancelled = await app.command("commitments.transition", {"id": commitment["id"], "state": "cancelled", "expected_revision": resumed["revision"], "reason": "Owner released responsibility"})
    assert (await app.command("commitments.list", {}))[0]["state"] == cancelled["state"] == "cancelled"


async def test_permission_commands_project_rejection_and_consume_exact_future_grant(app):
    work = admit(app)
    call = ToolCall("workspace.write", {"operation": "write", "path": "approved.txt",
        "data_base64": base64.b64encode(b"Observed authorized effect").decode()})
    denied = await invoke(app, work, call)
    assert denied.error["code"] == "approval_required"
    assert not (app.config.workspace / "approved.txt").exists()
    request = (await app.command("permissions.pending", {}))[0]
    assert (await app.command("permissions.request", {"event_id": request["id"]}))["call"] == call.as_dict()
    approval = await app.command("permissions.approve", {"event_id": request["id"]})
    assert approval["state"] == "approved" and not (app.config.workspace / "approved.txt").exists()
    allowed = await invoke(app, work, call)
    assert allowed.outcome == "success" and allowed.effects["certainty"] == "observed"
    assert (app.config.workspace / "approved.txt").read_bytes() == b"Observed authorized effect"
    assert (await app.command("permissions.request", {"event_id": request["id"]}))["state"] == "consumed"
    assert (await invoke(app, work, call)).error["code"] == "approval_required"
    next_request = (await app.command("permissions.pending", {}))[0]
    assert (await app.command("permissions.deny", {"event_id": next_request["id"]}))["state"] == "denied"
    policy = await app.command("permissions.set", {"tool": "workspace.write", "mode": "deny"})
    assert work.id in policy["fenced"]["work_ids"]
    assert "workspace.write" not in app.core.capabilities
    assert (await app.command("work.get", {"id": work.id}))["state"] == "cancelled"
    assert any(row["id"] == "workspace.write" and row["mode"] == "deny" for row in await app.command("permissions.list", {}))
    assert work.id in (await app.command("work.cancel", {"id": work.id}))["work_ids"]


async def test_workflow_commands_execute_dependencies_and_preserve_evidence(tmp_path):
    text = base64.b64encode(b"sourced result").decode()
    scripts = [[proposal("workspace.write", {"operation": "write", "path": "report.txt", "data_base64": text})],
        ["Recorded file effect"], ["Dependency report complete"]]
    async with application(tmp_path, scripts) as (app, model):
        await app.command("permissions.set", {"tool": "workspace.write", "mode": "allow"})
        room = await app.command("conversations.create", {"title": "Workflow"})
        plan = await app.command("workflows.create", {"steps": [
            {"id": "write", "description": "Write report.txt", "checks": [{"id": "file", "event_kind": "tool.effect", "source": "tool_runner", "matches": {"data.path": "report.txt"}}]},
            {"id": "report", "description": "Report the file", "dependencies": ["write"]}], "budget": BUDGET})
        await app.command("workflows.run", {"id": plan["id"], "conversation_id": room["id"], "expected_revision": plan["revision"]})
        await eventually(lambda: app.core.planner.get(app.scope, plan["id"]).value["state"] == "completed", timeout=12)
        final = await app.command("workflows.get", {"id": plan["id"]})
        assert final["completion_basis"] == {"write": "outcome_checks", "report": "model_turn"}
        assert len(final["execution"]["attempts"]) == 2 and len(model.requests) == 4
        assert final["steps"][0]["evidence"]["file"]
        assert (app.config.workspace / "report.txt").read_bytes() == b"sourced result"
        assert (await app.command("workflows.list", {}))[0]["id"] == plan["id"]
        work_ids = {r["id"] for r in await app.command("work.list", {})}
        assert all(attempt["work_id"] in work_ids for attempt in final["execution"]["attempts"])
        with error("invalid_transition"):
            await app.command("workflows.step", {"id": plan["id"], "step_id": "report", "state": "completed", "expected_revision": final["revision"]})


async def test_attention_inbox_and_workflow_revision_commands(app):
    policy = await app.command("attention.policy", {"policy": {"mode": "suggest", "minimum_score": 0}})
    assert policy["revision"] == 1
    source = app.journal.append(app.scope, "external.reminder", {"text": "Review the report"}, source="test_device")
    candidate = await app.command("initiative.propose", {"source_event_id": source.id, "action": "Review the report",
        "signals": {"urgency": 1, "expected_value": 1, "confidence": 1, "interruption_cost": 0, "resource_cost": 0, "duplication_penalty": 0},
        "expires_at": time.time() + 60, "dedupe_key": "report-reminder"})
    await eventually(lambda: app.journal.get("consumer.inbox", candidate["id"], app.scope))
    inbox = await app.command("inbox.list", {})
    assert inbox[0]["candidate"]["source_event_id"] == source.id and inbox[0]["decision"] == "pending"
    assert (await app.command("inbox.decide", {"id": candidate["id"], "decision": "dismiss"}))["decision"] == "dismissed"
    assert not await app.command("schedules.list", {})
    plan = await app.command("workflows.create", {"steps": [{"id": "review", "description": "Review", "assignee": {"kind": "user"}}], "budget": BUDGET})
    plan = await app.command("workflows.revise", {"id": plan["id"], "steps": [{"id": "review", "description": "Review carefully", "assignee": {"kind": "user"}}], "expected_revision": plan["revision"], "reason": "Clarify review"})
    plan = await app.command("workflows.step", {"id": plan["id"], "step_id": "review", "state": "in_progress", "expected_revision": plan["revision"]})
    assert plan["steps"][0]["state"] == "in_progress"
    plan = await app.command("workflows.state", {"id": plan["id"], "state": "paused", "expected_revision": plan["revision"], "reason": "Owner paused"})
    assert plan["state"] == "paused"


async def test_child_commands_reject_unsupported_outcomes_and_cancel_active_child(tmp_path):
    release = asyncio.Event()
    async with application(tmp_path, [["Child completed"], ["Child started", release]]) as (app, model):
        parent = admit(app, {**BUDGET, "tokens": 400000})
        params = {"parent_work_id": parent.id, "objective": "Read and report", "capabilities": [],
            "resources": {"tokens": 100000, "elapsed_ms": 60000, "iterations": 4}, "workspace_mode": "isolated"}
        child = await app.command("children.delegate", params)
        await app.core.wait(child["work_id"], timeout=5)
        result = await app.command("children.get", {"id": child["id"]})
        assert result["state"] == "cancelled" and result["result"]["verdict"] == "rejected"
        turn = app.journal.get("runtime_turns", child["work_id"], app.scope)
        assert turn.value["error"]["code"] == "child_evidence_incomplete"
        assert result["workspace_root"] != str(app.config.workspace)
        second = await app.command("children.delegate", {**params, "objective": "Wait for further direction"})
        await eventually(lambda: len(model.requests) == 3)
        cancelled = await app.command("children.cancel", {"id": second["id"]})
        assert cancelled["state"] == "cancelled"
        assert {row["id"] for row in await app.command("children.list", {})} == {child["id"], second["id"]}
        release.set()


async def test_device_commands_pair_sign_execute_and_fence_revocation(app):
    configured = await app.command("connections.configure", {"id": "device", "kind": "device", "endpoint": "https://device.invalid",
        "options": {"device_id": "test-device", "operations": {"read": {"type": "object", "additionalProperties": False}}}})
    assert not (await app.command("devices.get", {"connection_id": "device"}))["paired"]
    pair = await app.command("devices.pair", {"connection_id": "device", "expected_revision": configured["revision"]})
    claim = await app.command("ingress.device.claim", {"connection_id": "device", "pairing_token": pair["pairing_token"], "device_id": "test-device"})
    with error("pairing_denied"):
        await app.command("ingress.device.claim", {"connection_id": "device", "pairing_token": pair["pairing_token"], "device_id": "test-device"})
    configured = await app.command("connections.grant", {"id": "device", "operations": ["request"], "expected_revision": claim["revision"]})
    configured = await app.command("connections.probe", {"id": "device", "expected_revision": configured["revision"]})
    assert configured["status"] == "ready" and not configured["probe"]["actuator_verified"]
    listed = await app.command("devices.list", {})
    assert listed[0]["paired"] and claim["secret"] not in canonical_json(listed)
    assert pair["pairing_token"] not in canonical_json([asdict(event) for event in app.journal.events(app.scope)])
    sync = await app.command("ingress.device.sync", signed(claim["secret"], "sync", 0, "sync-one"))
    assert sync["revision"] == configured["revision"]
    await app.command("permissions.set", {"tool": "connection.device.request", "mode": "allow"})
    work = admit(app)
    pending = asyncio.create_task(invoke(app, work, ToolCall("connection.device.request", {"operation": "read", "arguments": {}}, tool_version=str(configured["revision"]))))
    try:
        await eventually(lambda: app.journal.list("consumer.device_requests", app.scope))
        polled = await app.command("ingress.device.poll", signed(claim["secret"], "poll", configured["revision"], "poll-one"))
        request = polled["requests"][0]
        assert request["operation"] == "read" and request["state"] == "offered"
        response = canonical_json({"request_id": request["request_id"], "status": "success", "data": {"reading": 42}}).encode()
        receipt = await app.command("ingress.device.respond", {**signed(claim["secret"], "respond", configured["revision"], "respond-one", response), "body_base64": base64.b64encode(response).decode()})
        observed = await asyncio.wait_for(pending, 3)
        assert observed.outcome == "success" and observed.data["evidence_event_id"] == receipt["event_id"]
        assert observed.data["trust"] == "external_evidence" and observed.effects["certainty"] == "unknown"
        revoked = await app.command("devices.revoke", {"connection_id": "device", "expected_revision": configured["revision"]})
        assert not revoked["active"] and "connection:device:request" not in app.core.capabilities
        assert (await app.command("work.get", {"id": work.id}))["state"] == "cancelled"
        with error("stale_connection"):
            await app.command("ingress.device.poll", signed(claim["secret"], "poll", configured["revision"], "poll-revoked"))
    finally:
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


async def test_plugin_commands_run_confined_versions_health_rollback_and_uninstall(app, tmp_path):
    app.core.plugins.install(app.scope, package(tmp_path), manifest())
    installed = (await app.command("plugins.list", {}))[0]
    assert installed["status"] == "installed"
    assert all("path" not in value for value in installed["versions"].values())
    assert str(app.config.root) not in canonical_json(installed)
    with error("inactive"):
        await app.command("plugins.health", {"id": "example"})
    active = await app.command("plugins.activate", {"id": "example", "expected_revision": installed["revision"]})
    assert active["active_version"] == "1" and active["status"] == "active"
    assert (await app.command("plugins.health", {"id": "example"}))["value"] == 0
    await app.command("permissions.set", {"tool": "plugin.example.tool.count", "mode": "allow"})
    work = admit(app)
    counted = await invoke(app, work, ToolCall("plugin.example.tool.count", {}))
    assert counted.outcome == "success" and counted.data["value"] == 1
    staged = app.core.plugins.install(app.scope, package(tmp_path, "2", schema=2), manifest("2", state_schema=2, accepts_state_schemas=[1]))
    active2 = await app.command("plugins.activate", {"id": "example", "expected_revision": staged.revision})
    assert active2["active_version"] == "2" and active2["state"]["counter"] == 10
    assert ("plugin.example.tool.count", "1") not in app.core.tools.registry
    with error("conflict"):
        await app.command("plugins.disable", {"id": "example", "expected_revision": staged.revision})
    rolled = await app.command("plugins.rollback", {"id": "example", "expected_revision": active2["revision"]})
    assert rolled["active_version"] == "1" and rolled["state"]["counter"] == 1
    with error("invalid_state"):
        await app.command("plugins.uninstall", {"id": "example", "expected_revision": rolled["revision"]})
    disabled = await app.command("plugins.disable", {"id": "example", "expected_revision": rolled["revision"]})
    assert disabled["status"] == "disabled"
    assert not any(key[0].startswith("plugin.example.") for key in app.core.tools.registry)
    removed = await app.command("plugins.uninstall", {"id": "example", "expected_revision": disabled["revision"]})
    assert removed["status"] == "uninstalled" and removed["active_version"] is None


async def test_plugin_concurrent_same_revision_mutations_have_one_winner(app, tmp_path):
    installed = app.core.plugins.install(app.scope, package(tmp_path), manifest())
    results = await asyncio.gather(*[app.command("plugins.activate", {"id": "example", "expected_revision": installed.revision}) for _ in range(2)], return_exceptions=True)
    successes = [value for value in results if isinstance(value, dict)]
    rejected = [value for value in results if isinstance(value, CoreError)]
    assert len(successes) == len(rejected) == 1 and rejected[0].code == "conflict"
    current = await app.command("plugins.inspect", {"id": "example"})
    assert current["revision"] == successes[0]["revision"] and current["status"] == "active"
    assert len([key for key in app.core.tools.registry if key[0].startswith("plugin.example.")]) == 2


async def test_plugin_revision_types_cannot_alias_integer_cas(app, tmp_path):
    installed = app.core.plugins.install(app.scope, package(tmp_path), manifest())
    for value in (True, False, 1.0, "1", -1, None):
        with pytest.raises(CoreError):
            await app.command("plugins.activate", {"id": "example", "expected_revision": value})
        assert app.core.plugins.inspect(app.scope, "example").revision == installed.revision
