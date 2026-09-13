"""Operator OAuth configuration must fail safely before sign-in side effects."""

import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from model_gateway.api import create_app
from model_gateway.auth import GatewayAuth
from model_gateway.errors import GatewayError
from model_gateway.network import NetworkPolicy
from model_gateway.store import GatewayStore
from tests.gateway.test_auth import auth_wire, config, seed, state


CLIENT_ID_ENV = "STURGIS_GATEWAY_GEMINI_CLIENT_ID"
CLIENT_SECRET_ENV = "STURGIS_GATEWAY_GEMINI_CLIENT_SECRET"


@pytest.fixture(autouse=True)
def clean_oauth_environment(monkeypatch):
    """No test may accidentally use the operator's registered application."""
    monkeypatch.delenv(CLIENT_ID_ENV, raising=False)
    monkeypatch.delenv(CLIENT_SECRET_ENV, raising=False)


@pytest.mark.parametrize(
    "values",
    [
        {},
        {CLIENT_ID_ENV: "synthetic-only-client"},
        {CLIENT_SECRET_ENV: "synthetic-only-secret"},
        {CLIENT_ID_ENV: "", CLIENT_SECRET_ENV: "synthetic-secret"},
        {CLIENT_ID_ENV: "synthetic-client", CLIENT_SECRET_ENV: " "},
        {CLIENT_ID_ENV: "synthetic-client", CLIENT_SECRET_ENV: "invalid\nsecret"},
    ],
)
async def test_missing_or_invalid_environment_starts_no_session_or_network(
    tmp_path, monkeypatch, values
):
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    store = GatewayStore(tmp_path)
    policy = NetworkPolicy()

    async def forbidden_request(*args, **kwargs):
        pytest.fail("Unconfigured sign-in must not contact a provider")

    monkeypatch.setattr(policy, "request", forbidden_request)
    auth = GatewayAuth(store, policy)
    with pytest.raises(GatewayError) as caught:
        await auth.start("tenant", "gemini-cli")
    error = caught.value
    assert error.code == "auth_not_configured" and error.status == 503
    assert not error.retryable
    assert CLIENT_ID_ENV in error.message and CLIENT_SECRET_ENV in error.message
    public = json.dumps([error.as_dict(), auth.providers(), auth.accounts("tenant")])
    assert all(value not in public for value in values.values() if value.strip())
    assert store.list("tenant", "auth_session") == []
    assert store.list("tenant", "auth_account") == []


@pytest.mark.parametrize("invalid", [None, 123, False, {}, "\t", "x" * 4097, "é"])
async def test_invalid_explicit_secret_does_not_fall_back_to_environment(
    tmp_path, monkeypatch, invalid
):
    monkeypatch.setenv(CLIENT_ID_ENV, "synthetic-environment-client")
    monkeypatch.setenv(CLIENT_SECRET_ENV, "synthetic-environment-secret")
    auth = GatewayAuth(
        GatewayStore(tmp_path),
        NetworkPolicy(),
        configs={"gemini-cli": {"client_secret": invalid}},
    )
    with pytest.raises(GatewayError) as caught:
        await auth.start("tenant", "gemini-cli")
    assert caught.value.code == "auth_not_configured"
    assert not auth.store.list("tenant", "auth_session")


@pytest.mark.parametrize("use_override", [False, True])
async def test_configured_credentials_drive_pkce_exchange_and_refresh_without_leakage(
    tmp_path, monkeypatch, use_override
):
    environment_id = "synthetic-environment-client"
    environment_secret = "synthetic-environment-secret"
    monkeypatch.setenv(CLIENT_ID_ENV, environment_id)
    monkeypatch.setenv(CLIENT_SECRET_ENV, environment_secret)
    replies = [
        {
            "access_token": "synthetic-access",
            "refresh_token": "synthetic-refresh",
            "expires_in": 3600,
        },
        {"cloudaicompanionProject": "synthetic-project"},
        {"access_token": "synthetic-renewed-access", "expires_in": 3600},
    ]
    async with auth_wire(replies) as (url, policy, requests):
        configs = config(url)
        if use_override:
            expected_id = configs["gemini-cli"]["client_id"]
            expected_secret = configs["gemini-cli"]["client_secret"]
        else:
            configs["gemini-cli"].pop("client_id")
            configs["gemini-cli"].pop("client_secret")
            expected_id, expected_secret = environment_id, environment_secret
        now = [1000]
        auth = GatewayAuth(GatewayStore(tmp_path), policy, configs=configs, clock=lambda: now[0])
        # Environment changes do not silently rotate a running instance's app.
        monkeypatch.setenv(CLIENT_SECRET_ENV, "synthetic-changed-after-construction")
        session = await auth.start("tenant", "gemini-cli")
        params = parse_qs(urlsplit(session["authorization_url"]).query)
        assert params["client_id"] == [expected_id]
        assert params["code_challenge_method"] == ["S256"]
        assert not requests
        connected = await auth.complete("tenant", session["id"], state(session), "synthetic-code")
        assert connected["status"] == "connected"
        now[0] += 3601
        binding = await auth.credential("tenant", connected["account_id"])
        assert binding["credential"] == "synthetic-renewed-access"
        for request in (requests[0], requests[-1]):
            assert request["body"]["client_id"] == [expected_id]
            assert request["body"]["client_secret"] == [expected_secret]
        assert requests[-1]["body"]["grant_type"] == ["refresh_token"]
        public = json.dumps([session, connected, auth.providers(), auth.accounts("tenant")])
        for secret in (
            expected_secret,
            environment_secret,
            "synthetic-access",
            "synthetic-refresh",
        ):
            assert secret not in public
            assert secret.encode() not in auth.store.path.read_bytes()
        assert "client_id" not in json.dumps(auth.providers())


async def test_missing_config_after_restart_keeps_pending_session_and_refresh_unmodified(
    tmp_path, monkeypatch
):
    async with auth_wire([]) as (url, policy, requests):
        store = GatewayStore(tmp_path)
        configured = GatewayAuth(store, policy, configs=config(url), clock=lambda: 1000)
        session = await configured.start("tenant", "gemini-cli")
        pending = store.get("tenant", "auth_session", session["id"])
        account = seed(store, "gemini-cli", project_id="synthetic-project")
        unconfigured = GatewayAuth(store, policy, clock=lambda: 1000)

        def forbidden_write(*args, **kwargs):
            pytest.fail("Missing configuration must preserve durable auth state")

        monkeypatch.setattr(store, "put", forbidden_write)
        for operation in (
            unconfigured.complete("tenant", session["id"], state(session), "synthetic-code"),
            unconfigured.credential("tenant", "account-a", force_refresh=True),
        ):
            with pytest.raises(GatewayError) as caught:
                await operation
            assert caught.value.code == "auth_not_configured"
        assert store.get("tenant", "auth_session", session["id"]) == pending
        assert store.get("tenant", "auth_account", "account-a") == account
        assert not requests


async def test_http_configuration_error_is_actionable_without_opening_callback(tmp_path):
    app = create_app(tmp_path, public_origin="http://gateway.test")
    gateway = app.state.gateway
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
    ) as client:
        client.headers["Authorization"] = "Bearer " + app.state.auth.key_path.read_text()
        try:
            result = await client.post(
                "/api/v1/gateway/auth/start", json={"provider_id": "gemini-cli"}
            )
            assert result.status_code == 503
            assert result.json()["error"]["code"] == "auth_not_configured"
            assert CLIENT_ID_ENV in result.text and CLIENT_SECRET_ENV in result.text
            assert not gateway.callbacks.sessions and not gateway.callbacks.servers
            assert not gateway.auth.accounts("owner")
        finally:
            await gateway.close()
