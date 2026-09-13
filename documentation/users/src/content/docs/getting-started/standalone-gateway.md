---
title: "Run Model Gateway on its own"
description: "Start the gateway without an agent, sign into its dashboard, and configure local or private model servers."
---

Sturgis Model Gateway is an independent Python service with its own dashboard and model API. It does not require an agent, a conversation, a native memory process, or the Sturgis consumer application to run. The same model connection UI can also be opened from the Sturgis application's installation sidebar.

## Start from this checkout

Use the repository's Python environment. The dashboard needs the compiled web assets; if they are not already built, build them once from the repository root:

```sh
npm ci
npm run build:web
.venv/bin/python -m model_gateway serve --host 127.0.0.1 --port 8790
```

For an installed package that includes built assets, the equivalent entry point is:

```sh
sturgis-gateway serve --host 127.0.0.1 --port 8790
```

The startup message prints the dashboard URL and the private administrator-key file path. Open `http://127.0.0.1:8790` and enter that administrator key. By default the file is `~/.local/share/sturgis-gateway/admin.key`.

The administrator key authenticates gateway management. After login, your browser uses an HttpOnly session cookie and a separate CSRF cookie; it does not retain the administrator key in local or session storage. Sign out using the dashboard's **Sign out** button. A gateway process restart invalidates its in-memory browser sessions, so sign in again after a restart.

The standalone gateway and Sturgis consumer application use distinct session/CSRF cookies. Signing out of the standalone gateway does not sign you out of the consumer app, including when both are served from different ports on the same host.

If you started the service without built web assets, its HTTP APIs can still run. Build the frontend or supply `--web-root` pointing to a directory containing `gateway.html` and its `assets` directory, then restart to serve the dashboard.

## First working connection

1. Sign in to the dashboard.
2. Select a provider preset. For a local server, choose **Local** and select the server type.
3. Load and select actual models, then connect. Follow the [provider guide](../guides/model-gateway.md) for API keys or the [account guide](../guides/gateway-accounts.md) for native sign-in.
4. Copy the endpoint from the top of the page. The default standalone endpoint is `http://127.0.0.1:8790/v1`.
5. Create an app key under **Access & usage** and copy a model ID under **Models**.
6. Configure your external app with those three values, or run the [Python client example](../guides/gateway-routing-keys.md#worked-example-test-from-any-python-environment).

The endpoint's `/v1` is a base URL. A Chat Completions client appends `/chat/completions`; do not append that path twice. Sturgis's embedded gateway instead uses the base URL shown by that installation, ending in `/model-gateway/v1`.

## Choose storage and listen addresses

```sh
sturgis-gateway serve \
  --root /srv/sturgis-gateway \
  --host 127.0.0.1 \
  --port 8790 \
  --public-origin http://127.0.0.1:8790
```

The root contains gateway state and private keys. It must belong to the service user and be private; do not use a shared writable directory or a symbolic link. The service rejects unsafe existing storage permissions rather than silently changing the ownership of unrelated files.

`--public-origin` is the exact browser-visible origin, including the port when applicable. It cannot contain a path or query. The Host and Origin checks use this value. Opening `localhost` when the configured origin is `127.0.0.1`, or using a different port, can therefore be rejected.

A wildcard listener (`--host 0.0.0.0` or `--host ::`) requires an explicit public origin. For a reverse proxy, preserve the configured Host, allow streaming responses, and use the actual HTTPS browser origin. The gateway does not trust proxy headers automatically. Configuring a listener is not itself a public deployment or a hosted account service.

## Local and private model servers

Self-hosted operation permits explicitly chosen loopback model servers by default. This only permits the gateway's model connection; it does not grant agent tools additional network access. To disable that convenience, use `--deny-local-servers`.

A server elsewhere on a private network requires its exact origin:

```sh
sturgis-gateway serve \
  --host 127.0.0.1 \
  --port 8790 \
  --private-origin http://192.168.1.50:11434
```

Use `http://192.168.1.50:11434/v1` as the Ollama endpoint if that is the server's actual API address. The permission contains the origin without `/v1`; the connection contains the API base path. Repeat `--private-origin` for additional intended origins.

Provider requests resolve DNS and pin the permitted destination to the actual socket. A hostname resolving to a private address is not automatically trusted. Provider calls do not follow redirects to arbitrary destinations. Fix the configured endpoint instead of relying on a redirect.

## Embedded self-hosted and hosted operation

| Deployment | Management sign-in | Gateway data scope | Local-network behavior |
| --- | --- | --- | --- |
| Standalone service | Its administrator key and browser session | Local gateway tenant | Loopback model servers allowed by default; explicit origins for other private servers. |
| Sturgis self-hosted application | Existing Sturgis account | Authenticated account's gateway tenant | Loopback model-server convenience is enabled; other private origins require operator configuration. |
| Sturgis hosted application | Existing hosted account | Authenticated account's gateway tenant | Private origins and network limits are operator-controlled; a customer's browser localhost is not a hosted model server. |

The standalone administrator session is not a replacement for hosted user identity. Applications embedding the gateway must supply their own authenticated tenant mapping. Sturgis already provides this boundary.

The standalone service and an embedded gateway normally use different storage roots. Connecting providers in one does not automatically populate the other. To consume one gateway from another, use a compatible gateway preset with an app key and import its advertised models.

## Adjust limits and transfer configuration

Open **Access & usage → Advanced: limits & configuration transfer**.

To adjust admission limits, enter **Request timeout (seconds)**, **Concurrent requests**, and **Requests per minute**, then choose **Save gateway limits**. Timeout and concurrency cannot exceed the operator's configured ceilings. Requests per minute must be between 1 and 10,000. The minute limit counts authenticated client requests; fallback provider attempts still appear separately in usage. These settings do not change a provider's own quotas.

To transfer configuration between installations:

1. Choose **Download configuration** on the source. Save the resulting `sturgis-gateway-config.json`.
2. Sign into the target gateway and open the same Advanced section.
3. Choose the JSON file under **Import configuration file**. The maximum file size is 2 MiB.
4. Review the connection/route counts and confirm **Import configuration**.
5. Open **Providers**. Imported connections report that reconnection is required. Use **Edit**, enter a fresh API key or complete account sign-in, verify the model list, and save each intended connection. Refresh alone does not grant imported connections execution access.
6. Review the imported routes, copy the new exact model/route IDs, and create new gateway app keys. Update clients to use the target endpoint and IDs.

Import adds new records and remaps IDs; it does not overwrite existing connections. Routes are rewritten to target the new connection IDs. The export includes public connection details, model lists, routes, and settings, but excludes provider secrets, signed-in account tokens, administrator authority, app keys, and usage. Exported settings are reference information and are not applied on import; set the target's limits explicitly.

Configuration transfer is not a secret-vault backup. If you need to preserve the whole same installation, follow the storage procedure below. Exported endpoints and connection names can still contain private operational information, so share the file only with its intended recipient.

## Restart, preserve data, and troubleshoot

Keep the gateway database and its `master.key` together when preserving the installation. The encrypted credential records cannot be recovered from the database alone if the master key is lost. The administrator key is a separate file. App-key records contain hashes; the full app keys cannot be recovered from a metadata listing.

Before moving or copying the live storage directory, stop the service so the database and key files form one consistent copy. Keep the copy private. This full gateway storage is different from an agent's portable backup, which does not carry upstream gateway credentials.

| Symptom | Recovery |
| --- | --- |
| Dashboard missing | Build the web client and restart with the correct `--web-root`. |
| Sign-in rejects the URL | Match the configured Host and public origin exactly. |
| Too many sign-in attempts | Wait for the rate-limit window before retrying the correct key. |
| Private server denied | Configure its exact private origin; check the gateway's actual network location. |
| Credentials fail after moving storage | Restore the matching master key and private file permissions. |
| Account refresh interrupted | Sign in again to the affected provider account. |
| Imported provider cannot run or refresh | Use Edit to reconnect it with current credentials and validate its imported models. |
| Gateway request limit reached | Wait for the next minute or review the account's gateway limits. |
| Configuration import rejected | Choose an unmodified credential-free gateway export within 2 MiB; correct unavailable presets on the source before exporting again. |
| Browser callback on the wrong machine | Follow the provider account guide's callback instructions; registered callback rules still apply. |

[Connect providers](../guides/model-gateway.md) · [Model routes and keys](../guides/gateway-routing-keys.md)
