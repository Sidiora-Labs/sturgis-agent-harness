---
title: "Gateway HTTP API contracts"
description: "Use the standalone and embedded management APIs, native account lifecycle, model inference formats, and typed error boundaries."
---

The management base path is `/api/v1/gateway`. Standalone management returns a `gateway/1` envelope; embedded Sturgis management returns its authenticated `consumer/1` envelope. Inference uses model-client protocol responses and has a separate credential boundary.

## Base URLs and authentication

| Composition | Management | Inference base | Management authority |
| --- | --- | --- | --- |
| Standalone defaults | `http://127.0.0.1:8790/api/v1/gateway` | `http://127.0.0.1:8790/v1` | Administrator bearer key or authenticated browser session. |
| Embedded Sturgis | `ORIGIN/api/v1/gateway` | `ORIGIN/model-gateway/v1` | Existing Sturgis principal and CSRF rules. |

Management parameters never contain a tenant ID. The authenticated boundary supplies it. Inference keys are tenant-scoped app keys and cannot authorize management.

For browser mutations, send the installation's session cookie, exact Origin, and `X-CSRF-Token`. Read that token from `sturgis_gateway_csrf` for the standalone dashboard or `sturgis_csrf` for the embedded consumer. Standalone non-browser management can use the administrator key as `Authorization: Bearer …`. Host still has to match the configured public origin.

Successful management envelope:

```json
{"version":"gateway/1","data":{"disconnected":true},"error":null}
```

A typed standalone management failure:

```json
{"version":"gateway/1","data":null,"error":{"code":"conflict","message":"This item changed. Refresh it and try again.","retryable":false}}
```

Read HTTP status and the error object. Public errors are sanitized; upstream response bodies and secrets are not echoed. Browser code accepts `gateway/1` only on gateway paths, preserving the consumer protocol version check elsewhere.

## Management endpoints

All paths below are relative to `/api/v1/gateway`. JSON mutations use `Content-Type: application/json`. Unknown fields and missing required fields are rejected at the boundary. `expected_revision` is the current nonnegative integer revision; an initial create uses zero where applicable.

| Method and path | Request | Response |
| --- | --- | --- |
| `GET /status` | None | `endpoint`, connection/model counts, and gateway version. |
| `GET /presets` | None | Preset array including ID, name, endpoint, protocol, auth/category metadata, description, optional key URL, defaults, and integration metadata. |
| `GET /connections` | None | Current tenant's public connections; secret references and values are excluded. |
| `POST /discover` | `preset_id`; optional `endpoint`, `api_key`, `connection_id`, `auth_account_id` | `{models:[…],source:"live"}`. Does not persist a new connection. |
| `POST /connections` | `preset_id`, `name`, `models`; optional connection fields below | New public connection. |
| `PATCH /connections/ID` | Same configuration plus required `expected_revision` | Updated public connection. |
| `POST /connections/ID/refresh` | `expected_revision` | Refreshed current import set and revision. |
| `DELETE /connections/ID` | `expected_revision` | `{disconnected:true}`; prunes dependent route targets. |
| `GET /models` | None | Imported namespaced model aliases and valid named routes. |
| `GET /routes` | None | Saved route objects. |
| `POST /routes` | `id`, `name`, `models`; optional `strategy`, `expected_revision` | Created/updated route with revision. |
| `DELETE /routes/ID` | `expected_revision` | Deletes the named route without deleting its provider connections or models. |
| `GET /keys` | None | Key metadata, prefix, allowed model names, creation time, revoked state. |
| `POST /keys` | `name`; optional `models` | Metadata plus the one-time `key`. |
| `DELETE /keys/ID` | No configuration fields | `{revoked:true}`. |
| `GET /usage` | None | Attempt counts, reported token sums, failures, unreported count, and up to 50 recent records. |
| `GET /settings` | None | Current `revision`, `request_timeout`, `max_concurrency`, `requests_per_minute`, and `operator_limits`. |
| `POST /settings` | `expected_revision`, `request_timeout`, `max_concurrency`, `requests_per_minute` | Updated settings and revision. |
| `GET /export` | None | Credential-free `sturgis-gateway-config/1` metadata archive. |
| `POST /import` | `archive` | Imported counts, old/new connection-ID map, reconnection required, and settings not applied. |

Connection fields are `endpoint`, `api_key`, `selected_model`, `expected_revision`, `auth_account_id`, and `catalog_mode`. `catalog_mode` is `discover` by default or explicitly `manual`. Model IDs are upstream IDs on a connection, not already prefixed gateway aliases. `selected_model`, when supplied, must belong to the import set. It does not select a model for an agent.

Public connection metadata includes `id`, `revision`, `name`, `preset_id`, `endpoint`, `protocol`, imported model records, selected model, `credential_configured`, status/error, discovery time, account reference if present, and catalog mode. A model record contains its upstream ID, display name, context/output limits, tools flag, and metadata provenance where available.

Changing a provider or endpoint does not silently send its saved API key to a new address. Reenter the key for the new destination. Native accounts instead require the matching provider and account-bound endpoint.

## Connection examples

Discover a default local Ollama server:

```json
{"preset_id":"ollama","endpoint":"http://127.0.0.1:11434/v1"}
```

Use actual IDs from its response when creating a connection:

```json
{"preset_id":"ollama","name":"Local workstation","endpoint":"http://127.0.0.1:11434/v1","models":["SERVICE_MODEL"]}
```

A protected API provider adds `api_key` directly to the request body. Do not log the body or place it in generic client mutation-recovery storage. Reusing an existing connection for discovery may omit the key when its provider and endpoint remain unchanged.

Manual import of a custom server:

```json
{"preset_id":"custom-openai","name":"Private server","endpoint":"https://model.example.com/v1","models":["SERVICE_MODEL"],"catalog_mode":"manual"}
```

Use the actual custom preset ID returned by `/presets`; the protocol-specific ID is an API value, not a display label. Manual import checks at most 16 models using a 16-token text probe. Discovered import permits at most 256 selected models. The gateway does not save an unadvertised model as discovered, or label preset fallback limits as a measured context window.

## Routes and scoped app keys

A route uses a slash-free ID not beginning with `conn_`, 1–16 distinct imported model aliases, and strategy `priority` or `round_robin`:

```json
{"id":"everyday","name":"Everyday assistant","models":["conn_FIRST/MODEL_A","conn_SECOND/MODEL_B"],"strategy":"priority","expected_revision":0}
```

Routes cannot target other routes. Later updates use the returned revision. Deleting a provider connection prunes its targets and deletes empty routes.

Create a key restricted to that route:

```json
{"name":"Research app","models":["everyday"]}
```

The response includes the complete key once. `GET /keys` never returns it again. Empty or omitted `models` permits all model names available to the key's tenant. Revoking a key does not remove the associated providers or models.

## Limits and configuration transfer

Settings example; use the actual current revision and operator ceilings returned by GET:

```json
{"expected_revision":0,"request_timeout":15,"max_concurrency":2,"requests_per_minute":60}
```

Timeout must be a finite positive number within the operator's timeout ceiling. Concurrency is an integer from one to the operator's maximum. Requests per minute is an integer from one to 10,000. The transactional fixed-minute counter counts authenticated client requests, including requests admitted by concurrent processes. Provider usage still records every actual attempt. A request exceeding admission returns a retryable 429.

Export returns exactly `format`, `created_at`, `credentials_included:false`, `connections`, `routes`, and `settings`. Each connection includes its public ID, name, preset, endpoint, protocol, models, and optional selected model/catalog mode. Each route includes ID/name/models/strategy. No API keys, credential references, native tokens, app-key authority, or usage are exported.

Submit the complete decoded result as `{archive: exportResult}`. Imports are bounded to 2 MiB, 256 connections, 256 routes, and each route's existing 16-model limit. The server validates endpoints, known presets/protocols, distinct model IDs, selected model, catalog mode, and settings metadata before committing. It does not send a request or create a network-policy exception while importing.

Portable archive metadata allows timeout up to 300 seconds, concurrency up to 128, and requests per minute up to 10,000; these are validation ceilings, not target execution grants. The target's operator ceilings remain authoritative, and archived settings remain unapplied.

Example import result:

```json
{"imported_connections":1,"imported_routes":1,"reconnection_required":true,"settings_applied":false,"connection_ids":{"conn_OLD":"conn_NEW"}}
```

Every connection receives a new ID and `reconnect_required`; routes receive new IDs and are rewritten to the new targets. Existing records are preserved. New app keys and explicit provider reconnection are required. Native reconnection supplies a newly authenticated `auth_account_id` through PATCH to preserve imported connection identity. Imported settings are not applied; submit a separate revision-checked settings write when intended.

## Native account lifecycle

| Method and path | Request | Response/behavior |
| --- | --- | --- |
| `GET /auth/providers` | None | Native sign-in descriptors with ID, name, flow, description, registered default redirect, and optional-setting names. |
| `POST /auth/start` | `provider_id`; optional `name`, `redirect_uri`, `options` | Pending public session and instructions; attaches a permitted callback listener when possible. |
| `POST /auth/SESSION/poll` | `{}` | Current session, one due device exchange, or account bootstrap progress. |
| `POST /auth/SESSION/complete` | `state`, `code` | Completes the matching PKCE exchange, then account bootstrap. |
| `DELETE /auth/SESSION` | No configuration fields | Cancels the pending sign-in and removes callback watching. |
| `GET /auth/accounts` | None | Public account metadata: ID, provider, name, status, expiry, revision. |
| `DELETE /auth/accounts/ACCOUNT` | `expected_revision` | Disconnects the current account and destroys its local token material. |

Start a device-code session:

```json
{"provider_id":"github-copilot","name":"Personal Copilot"}
```

Start Gemini CLI with an explicitly required project:

```json
{"provider_id":"gemini-cli","name":"Work Google account","options":{"project_id":"YOUR_PROJECT_ID"}}
```

Session status is `pending`, `exchanging`, `connected`, `cancelled`, `expired`, or `failed`. Relevant public fields include ID/provider/name/flow, expiry, `poll_after`, authorization or verification URL, user code, eventual `account_id`, error code, and callback mode. Honor `poll_after`; do not generate parallel polling exchanges. Device material, PKCE verifier, access token, and refresh token stay server-side.

A connected account becomes a model connection only after discovery/import. Supply `auth_account_id` in `/discover` and `/connections`; do not copy its access token into `api_key`.

```json
{"preset_id":"github-copilot","auth_account_id":"account_EXAMPLE"}
```

The same tenant and matching preset/endpoint must own the account. See [authentication internals](authentication.md) for callback listeners, registered redirects, refresh leases, and native wire limits.

## Standalone administrator session endpoints

`GET /session` returns `{authenticated:boolean}`. `POST /session` accepts only `admin_key` and sets the bounded browser session cookies. `DELETE /session` requires an authenticated CSRF-valid mutation, deletes that session, and clears cookies. These operations are a standalone composition boundary; the embedded Sturgis app uses its existing account session.

Example non-browser management reader using a private prompt:

```python
import getpass
import json
import urllib.request

origin = "http://127.0.0.1:8790"
admin_key = getpass.getpass("Gateway administrator key: ")
request = urllib.request.Request(
    origin + "/api/v1/gateway/connections",
    headers={"Authorization": "Bearer " + admin_key},
)
with urllib.request.urlopen(request, timeout=30) as response:
    envelope = json.load(response)
for connection in envelope["data"]:
    print(connection["name"], connection["status"])
```

This does not establish a browser session or authorize an inference request. Use an app key at the inference endpoints.

## Inference endpoints

All inference paths are relative to the composition's inference base. Supply exactly one gateway app key. Chat Completions, Models, and Responses accept `Authorization: Bearer …`. The Messages endpoint also accepts the native `x-api-key` form, but rejects ambiguous simultaneous key headers.

| Method and path | Contract |
| --- | --- |
| `GET /models` | OpenAI-style model list filtered by the app key's allowed names; includes usable route aliases. |
| `POST /chat/completions` | Full `messages` history and gateway `model` alias. Supports streaming or an assembled non-stream response. |
| `POST /messages` | Anthropic Messages client shape. Preserves native blocks on a matching target; translates supported ordinary content on other targets. |
| `POST /responses` | OpenAI Responses client shape with full supplied input/history. Native passthrough where supported; ordinary cross-protocol conversion otherwise. |

Example Chat Completions request:

```json
{"model":"everyday","messages":[{"role":"user","content":"Reply with a short greeting."}],"max_tokens":64,"stream":true}
```

Example Messages request:

```json
{"model":"everyday","max_tokens":64,"messages":[{"role":"user","content":"Reply with a short greeting."}],"stream":true}
```

Example Responses request:

```json
{"model":"everyday","input":"Reply with a short greeting.","max_output_tokens":64,"store":false,"stream":true}
```

The selected model must support the requested capabilities. Chat `max_tokens` and `max_completion_tokens` accept integers from 1 through 2,000,000; booleans, strings, floats, and larger values are invalid requests. Each target still enforces its own imported output limit and returns `output_limit` before upstream dispatch when the requested allowance exceeds it. Omitting both fields uses that imported allowance. The generic envelope bound matches the catalog/translator domain, so a valid advertised allowance above 262,144 is not rejected by a smaller unrelated request bound.

For example, an imported model advertising 384,000 output tokens can receive `max_tokens:384000`; `384001` fails that model's limit even though it is within the generic domain. The allowance does not mean the provider consumed that many tokens: accounting uses its actual reported usage. Requests are also bounded to 4 MiB, 1–2048 chat messages, one response choice, and at most 128 tool definitions. Tool calls describe client-executed functions; this package does not execute agent tools.

Native Responses references such as `previous_response_id`, `conversation`, background mode, and `store:true` are rejected: the gateway does not provide a provider conversation database. Cross-format conversion also rejects unsupported opaque thinking/history or provider-hosted tools rather than silently discarding them. Native matching-format passthrough can preserve signed blocks that cannot be reconstructed in another protocol.

Streaming Chat Completions emits SSE data records and `[DONE]` only for normal completed iteration. Initial authorization, routing, or upstream failures produce an HTTP error before streaming begins. A failure after streaming begins is delivered in-stream; consumers must inspect terminal/error records, not only HTTP 200. Native Messages/Responses use their own native event envelopes.

OpenRouter may repeat the finished choice in its final usage event. The gateway accepts only a semantically empty repeated choice with the same finish reason and valid nondecreasing accounting, then emits the final normalized usage once. It does not forward a second completion. New output after completion or a missing `[DONE]` remains a failure; see [the normalization contract](architecture.md#openrouter-final-usage-envelopes).

## Usage semantics

`GET /usage` aggregates upstream attempts, including management checks and fallback attempts. Token fields remain nullable per attempt when the provider does not report them. `unreported` identifies missing total usage; aggregate sums contain reported values only. A null count does not become a factual zero. Recent records include model alias, connection ID, outcome, input/output/total counts, convenience `tokens`, and timestamp.

Outcome records include completed, failed, incomplete, refused, and cancelled attempts. They are factual gateway execution records, not settled payment statements.

## Errors and recovery

| Error family | Recovery contract |
| --- | --- |
| `invalid_request`, `invalid_model`, `invalid_route` | Correct the submitted schema or selected model set. |
| `conflict`, `invalid_revision` | Reread the current object and retry its actual revision. |
| `provider_auth`, `credential_unavailable` | Reconnect the correct provider/account credential. |
| `provider_rate_limit`, `provider_unavailable`, `busy` | Retry according to error/policy or use an eligible fallback route before output. |
| `network_origin_denied`, `network_address_denied` | Operator must permit the actual exact origin; do not bypass the network layer. |
| `provider_protocol`, `provider_incomplete`, `provider_bounds` | Preserve any partial response, report the failure, and fix provider/protocol compatibility. |
| `model_denied`, `unauthenticated` | Use a current app key permitted for the requested name. |
| `invalid_auth_state`, `account_reauth_required` | Resume the exact pending sign-in or start a fresh authorized sign-in. |

Agent selection uses the consumer endpoint `/api/v1/agents/ID/gateway-model`, with a consumer response envelope. A failed readiness check returns outer `provider_unavailable` and its original adapter code in `probe_code`. Its message comes from the consumer's static category mapping, including account/key rejection, recognized credit/quota exhaustion, missing models, incompatible requests, timeouts, and stream failures. Preserve this safe message in clients. Unknown provider codes use general recovery text; arbitrary upstream error bodies are never a public troubleshooting payload.

A successful selection returns the actual Provider record. Inspect its `tools` value: `false` can represent a text-only fallback after a failed tool probe and is not a promise of action support. Reposting the same `{model: alias}` after an acknowledged active/failed binding starts a fresh worker request with the existing valid restricted key. Reposting while that binding is pending preserves the request identity to reconcile uncertain delivery. A different model cannot replace that pending operation before reconciliation.

Probe lease renewal preserves the original work generation within the existing bounded provider deadline; it does not extend the request indefinitely. Cancelled, revoked, stale-authority, over-budget, and disabled-profile outcomes do not trigger a new text-only fallback. See [provider lifecycle and settlement](../consumer/vault-provider-settings.md) for task joining, usage preservation, and authority precedence.

## Implementation sources

- [model_gateway/api.py](/reference/files/model_gateway/api.py/)
- [model_gateway/service.py](/reference/files/model_gateway/service.py/)
- [model_gateway/ingress.py](/reference/files/model_gateway/ingress.py/)
- [model_gateway/errors.py](/reference/files/model_gateway/errors.py/)
- [consumer/gateway.py](/reference/files/consumer/gateway.py/)

[Architecture](architecture.md) · [Authentication](authentication.md) · [Frontend](frontend.md)
