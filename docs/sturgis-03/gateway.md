# Independent model gateway

`sturgis-03` introduces a gateway that owns provider connections, authenticated provider accounts, encrypted secrets, model imports, routes, app keys, admission controls, and usage. It runs through `python -m model_gateway serve` without an agent or consumer application. The consumer product can mount the same subsystem; agents and other applications remain clients of its inference endpoint.

## Design and source ownership

- Normative requirements and task qualification: [spec.kvx](../../spec/sturgis-03/spec.kvx).
- Reference audit and source provenance: [reference.md](../../spec/sturgis-03/reference.md) and [9router research](../../.research/9router-gateway.md).
- Package overview: [model_gateway/README.md](../../model_gateway/README.md).
- Gateway package: [model_gateway/](../../model_gateway/).
- Consumer composition and agent binding: [consumer/gateway.py](../../consumer/gateway.py).
- Independent dashboard: [frontend/src/pages/gateway/](../../frontend/src/pages/gateway/).

The reference informs module boundaries and user journeys. Gateway package imports do not depend on `agent` or `consumer`. Provider API keys and native access/refresh tokens belong to gateway storage; an agent receives only a restricted gateway client key and a chosen model name. Legacy agent-owned provider configuration remains available during migration.

## Complete user manuals

| Manual | Covered user journey |
| --- | --- |
| [Model Gateway](../../documentation/users/src/content/docs/guides/model-gateway.md) | Presets, free/local/custom access, one-drawer setup, live/manual imports, model choice, edit/refresh/disconnect, and actionable failures. |
| [Provider accounts](../../documentation/users/src/content/docs/guides/gateway-accounts.md) | Copilot, Codex, Gemini CLI, and Kiro sign-in, device codes, PKCE callbacks, entitlements, refresh, and account recovery. |
| [Routes, app keys, and usage](../../documentation/users/src/content/docs/guides/gateway-routing-keys.md) | Exact model IDs, priority/round-robin behavior, key lifecycle, external client examples, and honest usage. |
| [Standalone gateway](../../documentation/users/src/content/docs/getting-started/standalone-gateway.md) | Installation, launch, administration, private storage, local servers, hosted boundaries, resource limits, and credential-free configuration transfer/reconnection. |
| [Connect an agent model](../../documentation/users/src/content/docs/models/connect-a-model.md) | One-action gateway binding and a non-destructive migration from existing direct provider setup. |
| [Agent credentials](../../documentation/users/src/content/docs/models/credentials.md) | Remaining agent-vault and legacy credential responsibilities. |

## Developer manuals

| Manual | Covered implementation contract |
| --- | --- |
| [Architecture](../../documentation/developers/src/content/docs/guides/gateway/architecture.md) | Every package module, owner boundaries, persistence, tenant authority, network limits, request lifecycle, cancellation, and routing. |
| [HTTP API](../../documentation/developers/src/content/docs/guides/gateway/http-api.md) | Management and inference endpoints, authentication, payload fields, revisions, examples, supported protocols, and errors. |
| [Authentication](../../documentation/developers/src/content/docs/guides/gateway/authentication.md) | Admin sessions, hashed scoped app keys, encrypted provider credentials, sign-in state machines, callbacks, refresh fencing, and native transports. |
| [Frontend](../../documentation/developers/src/content/docs/guides/gateway/frontend.md) | Entrypoints, modules, direct secret handling, query scope, forms, accessibility, agent binding, and test responsibilities. |

## Behavioral boundaries

Discovered imports come from the current remote catalog. A saved discovered connection proves catalog access and validated import, not full model/tool capability. Explicit manual imports perform a bounded short inference check and retain preset fallback metadata. Model IDs remain namespaced by their connection; routes use their own client-visible IDs.

The gateway exposes Chat Completions, Responses, and Anthropic Messages client APIs. Public API-key adapters support OpenAI Chat/Responses, Anthropic, and Gemini upstreams. Native account transports implement their actual bootstrap, model discovery, headers, and wire protocols. Matching native formats preserve supported signed/opaque state; unsupported cross-format transformations and provider-owned stored conversations fail explicitly.

Priority/round-robin fallback can occur only before the first response chunk. Cancellation closes the upstream stream and records an attempt. Token counts are nullable; missing usage is never fabricated. Provider quota and account entitlements still apply to free-tier and native sign-in choices.

## Qualification record

The normative task and documentation state remains in the spec and generated evidence. Authored manuals, source presence, unit tests, and line coverage are separate from actual user-path proof. Root qualification includes the full Python/C++/authored-client coverage floors, controlled HTTP and browser journeys, both complete documentation builds, link/example/source completeness, and the final full documentation gate.

Controlled upstream journeys can exercise the complete HTTP lifecycle without proving access to a real vendor account. No real provider-account sign-in should be claimed unless that exact evidence exists. Use the final integration evidence produced by the root task for current qualification status; this implementation index does not independently mark the feature complete.
