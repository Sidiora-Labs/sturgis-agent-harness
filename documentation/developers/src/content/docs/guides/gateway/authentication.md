---
title: "Gateway authentication and native accounts"
description: "Understand admin sessions, client keys, tenant-bound sign-in, callback listeners, refresh fencing, and native provider wire contracts."
---

The gateway has three distinct credentials: administrator authority for management, client app keys for inference, and upstream provider credentials for model execution. An authenticated management principal chooses a tenant; request bodies never choose their own tenant. Upstream provider tokens are not accepted as gateway administrator or app credentials.

## Standalone administrator sessions

`StandaloneAuth` creates or reads the private `admin.key`, retains a SHA-256 digest for verification, and checks the exact configured Host and optional Origin. The standalone management API supports an administrator bearer credential or a browser session. Inference routes instead require a gateway app key.

`POST /api/v1/gateway/session` exchanges `admin_key` for an opaque HttpOnly `sturgis_gateway` cookie and `sturgis_gateway_csrf`. Browser mutations require the configured Origin and matching `X-CSRF-Token`. Sessions last eight hours, are stored in process memory, and are invalidated by restart. There is a cap of 256 sessions and a per-peer login-attempt window. `GET` reports authentication state; `DELETE` revokes the session and clears its gateway cookies.

The consumer application continues using `sturgis_csrf`. The distinct standalone name prevents a gateway login/logout from overwriting the consumer's CSRF cookie when the two services use different ports on the same host; browser cookie identity does not include a port.

The frontend never stores the administrator key in query state, mutation state, localStorage, or sessionStorage. The embedded Sturgis application reuses consumer principal/CSRF handling instead of exchanging a standalone administrator key.

## Inference app keys

A gateway app key is returned once at creation. SQLite stores its SHA-256 digest, display prefix, name, tenant, permitted model names, and revocation state. Empty `models` means all names available to that tenant. A nonempty list permits only those model aliases or route names. Route resolution then selects its provider targets; permission for one route does not automatically permit direct access to all its targets.

Authentication is repeated immediately before provider dispatch. Rotation/revocation does not retroactively undo a request already accepted by an upstream server. Usage records identify the gateway client key without storing its complete value.

## Native account providers

`GatewayAuth` reads `STURGIS_GATEWAY_GEMINI_CLIENT_ID` and
`STURGIS_GATEWAY_GEMINI_CLIENT_SECRET` from the process environment at construction.
Trusted `ModelGateway(auth_configs={"gemini-cli": {...}})` configuration overrides
these values through `client_id` and `client_secret`. Use a registered OAuth
application with the documented callback; there are no bundled Gemini defaults.
Missing or malformed values produce a sanitized `auth_not_configured` HTTP 503
before session writes, provider requests, callback listeners or refresh leases.
The browser cannot supply these operator credentials. The client secret is never
included in public account/provider projections or errors; the public client ID
is necessarily part of the provider authorization URL. Restart the gateway after
changing environment values. The application does not automatically load `.env`.

| Provider         | Authorization                | Bootstrap and execution                                                                                                                                                                                                                      |
| ---------------- | ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `github-copilot` | GitHub device authorization  | Exchanges the account credential for a current Copilot token, fetches permitted chat models, and selects Chat Completions, Responses, or Anthropic transport from model metadata or recognized native model IDs for explicit manual imports. |
| `codex`          | Authorization code with PKCE | Requires the account routing identity from the trusted token response. Uses the Codex Responses endpoint and account headers; the adapter sets `store:false` and applies Codex-specific request restrictions.                                |
| `gemini-cli`     | Authorization code with PKCE | Discovers an actual Code Assist project/default tier, completes account bootstrap, fetches actual account models, and wraps Gemini requests with the project identity.                                                                       |
| `kiro`           | AWS device authorization     | Registers the supported public client, selects the account profile, discovers account models, and uses Kiro's AWS event-stream response.                                                                                                     |

The `auth/providers` projection exposes provider ID, name, flow, description, registered default redirect URI, and supported option names. It does not expose token endpoints or server credentials. Kiro accepts optional `region` and `start_url`; Gemini CLI accepts `project_id`; other option keys are rejected.

A native account's endpoint and provider are bound together. `ModelGateway._account` rejects an account used with another provider or endpoint. Only a server-resolved credential binding enters `AuthTransport`; clients cannot submit arbitrary account transport bindings.

## Sign-in state and one-time material

`GatewayAuth.start` creates a tenant-bound sign-in session with a 900-second maximum lifetime. Device-code material and PKCE verifiers are encrypted. A digest of PKCE state is compared in constant time at completion; the raw correlation state is also present in the persisted/public authorization URL. It is not the secret verifier. Public session fields include status, expiry, poll interval, provider login URL/code when required, and eventual account ID; access/refresh tokens are excluded.

Device polling observes `next_poll_at`. An early poll returns the current public observation without another token exchange. Provider `slow_down` responses increase the interval by five seconds, capped at the configured implementation limit. Denial and expiry become explicit terminal states.

Browser authorization exchanges only the code belonging to the same tenant, session, state, and registered redirect. The gateway persists the encrypted verifier and session atomically before returning the authorization URL to the client. A stale or mismatched callback fails; it does not become a login for a different session.

`auth_state.finish_session` creates the account and clears temporary credentials in one transaction after checking the session is still pending and unexpired. A cancelled session cannot resurrect into a connected account because a slow network exchange returned later.

## Loopback callbacks and hosted deployments

The default installed-client callback addresses are:

- Codex: `http://localhost:1455/auth/callback`
- Gemini CLI: `http://localhost:8085/oauth2callback`

`CallbackListeners.watch` attaches only to a provider-validated HTTP loopback redirect. It binds `127.0.0.1` on the registered port, maps the random state to tenant/session/path/port, and returns `callback_mode:"automatic"`. A port conflict, unsupported non-loopback redirect, or listener-capacity condition returns `callback_mode:"manual"`; the API does not claim a listener exists when binding failed.

The callback parser bounds request headers to 16 KiB, waits at most five seconds for the header, accepts GET with exactly one code and state, and requires the registered path and actual local port. It maintains at most 128 watched sessions and eight concurrent callback tasks. Responses do not echo codes or raw provider errors. Their no-store, no-referrer, and restrictive CSP headers avoid turning callback material into a generic web page asset.

Successful callbacks call the same state-bound completion operation used by the manual fallback. The browser sees a short return-to-gateway message. Cancellation/completion removes the watched session; unused listeners close. Startup can restore unexpired pending sessions, and service shutdown closes listeners and pending tasks.

An owned maintenance task checks listener expiry every second while listeners exist. Closing a browser tab therefore cannot keep its callback port bound indefinitely. Retired listeners drain in separately tracked tasks: awaiting a server's close from its own callback would deadlock on that same connection. Expiry of another port remains independent of an in-flight exchange. Shutdown first rejects new listeners, then cancels maintenance and active handlers and joins the retired-listener drains. The idle maintenance task exits when no listeners remain.

A loopback callback reaches the browser machine. A hosted operator therefore needs the appropriate registered OAuth application/allowed redirect configuration for its deployment, or the supported manual completion path. Arbitrary redirects are rejected. Do not rewrite registered redirect URIs merely to match a proxy's public model endpoint.

## Refresh and revocation fences

`GatewayAuth.credential` resolves current account state and refreshes near expiry. It combines per-process singleflight locking with a persisted `auth_state.claim_refresh` lease so another process cannot concurrently rotate the same token. No database transaction stays open while calling the token endpoint.

The lease records ownership and expiry. `finish_refresh` commits only if the account revision, connected state, and lease still match. Disconnect or a newer writer wins. If a rotating token exchange was interrupted or its result is uncertain, the account requires a new sign-in; it is unsafe to blindly replay a possibly consumed refresh token. A still-active lease reports a retryable busy condition instead.

Internal execution bindings include the resolved account revision. A forced refresh after an authentication failure checks that revision so a stale 401 cannot rotate an account that another request already refreshed. Blocking SQLite auth operations run in worker threads; transaction contention must not block the asyncio input/output loop.

Copilot account credentials and short-lived Copilot execution tokens have distinct checks. Kiro's application registration expiry is checked before refresh. Gemini project discovery uses actual server results; it does not invent a project when onboarding fails.

Disconnecting a native account clears its local encrypted token bundle and marks it disconnected. Removing an imported connection is a separate action and does not necessarily destroy its account credential. Applications must present these as different lifecycle operations.

## Native wire limits and unsupported behavior

`AuthTransport` discovers models from the account's real endpoints. It does not return a static fallback list when discovery fails. Public native catalog responses are bounded; internal/image-only or disabled models are filtered where provider metadata identifies them.

Copilot and Codex delegate matching formats to the common transport with server-owned authorization headers. Copilot Anthropic requests preserve its required bearer authentication. Codex adapts the Responses body for the account endpoint rather than assuming the public API contract is identical.

Gemini CLI wraps the canonical request with its resolved project, unwraps streamed response envelopes, and uses the Gemini translator. Provider thinking text is not silently emitted as ordinary answer text. Bounded tool-call signature correlation preserves function history where supported.

Kiro supports text, inline images, and matched function-call/result history. `kiro_request` explicitly rejects unsupported structured-output modes, reasoning overrides, and forced tool choice. `aws_events` checks the prelude and full-frame CRCs, validates header encoding, bounds each frame to 1 MiB, and decodes strict JSON. A partial frame or malformed tool-argument JSON fails. Clean EOF after complete valid frames is a native terminal boundary; EOF in the middle of a frame is not success. Quota percentages and billing credits are not invented as token counts.

Both public and account transports use the network policy's aggregate byte limit and deadline. Current defaults are 8 MiB and 30 seconds. The one-item producer queue preserves cancellation, backpressure, and HTTP-context ownership throughout streaming.

## Implementation sources

- [model_gateway/auth.py](/reference/files/model_gateway/auth.py/)
- [model_gateway/auth_state.py](/reference/files/model_gateway/auth_state.py/)
- [model_gateway/auth_transports.py](/reference/files/model_gateway/auth_transports.py/)
- [model_gateway/callbacks.py](/reference/files/model_gateway/callbacks.py/)
- [model_gateway/api.py](/reference/files/model_gateway/api.py/)
- [model_gateway/store.py](/reference/files/model_gateway/store.py/)

[Architecture](architecture.md) · [HTTP API](http-api.md) · [Frontend](frontend.md)
