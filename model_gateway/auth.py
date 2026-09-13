"""Tenant-bound native account sign-in, encrypted tokens and serialized refresh.

Wire contracts were audited against the pinned 9router reference, official
GitHub/AWS OAuth documentation, and the public Codex/Gemini CLI clients. This
module never imports another application's credentials or starts a remote login
until an authenticated caller explicitly requests ``start``.
"""
from __future__ import annotations

import asyncio
import base64
from copy import deepcopy
import hashlib
import json
import math
import re
import secrets
import time
from weakref import WeakValueDictionary
from urllib.parse import urlencode, urlsplit

from model_gateway.auth_state import claim_refresh, finish_refresh, finish_session
from model_gateway.errors import GatewayError
from model_gateway.network import origin
from model_gateway.store import text

SESSION_KIND = "auth_session"
ACCOUNT_KIND = "auth_account"
SESSION_TTL = 900
TOKEN_LEAD = 90

# Public installed-application client identifiers, not user credentials. Hosted
# operators can supply their own registered application and exact redirect URI.
_CONFIGS = {
    "github-copilot": {
        "name": "GitHub Copilot", "flow": "device_code", "client_id": "Iv1.b507a08c87ecfe98",
        "device_url": "https://github.com/login/device/code", "token_url": "https://github.com/login/oauth/access_token",
        "account_token_url": "https://api.github.com/copilot_internal/v2/token", "user_url": "https://api.github.com/user",
        "endpoint": "https://api.githubcopilot.com", "protocol": "openai", "scope": "read:user",
        "description": "Sign in to your GitHub account. Copilot access, organization policy and account quotas apply.",
    },
    "codex": {
        "name": "ChatGPT / Codex", "flow": "authorization_code_pkce", "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
        "authorize_url": "https://auth.openai.com/oauth/authorize", "token_url": "https://auth.openai.com/oauth/token",
        "endpoint": "https://chatgpt.com/backend-api/codex", "protocol": "openai-responses",
        "scope": "openid profile email offline_access", "redirect_uri": "http://localhost:1455/auth/callback",
        "description": "Sign in with ChatGPT. This connection uses your eligible Codex account and its limits.",
        "authorize_params": {"id_token_add_organizations": "true", "codex_cli_simplified_flow": "true", "originator": "codex_cli_rs"},
    },
    "gemini-cli": {
        "name": "Gemini CLI", "flow": "authorization_code_pkce",
        "client_id": "",
        "client_secret": "",
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth", "token_url": "https://oauth2.googleapis.com/token",
        "user_url": "https://www.googleapis.com/oauth2/v2/userinfo", "endpoint": "https://cloudcode-pa.googleapis.com/v1internal",
        "protocol": "gemini-cli", "redirect_uri": "http://localhost:8085/oauth2callback",
        "scope": "https://www.googleapis.com/auth/cloud-platform https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/userinfo.profile",
        "authorize_params": {"access_type": "offline", "prompt": "consent"},
        "description": "Use your eligible Google Code Assist account. Project, region and account restrictions apply.",
    },
    "kiro": {
        "name": "Kiro", "flow": "device_code", "protocol": "kiro", "region": "us-east-1",
        "endpoint": "https://runtime.us-east-1.kiro.dev", "scope": "codewhisperer:completions codewhisperer:analysis codewhisperer:conversations",
        "start_url": "https://view.awsapps.com/start", "issuer_url": "https://identitycenter.amazonaws.com/ssoins-722374e8c3c8e6c6",
        "description": "Sign in with AWS Builder ID or IAM Identity Center. Kiro account credits and access rules apply.",
    },
}


def _secret_json(value):
    """Encode token bundles as printable ASCII for the encrypted store boundary."""
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def _token(value, label="token", maximum=32768):
    if not isinstance(value, str) or not value or len(value) > maximum or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise GatewayError("invalid_auth_response", f"The provider returned an invalid {label}.", 502)
    return value


def _number(value, default, maximum=31_536_000):
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= maximum or not math.isfinite(value):
        raise GatewayError("invalid_auth_response", "The provider returned an invalid expiry or polling interval.", 502)
    return float(value)


def _claims(token):
    """Read routing hints from a token obtained from the trusted token endpoint.

    Claims are not used to authenticate a Sturgis user or assign tenant authority.
    """
    try:
        encoded = token.split(".")[1]
        value = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        return value if isinstance(value, dict) else {}
    except (ValueError, IndexError, AttributeError, UnicodeError):
        return {}


class GatewayAuth:
    """Persisted account lifecycle independent of Sturgis agents and consumers."""

    def __init__(self, store, network_policy, *, configs=None, clock=time.time):
        self.store, self.network_policy, self.clock = store, network_policy, clock
        self.configs = deepcopy(_CONFIGS)
        for key, value in (configs or {}).items():
            if key not in self.configs or not isinstance(value, dict):
                raise GatewayError("invalid_auth_config", "Unknown provider authentication configuration.")
            self.configs[key].update(value)
        self._locks = WeakValueDictionary()

    def _config(self, provider_id):
        if not isinstance(provider_id, str) or provider_id not in self.configs:
            raise GatewayError("unsupported_auth_provider", "Choose a supported account sign-in provider.")
        return self.configs[provider_id]

    def providers(self):
        """Public connection choices contain no token endpoints or client secrets."""
        return [{"id": key, "name": c["name"], "flow": c["flow"], "description": c["description"],
                 "default_redirect_uri": c.get("redirect_uri"),
                 "options": ["region", "start_url"] if key == "kiro" else (["project_id"] if key == "gemini-cli" else [])}
                for key, c in self.configs.items()]

    def _lock(self, tenant, id):
        return self._locks.setdefault((tenant, id), asyncio.Lock())

    def _secret(self, tenant, row):
        value = self.store.resolve(tenant, row.get("secret_ref"))
        if not value:
            raise GatewayError("auth_unavailable", "This sign-in credential is unavailable. Sign in again.", 409)
        try:
            result = json.loads(value)
        except (ValueError, TypeError):
            raise GatewayError("auth_unavailable", "This sign-in credential could not be read.", 503) from None
        if not isinstance(result, dict):
            raise GatewayError("auth_unavailable", "This sign-in credential could not be read.", 503)
        return result

    def _public_session(self, row):
        result = {key: row[key] for key in ("id", "provider_id", "name", "flow", "status", "expires_at", "authorization_url",
                   "redirect_uri", "user_code", "verification_uri", "verification_uri_complete", "account_id", "error_code") if key in row}
        result["poll_after"] = max(1, math.ceil(row.get("next_poll_at", self.clock()) - self.clock())) if row["status"] in {"pending", "exchanging"} else 0
        return result

    @staticmethod
    def _public_account(row):
        return {key: row[key] for key in ("id", "provider_id", "name", "status", "expires_at", "revision") if key in row}

    def accounts(self, tenant):
        return [self._public_account(row) for row in self.store.list(tenant, ACCOUNT_KIND) if row["status"] == "connected"]

    def _session(self, tenant, id):
        row = self.store.get(tenant, SESSION_KIND, id)
        if row["status"] in {"pending", "exchanging"} and row["expires_at"] <= self.clock():
            row = self.store.put(tenant, SESSION_KIND, id, {**row, "status": "expired"}, row["revision"], secret="")
        return row

    def inspect(self, tenant, id):
        return self._public_session(self._session(tenant, id))

    def _redirect(self, config, value):
        value = value or config["redirect_uri"]
        if not isinstance(value, str) or len(value) > 2048:
            raise GatewayError("invalid_redirect", "Use this provider's registered callback URL.")
        origin(value)
        parsed = urlsplit(value)
        registered = config.get("allowed_redirect_uris", [config["redirect_uri"]])
        if value not in registered or parsed.query or parsed.fragment:
            raise GatewayError("invalid_redirect", "This callback URL is not registered for the provider. Check the gateway operator configuration.")
        return value

    async def _request(self, method, url, *, form=None, body=None, headers=None):
        request_headers = {"Accept": "application/json", "User-Agent": "Sturgis-Gateway/0.1", **(headers or {})}
        content = None
        if form is not None:
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
            content = urlencode(form).encode()
        elif body is not None:
            request_headers["Content-Type"] = "application/json"
            content = _secret_json(body).encode()
        try:
            response = await self.network_policy.request(method, url, allowed_origins=(origin(url),), headers=request_headers,
                                                         content=content, max_bytes=262144, follow_redirects=False)
            data = response.json()
        except GatewayError:
            raise GatewayError("auth_network", "The account service could not be reached. Try again.", 503, retryable=True) from None
        if not isinstance(data, dict):
            raise GatewayError("invalid_auth_response", "The account service returned an invalid response.", 502)
        return response.status, data

    @staticmethod
    def _success(status, data):
        if not 200 <= status < 300 or data.get("error") or data.get("__type"):
            raise GatewayError("auth_rejected", "The provider could not authorize this account. Check account access or sign in again.", 502,
                               retryable=status == 429 or status >= 500)
        return data

    async def start(self, tenant, provider_id, redirect_uri=None, name=None, options=None):
        """Begin a user-requested sign-in; device and verifier material stays encrypted."""
        tenant = self.store._tenant(tenant)
        c = self._config(provider_id)
        if options is not None and not isinstance(options, dict):
            raise GatewayError("invalid_auth_options", "Sign-in options must be an object.")
        options = options or {}
        allowed = {"region", "start_url"} if provider_id == "kiro" else ({"project_id"} if provider_id == "gemini-cli" else set())
        if set(options) - allowed:
            raise GatewayError("invalid_auth_options", "This provider does not accept those sign-in options.")
        now, id = self.clock(), "signin_" + secrets.token_hex(16)
        body = {"provider_id": provider_id, "name": text(name or c["name"], "account name", 120), "flow": c["flow"],
                "status": "pending", "expires_at": now + SESSION_TTL, "next_poll_at": now, "interval": 5}
        if provider_id == "gemini-cli" and "project_id" in options:
            options = {"project_id": text(options["project_id"], "Google project identifier", 256)}
        private = {"options": options}
        if c["flow"] == "authorization_code_pkce":
            redirect = self._redirect(c, redirect_uri)
            state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            params = {"client_id": c["client_id"], "response_type": "code", "redirect_uri": redirect, "scope": c["scope"],
                      "state": state, "code_challenge": challenge, "code_challenge_method": "S256", **c.get("authorize_params", {})}
            body.update({"authorization_url": c["authorize_url"] + "?" + urlencode(params), "redirect_uri": redirect,
                         "state_digest": hashlib.sha256(state.encode()).hexdigest()})
            private["verifier"] = verifier
        else:
            if provider_id == "kiro":
                region = text(options.get("region", c["region"]), "AWS region", 32)
                if not re.fullmatch(r"[a-z]{2}(?:-gov)?-[a-z]+-\d", region):
                    raise GatewayError("invalid_region", "Choose a valid AWS region.")
                start_url = options.get("start_url", c["start_url"])
                if not isinstance(start_url, str) or len(start_url) > 2048:
                    raise GatewayError("invalid_auth_options", "Use your AWS access portal URL.")
                parsed = urlsplit(start_url)
                if parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment or not (parsed.hostname or "").endswith(".awsapps.com"):
                    raise GatewayError("invalid_auth_options", "Use an HTTPS AWS access portal URL.")
                oidc = c.get("oidc_endpoint", f"https://oidc.{region}.amazonaws.com")
                status, registration = await self._request("POST", oidc + "/client/register", body={
                    "clientName": "Sturgis model gateway", "clientType": "public", "scopes": c["scope"].split(),
                    "grantTypes": ["urn:ietf:params:oauth:grant-type:device_code", "refresh_token"], "issuerUrl": c["issuer_url"]})
                self._success(status, registration)
                private.update({"client_id": _token(registration.get("clientId"), "client identifier"),
                                "client_secret": _token(registration.get("clientSecret"), "client credential"), "region": region,
                                "token_url": oidc + "/token", "registration_expires_at": _number(registration.get("clientSecretExpiresAt"), None, 10_000_000_000)})
                status, data = await self._request("POST", oidc + "/device_authorization", body={
                    "clientId": private["client_id"], "clientSecret": private["client_secret"], "startUrl": start_url})
                data = {**data, "device_code": data.get("deviceCode"), "user_code": data.get("userCode"),
                        "verification_uri": data.get("verificationUri"), "verification_uri_complete": data.get("verificationUriComplete"),
                        "expires_in": data.get("expiresIn"), "interval": data.get("interval")}
            else:
                status, data = await self._request("POST", c["device_url"], form={"client_id": c["client_id"], "scope": c["scope"]})
            self._success(status, data)
            private["device_code"] = _token(data.get("device_code"), "device code")
            verification = data.get("verification_uri")
            if not isinstance(verification, str) or urlsplit(verification).scheme != "https" or len(verification) > 2048:
                raise GatewayError("invalid_auth_response", "The provider returned an invalid verification URL.", 502)
            origin(verification)
            complete = data.get("verification_uri_complete")
            if complete and (not isinstance(complete, str) or len(complete) > 2048 or origin(complete) != origin(verification)):
                raise GatewayError("invalid_auth_response", "The provider returned an invalid verification URL.", 502)
            interval = _number(data.get("interval"), 5, 60)
            body.update({"user_code": _token(data.get("user_code"), "verification code", 128), "verification_uri": verification,
                         "expires_at": now + min(SESSION_TTL, _number(data.get("expires_in"), SESSION_TTL)),
                         "interval": interval, "next_poll_at": now + interval})
            if complete:
                body["verification_uri_complete"] = complete
        row = await asyncio.to_thread(self.store.put, tenant, SESSION_KIND, id, body, secret=_secret_json(private))
        return self._public_session(row)

    async def poll(self, tenant, session_id):
        """Perform at most one due device exchange or post-login bootstrap request."""
        async with self._lock(tenant, session_id):
            row = await asyncio.to_thread(self._session, tenant, session_id)
            if row["status"] != "pending" or row["next_poll_at"] > self.clock():
                return self._public_session(row)
            private = await asyncio.to_thread(self._secret, tenant, row)
            if "tokens" in private:
                return await self._finish(tenant, row, private)
            if row["flow"] != "device_code":
                return self._public_session(row)
            c = self._config(row["provider_id"])
            row = await asyncio.to_thread(self.store.put, tenant, SESSION_KIND, session_id, {**row, "status": "exchanging", "next_poll_at": self.clock() + row["interval"]}, row["revision"])
            try:
                if row["provider_id"] == "kiro":
                    status, result = await self._request("POST", private["token_url"], body={"clientId": private["client_id"],
                        "clientSecret": private["client_secret"], "deviceCode": private["device_code"],
                        "grantType": "urn:ietf:params:oauth:grant-type:device_code"})
                else:
                    status, result = await self._request("POST", c["token_url"], form={"client_id": c["client_id"],
                        "device_code": private["device_code"], "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
            except BaseException:
                await asyncio.to_thread(self._fail, tenant, session_id, "failed", "authorization_exchange_failed")
                raise
            error = result.get("error", result.get("__type", ""))
            if isinstance(error, str):
                error = error.rsplit("#", 1)[-1]
            if not isinstance(error, str):
                error = "invalid_provider_error"
            if error in {"authorization_pending", "AuthorizationPendingException"}:
                current = await asyncio.to_thread(self._session, tenant, session_id)
                if current["status"] == "exchanging":
                    current = await asyncio.to_thread(self.store.put, tenant, SESSION_KIND, session_id, {**current, "status": "pending"}, current["revision"])
                return self._public_session(current)
            if error in {"slow_down", "SlowDownException"}:
                current = await asyncio.to_thread(self._session, tenant, session_id)
                if current["status"] == "exchanging":
                    interval = min(60, current["interval"] + 5)
                    current = await asyncio.to_thread(self.store.put, tenant, SESSION_KIND, session_id, {**current, "status": "pending", "interval": interval,
                        "next_poll_at": self.clock() + interval}, current["revision"])
                return self._public_session(current)
            if error in {"access_denied", "authorization_declined", "AccessDeniedException", "expired_token", "ExpiredTokenException"}:
                return await asyncio.to_thread(self._fail, tenant, session_id, "expired" if error in {"expired_token", "ExpiredTokenException"} else "failed", "authorization_denied")
            try:
                self._success(status, result)
            except GatewayError:
                await asyncio.to_thread(self._fail, tenant, session_id, "failed", "authorization_exchange_failed")
                raise
            return await self._save_tokens(tenant, row, private, result)

    async def complete(self, tenant, session_id, state, code):
        """Exchange one tenant/state-bound authorization code with its saved PKCE verifier."""
        async with self._lock(tenant, session_id):
            row = await asyncio.to_thread(self._session, tenant, session_id)
            if not isinstance(state, str) or len(state) > 256 or any(ord(c) < 33 or ord(c) > 126 for c in state) or not secrets.compare_digest(
                    hashlib.sha256(state.encode()).hexdigest(), row.get("state_digest", "")):
                raise GatewayError("invalid_auth_state", "This sign-in callback does not match the pending sign-in.", 403)
            if row["status"] == "connected":
                return self._public_session(row)
            if row["status"] != "pending" or row["flow"] != "authorization_code_pkce":
                raise GatewayError("auth_session_closed", "This sign-in is no longer pending. Start again.", 409)
            private, c = await asyncio.to_thread(self._secret, tenant, row), self._config(row["provider_id"])
            if "tokens" in private:
                return await self._finish(tenant, row, private)
            code = _token(code, "authorization code", 8192)
            row = await asyncio.to_thread(self.store.put, tenant, SESSION_KIND, session_id, {**row, "status": "exchanging"}, row["revision"])
            form = {"grant_type": "authorization_code", "client_id": c["client_id"], "redirect_uri": row["redirect_uri"],
                    "code": code, "code_verifier": private["verifier"]}
            if c.get("client_secret"):
                form["client_secret"] = c["client_secret"]
            try:
                status, result = await self._request("POST", c["token_url"], form=form)
                self._success(status, result)
            except BaseException:
                await asyncio.to_thread(self._fail, tenant, session_id, "failed", "authorization_exchange_failed")
                raise
            return await self._save_tokens(tenant, row, private, result)

    def _fail(self, tenant, session_id, status, code):
        current = self.store.get(tenant, SESSION_KIND, session_id)
        if current["status"] in {"pending", "exchanging"}:
            current = self.store.put(tenant, SESSION_KIND, session_id, {**current, "status": status, "error_code": code}, current["revision"], secret="")
        return self._public_session(current)

    def cancel(self, tenant, session_id):
        """Tombstone the session and destroy its verifier/device/tokens immediately."""
        return self._fail(tenant, session_id, "cancelled", "cancelled")

    def _tokens(self, result, old=None):
        old = old or {}
        access = _token(result.get("access_token", result.get("accessToken")))
        refresh = result.get("refresh_token", result.get("refreshToken", old.get("refresh_token")))
        value = {**old, "access_token": access, "expires_at": self.clock() + _number(result.get("expires_in", result.get("expiresIn")), 3600)}
        if refresh:
            value["refresh_token"] = _token(refresh, "refresh token")
        if result.get("id_token"):
            value["id_token"] = _token(result["id_token"], "identity token")
        if result.get("profileArn"):
            value["profile_arn"] = _token(result["profileArn"], "profile identifier", 512)
        return value

    async def _save_tokens(self, tenant, row, private, result):
        current = await asyncio.to_thread(self._session, tenant, row["id"])
        if current["status"] not in {"pending", "exchanging"}:
            return self._public_session(current)
        private = {key: value for key, value in private.items() if key not in {"device_code", "verifier"}}
        try:
            private["tokens"] = self._tokens(result)
        except GatewayError:
            await asyncio.to_thread(self._fail, tenant, row["id"], "failed", "invalid_auth_response")
            raise
        current = await asyncio.to_thread(self.store.put, tenant, SESSION_KIND, row["id"], {**current, "status": "pending"}, current["revision"], secret=_secret_json(private))
        return await self._finish(tenant, current, private)

    async def _finish(self, tenant, row, private):
        c, provider = self._config(row["provider_id"]), row["provider_id"]
        tokens = private["tokens"]
        if provider == "github-copilot":
            await self._copilot(c, tokens)
        elif provider == "codex":
            account_id = None
            for candidate in (tokens.get("id_token"), tokens["access_token"]):
                claims = _claims(candidate)
                account = claims.get("https://api.openai.com/auth")
                account_id = (account.get("chatgpt_account_id") if isinstance(account, dict) else None) or claims.get("account_id")
                if account_id:
                    break
            if not account_id:
                raise GatewayError("account_unavailable", "This ChatGPT sign-in did not provide an eligible Codex account.", 502)
            tokens["chatgpt_account_id"] = _token(account_id, "account identifier", 256)
        elif provider == "gemini-cli":
            project = await self._gemini_project(c, tokens, private)
            if not project:
                current = await asyncio.to_thread(self._session, tenant, row["id"])
                if current["status"] != "pending":
                    return self._public_session(current)
                current = await asyncio.to_thread(self.store.put, tenant, SESSION_KIND, row["id"], {**current, "next_poll_at": self.clock() + 5},
                                         current["revision"], secret=_secret_json(private))
                return self._public_session(current)
            tokens["project_id"] = project
        elif provider == "kiro":
            tokens.update({key: private[key] for key in ("client_id", "client_secret", "region", "token_url", "registration_expires_at")})
            if not tokens.get("profile_arn"):
                endpoint = c.get("profile_url", f"https://codewhisperer.{private['region']}.amazonaws.com/ListAvailableProfiles")
                status, data = await self._request("POST", endpoint, body={"maxResults": 10}, headers={"Authorization": "Bearer " + tokens["access_token"]})
                self._success(status, data)
                profiles = data.get("profiles", [])
                profile = next((p.get("arn") for p in profiles if isinstance(p, dict) and p.get("arn")), None) if isinstance(profiles, list) else None
                if not profile:
                    raise GatewayError("account_unavailable", "No accessible Kiro profile was returned for this account.", 502)
                tokens["profile_arn"] = _token(profile, "profile identifier", 512)
        current = await asyncio.to_thread(self._session, tenant, row["id"])
        if current["status"] != "pending":
            return self._public_session(current)
        account_id = "account_" + row["id"].removeprefix("signin_")
        current = await asyncio.to_thread(finish_session, self.store, tenant, row["id"], account_id,
            {"provider_id": provider, "name": row["name"], "status": "connected", "expires_at": tokens["expires_at"]},
            _secret_json(tokens), self.clock())
        return self._public_session(current)

    async def _copilot(self, config, tokens):
        if tokens.get("copilot_expires_at", 0) > self.clock() + TOKEN_LEAD:
            return
        status, data = await self._request("GET", config["account_token_url"], headers={
            "Authorization": "Bearer " + tokens["access_token"], "X-GitHub-Api-Version": "2022-11-28"})
        self._success(status, data)
        tokens["copilot_token"] = _token(data.get("token"), "Copilot token")
        expiry = _number(data.get("expires_at"), None, 10_000_000_000)
        if expiry is None or expiry <= self.clock():
            raise GatewayError("invalid_auth_response", "Copilot returned an expired account token.", 502)
        tokens["copilot_expires_at"] = expiry

    async def _gemini_project(self, config, tokens, private):
        if tokens.get("project_id"):
            return tokens["project_id"]
        headers = {"Authorization": "Bearer " + tokens["access_token"]}
        metadata = {"ideType": "IDE_UNSPECIFIED", "platform": "PLATFORM_UNSPECIFIED", "pluginType": "GEMINI"}
        requested = private.get("options", {}).get("project_id")
        if requested is not None:
            requested = text(requested, "Google project identifier", 256)
        if private.get("onboard_tier"):
            body = {"tierId": private["onboard_tier"], "metadata": metadata}
            if requested:
                body["cloudaicompanionProject"] = requested
            status, data = await self._request("POST", config["endpoint"] + ":onboardUser", body=body, headers=headers)
            self._success(status, data)
            if data.get("done") is not True:
                return None
            data = data.get("response", {})
        else:
            body = {"metadata": metadata}
            if requested:
                body["cloudaicompanionProject"] = requested
            status, data = await self._request("POST", config["endpoint"] + ":loadCodeAssist", body=body, headers=headers)
            self._success(status, data)
        project = data.get("cloudaicompanionProject") if isinstance(data, dict) else None
        if isinstance(project, dict):
            project = project.get("id")
        if project:
            return text(project, "Google project identifier", 256)
        if private.get("onboard_tier"):
            raise GatewayError("account_unavailable", "Google completed setup without an accessible project.", 502)
        tiers = data.get("allowedTiers", [])
        tier = next((t.get("id") for t in tiers if isinstance(t, dict) and t.get("isDefault") is True), None) if isinstance(tiers, list) else None
        if not tier:
            raise GatewayError("account_unavailable", "Google did not return an accessible Code Assist project or default tier.", 502)
        private["onboard_tier"] = text(tier, "Google account tier", 128)
        return None

    async def credential(self, tenant, account_id, force_refresh=False, expected_revision=None):
        """Resolve a server-only transport binding; refresh at most once concurrently."""
        if expected_revision is not None:
            self.store._revision(expected_revision)
        observed = await asyncio.to_thread(self.store.get, tenant, ACCOUNT_KIND, account_id)
        async with self._lock(tenant, account_id):
            row = await asyncio.to_thread(self.store.get, tenant, ACCOUNT_KIND, account_id)
            if row["status"] != "connected":
                raise GatewayError("account_disconnected", "This provider account was disconnected. Sign in again.", 503)
            tokens, c = await asyncio.to_thread(self._secret, tenant, row), self._config(row["provider_id"])
            force_refresh = bool(force_refresh) and observed["revision"] == row["revision"] and expected_revision in (None, row["revision"])
            oauth_due = tokens["expires_at"] <= self.clock() + TOKEN_LEAD or force_refresh
            copilot_due = row["provider_id"] == "github-copilot" and (tokens.get("copilot_expires_at", 0) <= self.clock() + TOKEN_LEAD or force_refresh)
            if row.get("refresh_uncertain") or row.get("refresh_lease"):
                # The persisted lease fences other instances before any token is
                # reused, even if this process still holds a cached access token.
                await asyncio.to_thread(claim_refresh, self.store, tenant, account_id, row["revision"], self.clock(), 60)
            if oauth_due or copilot_due:
                row = await asyncio.to_thread(claim_refresh, self.store, tenant, account_id, row["revision"], self.clock(),
                                    max(60, self.network_policy.timeout * 2 + 10))
                rotating = False
                try:
                    if oauth_due:
                        if row["provider_id"] == "github-copilot" and not tokens.get("refresh_token"):
                            # This timestamp schedules a credential recheck, not
                            # a claimed expiry for GitHub's non-expiring token.
                            tokens["expires_at"] = self.clock() + 3600
                        else:
                            if not tokens.get("refresh_token"):
                                raise GatewayError("account_reauth_required", "This account needs a new sign-in.", 503)
                            if row["provider_id"] == "kiro":
                                registration_expiry = tokens.get("registration_expires_at")
                                if registration_expiry is not None and (type(registration_expiry) not in (int, float) or registration_expiry <= self.clock()):
                                    raise GatewayError("account_reauth_required", "The AWS application registration expired. Sign in again.", 503)
                                rotating = True
                                status, data = await self._request("POST", tokens["token_url"], body={"clientId": tokens["client_id"],
                                    "clientSecret": tokens["client_secret"], "refreshToken": tokens["refresh_token"], "grantType": "refresh_token"})
                            else:
                                form = {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": c["client_id"]}
                                if c.get("client_secret"):
                                    form["client_secret"] = c["client_secret"]
                                rotating = True
                                status, data = await self._request("POST", c["token_url"], form=form)
                            self._success(status, data)
                            tokens = self._tokens(data, tokens)
                    if copilot_due:
                        if force_refresh:
                            tokens["copilot_expires_at"] = 0
                        await self._copilot(c, tokens)
                    row = await asyncio.to_thread(finish_refresh, self.store, tenant, row, secret=_secret_json(tokens), expires_at=tokens["expires_at"])
                except BaseException:
                    try:
                        await asyncio.to_thread(finish_refresh, self.store, tenant, row, uncertain=rotating)
                    except GatewayError:
                        pass  # A disconnect or newer writer keeps its authority.
                    raise
            return self._binding(c, row, tokens)

    def _binding(self, c, row, tokens):
        provider = row["provider_id"]
        headers = {}
        credential = tokens["access_token"]
        if provider == "codex":
            headers = {"ChatGPT-Account-ID": tokens["chatgpt_account_id"], "OpenAI-Beta": "responses=experimental", "originator": "codex_cli_rs"}
        elif provider == "github-copilot":
            credential = tokens["copilot_token"]
            headers = {"Copilot-Integration-Id": "vscode-chat", "X-GitHub-Api-Version": "2022-11-28", "OpenAI-Intent": "conversation-panel"}
        elif provider == "kiro":
            headers = {"x-amz-sso-bearer": credential, "x-amzn-codewhisperer-profile-arn": tokens["profile_arn"]}
        return {"provider_id": provider, "preset_id": provider, "credential": credential, "protocol": c["protocol"],
                "endpoint": c["endpoint"], "headers": headers, "project_id": tokens.get("project_id"),
                "profile_arn": tokens.get("profile_arn"), "region": tokens.get("region"),
                "models_endpoint": c.get("models_endpoint"), "account_id": row["id"], "account_revision": row["revision"]}

    def disconnect(self, tenant, account_id, expected_revision=None):
        """Destroy the encrypted account tokens and prevent future resolution."""
        row = self.store.get(tenant, ACCOUNT_KIND, account_id)
        if expected_revision is not None:
            self.store._revision(expected_revision)
            if expected_revision != row["revision"]:
                raise GatewayError("conflict", "This account changed. Refresh it before disconnecting.", 409)
        row = self.store.put(tenant, ACCOUNT_KIND, account_id, {**row, "status": "disconnected"}, row["revision"], secret="")
        return self._public_account(row)
