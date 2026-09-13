---
title: Gateway frontend implementation
description: Independent dashboard composition, request and credential handling, interaction contracts, and frontend verification.
---

The gateway dashboard is a React application surface that does not require an agent, an agent runtime, or the agent command bus. It shares Sturgis branding, Material-inspired components, and the HTTP envelope reader with the consumer application. Gateway metadata belongs to the authenticated gateway tenant.

## Entrypoints and ownership

| File | Responsibility |
| --- | --- |
| `frontend/gateway.html` and `frontend/src/gateway-main.tsx` | Standalone Vite entry, React root, query client, and shared styles. The backend serves the built gateway entry independently. |
| `pages/gateway/StandaloneGateway.tsx` | Administrator session query, masked login, direct login/logout requests, query-cache clearing on logout, and a skip link to the dashboard. |
| `App.tsx` and `components/Shell.tsx` | Installation-level Model Gateway navigation. The gateway branch is outside the selected agent's context. |
| `pages/gateway/GatewayPage.tsx` | Provider catalog, connected accounts and connections, model aliases, route ordering, app keys, and factual usage. |
| `pages/gateway/ProviderDrawer.tsx` | Provider setup/edit state, live discovery, manual model import, selection, and direct credential-bearing submissions. |
| `pages/gateway/NativeAuthDialog.tsx` | Provider-owned device/browser sign-in sessions, polling, cancellation, callback recovery, and transition to account-backed discovery. |
| `pages/gateway/GatewayControlsPanel.tsx` | Lazily loaded limits form and credential-free JSON download/import with preview, confirmation, bounds, and reconnect guidance. |
| `pages/gateway/AgentGatewayModel.tsx` | Agent-only consumer of the shared model catalog and the agent binding endpoint. |
| `pages/gateway/useGateway.ts` | Account-scoped public metadata queries and direct actions with invalidation. |
| `pages/gateway/gateway.css` | Responsive catalog, drawer, native sign-in, standalone login, and gateway surface styles. |
| `lib/types.ts` | Public gateway DTOs, including nullable usage and native sign-in states. |
| `pages/SettingsPage.tsx` | Agent model choice and preserved advanced legacy provider configuration. |

The dedicated backend serves `gateway.html` at its root. The consumer application instead selects the gateway page from its shell. Neither entry constructs a private agent worker merely to list or connect providers.

## Requests and cache boundaries

`useGatewayQuery(accountId, resource)` reads `/api/v1/gateway/{resource}` with a query key of `["gateway", accountId, resource]`, a 15-second stale time, no automatic retry, and the query's abort signal. The account component of the key prevents one account's catalog from masquerading as another's. The backend derives the real tenant from its authenticated session; the browser query key is not an authorization grant.

`useGatewayAction` performs a direct request, keeps pending/error state locally, and invalidates the account's gateway query prefix after success. It does not use TanStack mutation records. Public list queries contain credential presence and key prefixes, not upstream secrets or full issued keys.

`ProviderDrawer` directly calls discovery/connect because the submitted API key must stay out of query keys, mutation caches, browser storage, URL parameters, and persisted command receipts. The password exists only in mounted component state. Failure retains the form so the user can correct it. Success and close clear the password. Closing during a pending submission is disabled to preserve the outcome relationship.

Native sign-in similarly uses direct requests. The browser receives public account/session metadata, provider URLs, device codes, and optional callback recovery input; provider access/refresh tokens and PKCE verifiers remain server-side. A one-time app key is kept only in the reveal modal until dismissal. Clipboard copying is explicit and failure leaves a selectable value with an actionable error.

The shared API reader validates `consumer/1`, and accepts `gateway/1` only for gateway paths. Cookie requests carry credentials; mutations use the CSRF cookie value in `X-CSRF-Token`. Standalone session responses cache only authentication state. Logout clears the query client and unmounts secret-bearing components.

`gateway-main.tsx` calls `configureApiSession("gateway")` before rendering so standalone requests use `sturgis_gateway_csrf`. The default consumer mode uses `sturgis_csrf`, including when the consumer displays its embedded gateway. The API path alone does not select a session mode. Missing gateway CSRF material never falls back to the consumer cookie. This keeps two applications on different ports of the same host from overwriting each other's session token.

## Provider and account interactions

The searchable preset catalog combines API-key, local, gateway, custom, and account-sign-in providers. Category text describes access conditions; a free-tier label does not assert unlimited or entitlement-free use. An OAuth preset selects a matching server-provided auth descriptor rather than opening a generic API-key field. Existing connected accounts can be used directly to import another model selection.

The provider drawer initializes endpoint/name from the preset or existing connection. Local and compatible gateway presets may accept an optional API key. Account-backed discovery sends `auth_account_id`, omits the password field, and locks the endpoint to the registered account transport.

`Load models` replaces the current discovered list. Search changes visibility without changing the selected model IDs. The connect operation submits the selected IDs; the server rediscovers before committing ordinary imports. Manual IDs under Advanced set `catalog_mode:"manual"` and display the short inference-check requirement. A later live discovery resets that manual mode. Capability defaults are server-owned, so the simple path requires no token-limit calculations.

Updates, refresh, and deletes send the displayed `expected_revision`. A revision conflict remains an error instead of silently overwriting another editor. Successful writes invalidate connections, models, routes, and usage through the common gateway query prefix. Refresh does not silently import every newly advertised model.

The auth dialog starts a server-owned session with the descriptor's registered redirect and any explicitly entered supported options (`region`, `start_url`, `project_id`). It schedules polling from `poll_after`, clears timers when state changes/unmounts, and stops automatic polling on an actionable error. Device sign-in shows the provider code and verification link. Browser sign-in opens the provider URL and waits for the server's callback listener; the collapsed callback-URL control provides recovery when automatic return is unavailable. A completed session opens the model import drawer with its account ID. Cancel waits for server cancellation before closing.

The server's `callback_mode:"manual"` changes the guidance to explicitly request callback-URL completion. The normal automatic mode leads with provider return and polling. Account disconnect has its own revision-checked confirmation. Edit on an imported or disconnected account connection starts sign-in and retains the original connection until the new account completes, then PATCHes that connection so route references remain stable.

## Operational controls and transfer

Advanced limits/transfer controls mount only when expanded. Settings queries contain public resource limits; edits carry `expected_revision`. Numeric inputs reflect operator maximums and the minute-rate range, while the server remains authoritative. Failed writes retain user input and show errors. Successful writes invalidate public metadata and report completion.

Download calls `/gateway/export` directly, creates a JSON Blob and a temporary object URL, triggers an explicit browser download, then revokes the object URL. No export query or mutation record is created. Import reads a selected file up to 2 MiB, checks the advertised credential-free format, and previews its connection/route counts. Only confirmation posts `{archive}` to the server, which performs full structural and authority validation. Failed import remains retryable; cancellation discards the pending archive. Success explains new IDs, reconnection, fresh keys, and unapplied settings. Imported connections cannot become executable through metadata refresh alone.

## Models, routing, keys, and usage

Model labels are display text. The copy action uses the exact gateway ID: `connection_id/upstream_model_id` or a route's ID. Route editing accepts direct imported models, not other routes, preserving bounded resolution. Up/down controls change priority; removing a target affects only the draft until save. Existing route edits carry the current revision.

Route deletion has a separate confirmation and submits its current revision. A failed delete stays visible for recovery. Success invalidates routes and models; provider connections remain intact. Gateway secondary buttons use the on-container foreground token so hovered container colors retain readable contrast.

The app-key creation dialog asks for a recognizable name. The dashboard currently creates an unrestricted key for that tenant; the HTTP API additionally accepts an explicit model/route allowlist. The result is revealed once and then cleared. Revocation requires confirmation and keeps a failed operation visible for retry.

Usage describes provider attempts. A fallback can create multiple attempts for one client request. The backend may return `tokens:null`; the UI renders **Usage not reported**, retains partial aggregate totals, and explains the unreported count. A completed provider attempt is a success state even when its provider did not report token counts.

## Agent binding and migration

`AgentGatewayModel` is the only gateway component using `useAgent`. It reads shared public models, then posts `{model: alias}` to `/api/v1/agents/{id}/gateway-model`. The server owns restricted key creation, runtime credential installation, probing, and activation. **Use model** becomes **Checking model…** while pending. On success the component invalidates that agent's state and reports readiness. Upstream credentials never become component input.

Gateway connection rows show **Models imported** for a saved connected catalog; this is deliberately separate from the agent's runtime check. A failed check remains inline beside the attempted model, with **Could not use MODEL NAME**, the server's safe recovery message, and **Retry**. The component refreshes agent state after failure as well as success, so it does not continue displaying stale readiness after a configuration changed during a check. The failed candidate is not marked selected. Ordinary chat after successful selection remains the final user-path check.

The current provider's `tools` field controls **Chat and tools** versus **Chat only**. Selection toasts use the returned Provider record, so a successful text fallback explicitly reports that tool use is unverified. A selected chat-only row retains **Selected** and exposes **Retry tool check**, using the same selection endpoint and pending/error controls. Retry may restore tool support if the provider demonstrates it; it cannot make a text-only provider support actions or grant agent permissions. The backend gives acknowledged same-model retries fresh worker request identities while retaining pending identities for reconciliation.

The Settings page retains direct provider and credential workflows inside **Advanced: legacy model connections, credentials & usage**. New model selection does not delete older credentials or connections; migration can be tested before an owner removes them.

## Accessibility and validation

The catalog uses named buttons, pressed category chips, and a labeled search field. Drawer/modal primitives manage keyboard focus, dismissal, and dialog naming. Every password/control has a visible label. Pending actions disable conflicting operations, errors use `role="alert"`, and sign-in progress uses `role="status"`. The standalone page has a skip link. CSS supports narrow viewports, reduced motion, and theme tokens shared with the surrounding product.

Focused coverage lives in `frontend/tests/gateway.test.tsx`, with shared Settings/App/Shell behavior tested in their existing suites. Tests exercise independence from agents, discovery and failed reconnect recovery, secret-cache boundaries, aliases, route order, key creation/revocation, standalone sessions, native sign-in/cancellation, optional options, manual imports, and nullable usage. Type checking verifies the public DTO consumers.

Mocked UI tests and screenshot/axe inspection establish rendering and interaction behavior. They do not establish vendor access or the actual backend contract. Qualification must also run the real gateway HTTP/browser journeys against controlled upstream servers, the complete authored TypeScript coverage gate, and both documentation-site gates. Actual vendor sign-in remains separate evidence.

## Implementation references

- [GatewayPage.tsx](/reference/files/frontend/src/pages/gateway/GatewayPage.tsx/)
- [ProviderDrawer.tsx](/reference/files/frontend/src/pages/gateway/ProviderDrawer.tsx/)
- [NativeAuthDialog.tsx](/reference/files/frontend/src/pages/gateway/NativeAuthDialog.tsx/)
- [StandaloneGateway.tsx](/reference/files/frontend/src/pages/gateway/StandaloneGateway.tsx/)
- [AgentGatewayModel.tsx](/reference/files/frontend/src/pages/gateway/AgentGatewayModel.tsx/)
- [useGateway.ts](/reference/files/frontend/src/pages/gateway/useGateway.ts/)
- [GatewayControlsPanel.tsx](/reference/files/frontend/src/pages/gateway/GatewayControlsPanel.tsx/)
- [gateway-main.tsx](/reference/files/frontend/src/gateway-main.tsx/)
- [gateway.html](/reference/files/frontend/gateway.html/)
- [gateway.css](/reference/files/frontend/src/pages/gateway/gateway.css/)
- [App.tsx](/reference/files/frontend/src/App.tsx/)
- [Shell.tsx](/reference/files/frontend/src/components/Shell.tsx/)
- [SettingsPage.tsx](/reference/files/frontend/src/pages/SettingsPage.tsx/)
- [types.ts](/reference/files/frontend/src/lib/types.ts/)
- [api.ts](/reference/files/frontend/src/lib/api.ts/)

[Architecture](architecture.md) · [HTTP API](http-api.md) · [Authentication](authentication.md)
