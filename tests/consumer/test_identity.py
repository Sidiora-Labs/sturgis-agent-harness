"""Real identity storage, encryption, provider wire I/O and usage qualification."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import sqlite3
import threading
import time

import pytest

from agent.protocol import CoreError, ToolCall, canonical_json
from agent.runtime import CoreRuntime
from agent.state import Journal
from consumer.identity import Accounts, AgentDirectory, Principal, CONTROL, USERS, SESSIONS
from consumer.network import NetworkPolicy
from consumer.providers import ProviderSettings, PROBES
from consumer.usage import UsageView
from consumer.vault import CredentialVault

PASSWORD = "a secure test password"
BOOTSTRAP = "operator-owned-bootstrap-token"


@pytest.fixture
def journal(tmp_path):
    value = Journal(tmp_path / "accounts.sqlite")
    yield value
    value.close()


@pytest.fixture
def accounts(journal):
    return Accounts(journal, bootstrap_token=BOOTSTRAP, allow_signup=True)


def owner(accounts):
    result = accounts.bootstrap("Owner@Example.test", PASSWORD, BOOTSTRAP)
    return accounts.authenticate(result["token"]), result


def error(code):
    return pytest.raises(CoreError, match=".") if code is None else _Error(code)


class _Error:
    def __init__(self, code):
        self.code = code

    def __enter__(self):
        self.context = pytest.raises(CoreError)
        self.caught = self.context.__enter__()
        return self.caught

    def __exit__(self, *args):
        result = self.context.__exit__(*args)
        assert self.caught.value.code == self.code
        return result


def test_bootstrap_login_persistence_and_secret_projection(accounts, journal, tmp_path):
    assert accounts.status() == {"bootstrap_required": True, "allow_signup": True}
    with error("bootstrap_required"):
        accounts.signup("other@example.test", PASSWORD)
    with error("unauthenticated"):
        accounts.bootstrap("owner@example.test", PASSWORD, "wrong")
    principal, minted = owner(accounts)
    assert principal.email == "owner@example.test" and principal.admin
    assert not accounts.status()["bootstrap_required"]
    with error("bootstrap_closed"):
        accounts.bootstrap("other@example.test", PASSWORD, BOOTSTRAP)
    logged = accounts.login("OWNER@example.test", PASSWORD)
    assert logged["token"] != minted["token"]
    accounts.validate_csrf(principal, minted["csrf_token"])
    for csrf in ("bad", None, "x" * 257):
        with error("csrf_failed"):
            accounts.validate_csrf(principal, csrf)
    view = AgentDirectory(journal).create(principal, " My Agent ")
    assert view["name"] == "My Agent"
    contents = canonical_json([r.value for ns in (USERS, SESSIONS, "consumer.agents") for r in journal.list(ns, CONTROL)])
    for secret in (PASSWORD, BOOTSTRAP, minted["token"], minted["csrf_token"]):
        assert secret not in contents
    other = Journal(tmp_path / "accounts.sqlite")
    try:
        assert Accounts(other, bootstrap_token=BOOTSTRAP).authenticate(minted["token"]) == principal
    finally:
        other.close()
    assert accounts.logout(principal) == {"logged_out": True}
    with error("unauthenticated"):
        accounts.authenticate(minted["token"])
    # Revoking one session does not silently revoke a separately minted session.
    assert accounts.authenticate(logged["token"]).id == principal.id


def test_owner_isolation_and_administrator_cannot_read_private_agents(accounts, journal):
    admin, _ = owner(accounts)
    minted = accounts.signup("person@example.test", PASSWORD)
    user = accounts.authenticate(minted["token"])
    directory = AgentDirectory(journal)
    a = directory.create(admin, "One")
    b = directory.create(user, "Two")
    assert [row["id"] for row in directory.list(admin)] == [a["id"]]
    for principal, target in ((admin, b), (user, a)):
        with error("not_found"):
            directory.authorize(principal, target["id"])
    with error("forbidden"):
        accounts.disable(replace(user, admin=True), admin.id, 1)
    with error("last_administrator"):
        accounts.disable(admin, admin.id, 1)
    with error("not_found"):
        accounts.disable(admin, "missing", 1)
    with error("conflict"):
        accounts.disable(admin, user.id, 0)
    assert accounts.disable(admin, user.id, 1)["enabled"] is False
    with error("unauthenticated"):
        accounts.authenticate(minted["token"])
    with error("unauthenticated"):
        directory.create(user, "Denied")
    with error("unauthenticated"):
        accounts.login("person@example.test", PASSWORD)


def test_directory_revisions_archive_and_invalid_inputs(accounts, journal):
    principal, _ = owner(accounts)
    directory = AgentDirectory(journal)
    for name in (None, " ", "x" * 121, "bad\nname"):
        with error("invalid_name"):
            directory.create(principal, name)
    with error("unauthenticated"):
        directory.create({}, "Agent")
    row = directory.create(principal, "Initial")
    with error("conflict"):
        directory.rename(principal, row["id"], "Changed", 0)
    row = directory.rename(principal, row["id"], "Changed", 1)
    with error("invalid_permission"):
        directory.authorize(principal, row["id"], "root")
    assert directory.archive(principal, row["id"], row["revision"])["archived"]
    assert not directory.list(principal)
    assert directory.list(principal, include_archived=True)[0]["archived"]
    assert directory.authorize(principal, row["id"])["archived"]
    with error("agent_archived"):
        directory.rename(principal, row["id"], "No", 3)


@pytest.mark.parametrize("kwargs", [{"bootstrap_token": "short"}, {"bootstrap_token": BOOTSTRAP, "session_ttl": 0}, {"bootstrap_token": BOOTSTRAP, "allow_signup": 1}])
def test_account_configuration_rejected(journal, kwargs):
    with error("invalid_configuration"):
        Accounts(journal, **kwargs)


def test_account_input_validation_throttling_and_expiry(accounts, journal):
    for email in (None, "no-address", "a\n@example.test", "x" * 255):
        with error("invalid_account"):
            accounts.bootstrap(email, PASSWORD, BOOTSTRAP)
    for password in (None, "short", "x" * 1025, "password\0withnull"):
        with error("invalid_password"):
            accounts.bootstrap("a@example.test", password, BOOTSTRAP)
    principal, minted = owner(accounts)
    with error("account_unavailable"):
        accounts.signup("Owner@example.test", PASSWORD)
    with error("signup_disabled"):
        Accounts(journal, bootstrap_token=BOOTSTRAP).signup("new@example.test", PASSWORD)
    for password in (None, "x" * 1025):
        with error("unauthenticated"):
            accounts.login(principal.email, password)
    for _ in range(10):
        with error("unauthenticated"):
            accounts.login("unknown@example.test", "incorrect")
    with error("rate_limited"):
        accounts.login("unknown@example.test", "incorrect")
    for token in (None, "short", "x" * 257, "x" * 40):
        with error("unauthenticated"):
            accounts.authenticate(token)
    row = journal.get(SESSIONS, principal.session_id, CONTROL)
    journal.put(SESSIONS, row.id, CONTROL, {**row.value, "expires_at": time.time() - 1}, row.revision)
    with error("unauthenticated"):
        accounts.authenticate(minted["token"])
    with error("unauthenticated"):
        AgentDirectory(journal).list(Principal(principal.id, "", True, "unknown"))


def test_vault_rotation_persistence_scope_and_revocation(tmp_path):
    root = tmp_path / "vault"
    vault = CredentialVault(root)
    first = vault.put("agent-one", "provider", "private-provider-key")
    assert vault.get("agent-one", "provider") == first
    assert vault.resolve("agent-one", first["reference"]) == "private-provider-key"
    assert vault.list("agent-one") == [first]
    assert vault.list("other") == [] and vault.get("other", "provider") is None
    assert not vault.current("other", first["reference"])
    with error("credential_unavailable"):
        vault.resolve("other", first["reference"])
    with error("conflict"):
        vault.put("agent-one", "provider", "incorrect-overwrite")
    second = vault.put("agent-one", "provider", "replacement-key", 1)
    assert second["reference"] != first["reference"]
    assert not vault.current("agent-one", first["reference"])
    restarted = CredentialVault(root)
    assert restarted.resolve("agent-one", second["reference"]) == "replacement-key"
    for path in root.iterdir():
        assert path.stat().st_mode & 0o077 == 0
        assert b"private-provider-key" not in path.read_bytes()
        assert b"replacement-key" not in path.read_bytes()
    with error("conflict"):
        vault.revoke("agent-one", "provider", 1)
    revoked = vault.revoke("agent-one", "provider", 2)
    assert not revoked["active"] and revoked["revision"] == 3
    with error("credential_unavailable"):
        vault.resolve("agent-one", second["reference"])
    with error("not_found"):
        vault.revoke("agent-one", "missing", 0)
    for secret in (None, "", "x" * 65537, "contains\0null"):
        with error("invalid_credential"):
            vault.put("one", "name", secret)
    for revision in (-1, False, "0"):
        with error("invalid_revision"):
            vault.put("one", "name", "value", revision)


def test_vault_ciphertext_integrity_and_compare_swap(tmp_path):
    vault = CredentialVault(tmp_path / "vault")
    first = vault.put("a", "key", "first")
    def rotate(secret):
        try:
            return vault.put("a", "key", secret, 1)
        except CoreError as exc:
            return exc.code
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(rotate, ["second", "third"]))
    assert sum(result == "conflict" for result in results) == 1
    current = vault.get("a", "key")
    with sqlite3.connect(vault.root / "credentials.sqlite") as db:
        db.execute("UPDATE credentials SET ciphertext=?", (b"tampered",))
    with error("credential_corrupt"):
        vault.resolve("a", current["reference"])
    assert not vault.current("a", first["reference"])


def test_vault_private_paths_and_key_validation(tmp_path):
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    with error("unsafe_vault"):
        CredentialVault(public)
    linked = tmp_path / "linked"
    linked.symlink_to(public, target_is_directory=True)
    with error("unsafe_vault"):
        CredentialVault(linked)
    badkey = tmp_path / "badkey"
    badkey.mkdir(mode=0o700)
    (badkey / "master.key").write_bytes(b"wrong")
    (badkey / "master.key").chmod(0o600)
    with error("unsafe_vault"):
        CredentialVault(badkey)
    vault = CredentialVault(tmp_path / "proper")
    (vault.root / "master.key").chmod(0o644)
    with error("unsafe_vault"):
        vault.list("a")


@contextmanager
def provider_server():
    state = {"requests": [], "status": 200, "usage": 7, "tools": False, "delay": 0, "received": threading.Event()}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append({"body": body, "authorization": self.headers.get("Authorization")})
            state["received"].set()
            time.sleep(state["delay"])
            tool = state["tools"]
            delta = {"content": "OK"} if not tool else {"tool_calls": [{"index": 0, "id": "call_probe", "type": "function", "function": {
                "name": "invoke", "arguments": canonical_json(ToolCall("consumer.probe", {}).as_dict())}}]}
            message = {"choices": [{"index": 0, "delta": delta, "finish_reason": "tool_calls" if tool else "stop"}]}
            if state["usage"] is not None:
                message["usage"] = {"total_tokens": state["usage"]}
            data = ("data: " + json.dumps(message) + "\n\ndata: [DONE]\n\n").encode()
            self.send_response(state["status"])
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except BrokenPipeError:
                pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/chat", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.fixture
async def core(tmp_path):
    (tmp_path / "workspace").mkdir()
    runtime = CoreRuntime(tmp_path / "runtime", scope="agent-test", workspace=tmp_path / "workspace")
    yield runtime
    await runtime.close()


def settings(core, tmp_path, endpoint, *, private=True):
    vault = CredentialVault(tmp_path / "vault")
    policy = NetworkPolicy(private_origins=(endpoint.rsplit("/", 1)[0],) if private else ())
    service = ProviderSettings(core, vault, policy)
    return service, vault


def configure(service, endpoint, **kwargs):
    return service.configure("primary", endpoint=endpoint, model="local-wire-model", context_limit=8192, output_limit=512, **kwargs)


async def test_provider_real_probe_activation_runtime_stream_and_usage(core, tmp_path):
    with provider_server() as (endpoint, wire):
        service, vault = settings(core, tmp_path, endpoint)
        secret = vault.put(core.scope, "primary", "provider-secret-value")
        configured = configure(service, endpoint, credential_ref=secret["reference"])
        assert configured["limits_source"] == "user_declared" and not configured["ready"]
        with error("provider_unavailable"):
            service.activate("primary", 1)
        probe = await service.probe("primary", 1)
        assert probe["ready"] and probe["probe"]["usage_reported"]
        assert UsageView(core).summary()["actual"] == {"tokens": 7}
        assert not UsageView(core).summary()["unresolved_claims"]
        assert service.activate("primary", 1)["active"]
        service.install()
        assert core.provider is service and core.credential is None
        descriptor = core.catalog.resolve(core.scope, "primary")
        events = [event async for event in service.stream(descriptor, [{"role": "user", "content": "Hello"}])]
        assert events[-1].data["status"] == "completed"
        assert all(call["authorization"] == "Bearer provider-secret-value" for call in wire["requests"])
        assert "provider-secret-value" not in canonical_json(service.inspect())
        assert "provider-secret-value" not in canonical_json([e.payload for e in core.journal.events(core.scope)])
        vault.put(core.scope, "primary", "rotated-secret", 1)
        assert not service.inspect("primary")["ready"]
        with error("credential_unavailable"):
            await service.ensure_selected_ready()
        with error("provider_unavailable"):
            service.activate("primary", 1)
        with error("provider_unavailable"):
            _ = [event async for event in service.stream(descriptor, [])]
        assert len(wire["requests"]) == 2


async def test_provider_configuration_revisions_expiry_and_tools_probe(core, tmp_path):
    with provider_server() as (endpoint, wire):
        service, _ = settings(core, tmp_path, endpoint)
        configure(service, endpoint, tools=True)
        result = await service.probe("primary", 1)
        assert not result["ready"] and result["probe"]["error"]["code"] == "probe_incompatible"
        wire["tools"] = True
        assert (await service.probe("primary", 1))["ready"]
        service.activate("primary", 1)
        old = core.catalog.resolve(core.scope, "primary")
        with error("conflict"):
            service.activate("primary", 0)
        row = configure(service, endpoint, tools=True, expected_revision=1)
        assert row["revision"] == 2 and not row["active"] and not row["ready"]
        with error("provider_unavailable"):
            core.catalog.resolve(core.scope, "primary")
        with error("conflict"):
            await service.probe("primary", 1)
        await service.probe("primary", 2)
        service.activate("primary", 2)
        with error("provider_unavailable"):
            _ = [event async for event in service.stream(old, [])]
        probe = core.journal.get(PROBES, "primary", core.scope)
        core.journal.put(PROBES, probe.id, core.scope, {**probe.value, "expires_at": 0}, probe.revision)
        assert not service.inspect("primary")["ready"]
        before = len(wire["requests"])
        results = await asyncio.gather(service.ensure_selected_ready(), service.ensure_selected_ready())
        assert all(result["ready"] for result in results)
        assert len(wire["requests"]) == before + 1


async def test_provider_failures_unknown_usage_and_budget_overrun(core, tmp_path):
    with provider_server() as (endpoint, wire):
        service, _ = settings(core, tmp_path, endpoint)
        configure(service, endpoint)
        wire["status"] = 401
        result = await service.probe("primary", 1)
        assert result["probe"]["error"]["code"] == "provider_http" and not result["ready"]
        assert UsageView(core).summary()["unresolved_resources"] == {"tokens": 4096}
        wire.update(status=200, usage=None)
        result = await service.probe("primary", 1)
        assert result["ready"] and not result["probe"]["usage_reported"]
        wire["usage"] = 5000
        result = await service.probe("primary", 1)
        assert not result["ready"] and result["probe"]["error"]["code"] == "budget_exceeded"
        summary = UsageView(core).summary()
        assert summary["actual"] == {"tokens": 5000} and summary["overruns"]
        assert summary["unresolved_resources"] == {"tokens": 8192}


async def test_provider_probe_rotation_and_config_races_preserve_usage(core, tmp_path):
    with provider_server() as (endpoint, wire):
        service, vault = settings(core, tmp_path, endpoint)
        secret = vault.put(core.scope, "primary", "secret")
        configure(service, endpoint, credential_ref=secret["reference"])
        wire["delay"] = .15
        task = asyncio.create_task(service.probe("primary", 1))
        assert await asyncio.to_thread(wire["received"].wait, 2)
        vault.put(core.scope, "primary", "new-secret", 1)
        with error("credential_unavailable"):
            await task
        assert UsageView(core).summary()["actual"]["tokens"] == 7
        new_secret = vault.get(core.scope, "primary")
        configure(service, endpoint, credential_ref=new_secret["reference"], expected_revision=1)
        wire["received"].clear()
        task = asyncio.create_task(service.probe("primary", 2))
        assert await asyncio.to_thread(wire["received"].wait, 2)
        configure(service, endpoint, credential_ref=new_secret["reference"], expected_revision=2)
        with error("conflict"):
            await task
        assert UsageView(core).summary()["actual"]["tokens"] == 14


async def test_provider_probe_cancellation_and_busy_admission(core, tmp_path):
    with provider_server() as (endpoint, wire):
        service, _ = settings(core, tmp_path, endpoint)
        configure(service, endpoint)
        wire["delay"] = .2
        task = asyncio.create_task(service.probe("primary", 1))
        assert await asyncio.to_thread(wire["received"].wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service.inspect("primary")["probe"]["error"]["code"] == "cancelled"
        assert UsageView(core).summary()["unresolved_claims"]
        for _ in range(core.scheduler.max_running):
            row = core.scheduler.submit(core.scope, "hold", {})
            core.scheduler.admit(core.scope, row.id, eligible_ids=[row.id])
        before = len(core.journal.list("work", core.scope))
        with error("busy"):
            await service.probe("primary", 1)
        assert len(core.journal.list("work", core.scope)) == before


async def test_provider_refresh_does_not_override_human_selection(core, tmp_path):
    with provider_server() as (endpoint, wire):
        service, _ = settings(core, tmp_path, endpoint)
        configure(service, endpoint)
        await service.probe("primary", 1)
        service.activate("primary", 1)
        service.configure("second", endpoint=endpoint, model="model-two", context_limit=8192, output_limit=512)
        await service.probe("second", 1)
        probe = core.journal.get(PROBES, "primary", core.scope)
        core.journal.put(PROBES, probe.id, core.scope, {**probe.value, "expires_at": 0}, probe.revision)
        wire["received"].clear()
        wire["delay"] = .15
        task = asyncio.create_task(service.ensure_selected_ready())
        assert await asyncio.to_thread(wire["received"].wait, 2)
        service.activate("second", 1)
        with error("conflict"):
            await task
        assert core.provider_id == "second" and service.inspect("second")["active"]


async def test_provider_network_denial_and_configuration_validation(core, tmp_path):
    with provider_server() as (endpoint, wire):
        service, vault = settings(core, tmp_path, endpoint, private=False)
        assert not ProviderSettings(core, vault).inspect()
        with error("provider_unavailable"):
            await service.ensure_selected_ready()
        with error("not_found"):
            service.inspect("missing")
        with error("credential_unavailable"):
            configure(service, endpoint, credential_ref="missing")
        for change in ({"endpoint": endpoint + "?key=secret"}, {"endpoint": "ws://example.com"},
                       {"model": ""}, {"context_limit": 10}, {"output_limit": 0}, {"tools": "yes"}, {"expected_revision": -1}):
            args = {"endpoint": endpoint, "model": "model", "context_limit": 8192, "output_limit": 512, **change}
            with error("invalid_provider"):
                service.configure("primary", **args)
        secret = vault.put(core.scope, "key", "secret")
        configure(service, endpoint, credential_ref=secret["reference"])
        vault.revoke(core.scope, "key", 1)
        with error("credential_unavailable"):
            await service.probe("primary", 1)
        configure(service, endpoint, expected_revision=1)
        result = await service.probe("primary", 2)
        assert result["probe"]["error"]["code"] == "network_address_denied" and not wire["requests"]
        service.install()
        assert core.provider is service


async def test_usage_deduplicates_children_and_tool_receipts(core):
    scope, scheduler = core.scope, core.scheduler
    parent = scheduler.submit(scope, "model", {}, resources={"tokens": 100, "tool_calls": 2}, capabilities=["memory.read"])
    parent = scheduler.admit(scope, parent.id)
    child = scheduler.submit(scope, "scout", {}, resources={"tokens": 30, "tool_calls": 1}, parent_id=parent.id, capabilities=["memory.read"])
    child = scheduler.admit(scope, child.id)
    scheduler.record_usage(scope, child.id, "child-tokens", {"tokens": 9})
    scheduler.record_usage(scope, child.id, "child-tokens", {"tokens": 9})
    scheduler.authorize(scope, {"work_id": child.id, "generation": child.value["generation"],
        "claim_id": "tool-claim", "required_capabilities": ["memory.read"], "resources": {"tool_calls": 1}}, ToolCall("memory.query", {}))
    core.journal.append(scope, "tool.usage", {"work_id": child.id, "claim_id": "tool-claim", "resources": {"tool_calls": 1}}, source="tool_runner", dedupe_key="tool-usage")
    scheduler.authorize(scope, {"work_id": parent.id, "generation": parent.value["generation"],
        "claim_id": "unresolved", "required_capabilities": [], "resources": {"tokens": 20}}, ToolCall("model.invoke", {}))
    other = "other-agent"
    core.journal.put("work_usage", "outside", other, {"work_id": "outside", "resources": {"tokens": 999999}})
    view = UsageView(core)
    result = view.summary()
    assert result["actual"] == {"tokens": 9, "tool_calls": 1}
    assert result["direct_work_usage"] == {child.id: {"tokens": 9, "tool_calls": 1}}
    assert result["receipt_count"] == 2 and result["unresolved_resources"] == {"tokens": 20}
    assert len(result["active_allocations"]) == 2
    assert result["cost_estimate"] is None
    assert view.summary() == result
