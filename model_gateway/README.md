# Sturgis Model Gateway

An independent Python model gateway with a Material 3 dashboard, live provider catalogs, API-key and native account connections, model routes, scoped client keys, and factual usage records. It can run on its own or be composed behind Sturgis's consumer identity boundary. The package contains no agent, planner, conversation, or memory ownership.

```sh
python -m model_gateway serve --host 127.0.0.1 --port 8790
```

With compiled assets present, open `http://127.0.0.1:8790`. The CLI prints the private administrator-key file location. Sign in, connect a provider, import models, then create an app key. Ordinary clients use the displayed `/v1` endpoint, their gateway app key, and an exact namespaced model ID or route alias.

For checkout builds, run `npm run build:web` from the repository root. The standalone entry is `frontend/gateway.html`; the agent application is a separate frontend entry. A custom `--web-root` must contain the built `gateway.html` and `assets` directory.

## Public interfaces

- `ModelGateway`: connection/discovery/model/route/key/usage orchestration and bounded inference.
- `GatewayHTTP`: management/inference HTTP adapter with an injected management authorizer.
- `create_app`: standalone Starlette composition with administrator authentication.
- `GatewayStore`: tenant-scoped SQLite objects, encrypted upstream secrets, hashed app keys, and usage.
- `GatewayAuth`: server-owned device/PKCE sessions, account bootstrap, credential refresh, and revocation.
- `GatewayControls`: revision-checked tenant limits, transactional minute-rate admission, and credential-free configuration transfer.
- `GatewayTransport` and `AuthTransport`: policy-bound public and account-specific protocol execution.

Management uses `/api/v1/gateway/*` and a `gateway/1` envelope in standalone mode. The embedded consumer uses its existing authenticated account and `consumer/1` envelope. Inference uses model-client protocol responses; app keys never authorize management.

## Data and execution boundaries

The default standalone root is `~/.local/share/sturgis-gateway`. Preserve `gateway.sqlite`, `master.key`, and the separate `admin.key` as private operator data. Upstream credentials are AES-GCM encrypted with tenant/reference associated data. Client API keys are returned once and stored as hashes.

Inference rechecks client authority and connection revision before dispatch. Eligible failures may choose another route target only before a response chunk has been emitted. Usage records preserve missing token counts as unknown, including failures and manual-model checks. The network layer pins validated DNS destinations to actual sockets and bounds bytes, time, and concurrent requests.

The dashboard can export public connections, models, routes, and settings. Import allocates new IDs and disables every imported connection until explicit reconnection; secrets, native accounts, app-key authority, and usage are not transferred. Imported settings are not applied. This metadata transfer is distinct from preserving the entire stopped private storage root.

See [third-party notices](THIRD_PARTY_NOTICES.md) for provider/protocol provenance and license boundaries.

Read the authored guides for complete contracts and examples:

- [User provider manual](../documentation/users/src/content/docs/guides/model-gateway.md)
- [Standalone operation](../documentation/users/src/content/docs/getting-started/standalone-gateway.md)
- [Architecture](../documentation/developers/src/content/docs/guides/gateway/architecture.md)
- [HTTP API](../documentation/developers/src/content/docs/guides/gateway/http-api.md)
- [Authentication](../documentation/developers/src/content/docs/guides/gateway/authentication.md)
- [Frontend mechanisms](../documentation/developers/src/content/docs/guides/gateway/frontend.md)

Source presence, preset availability, and controlled test fixtures do not constitute a live provider-account certification. Release qualification is tracked by `spec/sturgis-03/spec.kvx` and its evidence.
