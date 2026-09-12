# Deploying Sturgis

Sturgis runs a Python consumer service and private Python agent workers, each using
the C++ SQLite memory executable. Its React/TypeScript interface uses Material 3
styling, Radix components, and bundled fonts. Python serves the compiled static
assets in both deployment modes. Node.js and the frontend sources are build-time
dependencies, not runtime services.

This guide covers a local Linux installation and operator-managed hosted workers.
It does not provision a cloud account or claim a production deployment. Refer to
[Qualification](qualification.md) for the recorded checks and remaining limits,
and [Using Sturgis](consumer.md) for the owner-facing journeys.

## Install a platform wheel

Use a release wheel built for the installation's Linux architecture, CPython
version, and system library baseline. The wheel is platform-specific because it
contains an ELF executable. The exercised baseline is Ubuntu 24.04, CPython 3.12,
SQLite's runtime library, and libstdc++. The wheel includes the CLI, Python
modules, native executable and third-party attribution, compiled web assets, and
fonts. Python dependencies are declared in its metadata.

Given a matching wheel in the current directory:

```sh
python3.12 -m venv "$HOME/.local/opt/sturgis"
"$HOME/.local/opt/sturgis/bin/python" -m pip install ./sturgis_core-*.whl
"$HOME/.local/opt/sturgis/bin/sturgis" setup
"$HOME/.local/opt/sturgis/bin/sturgis" serve
```

Make sure Linux provides `libsqlite3.so.0` and the C++ runtime. On Ubuntu 24.04 the
relevant runtime packages include `python3.12-venv`, `libsqlite3-0`, and
`libstdc++6`. Add the virtual environment's `bin` directory to your shell's PATH if
you want to use the shorter `sturgis` commands below.

The default listener is `127.0.0.1:8787`; data defaults to
`~/.local/share/sturgis`, and CLI configuration to `~/.config/sturgis` (respecting
`XDG_CONFIG_HOME`). `setup --root /absolute/private/data` records an alternative
data root. Keep data outside the installed code or virtual environment. Packaged
installations select their bundled `consumer/bin/sturgis-memory` automatically.

No model-vendor account is needed to start the service, create accounts and agents,
or use local content services. Model execution requires a configured, successfully
probed provider. Speech, images, search, and remote devices require their respective
compatible services.

## Bootstrap and credentials

On first serve, the installation generates a private `bootstrap.token` under its
data root. Use that file's value in the browser's first-owner form, or use:

```sh
sturgis --url http://127.0.0.1:8787 login --bootstrap --email you@example.com
```

The CLI prompts privately for the password and installation token. It does not
accept those secrets as command-line arguments. Normal later login omits
`--bootstrap`. `serve --bootstrap-prompt` lets an operator supply the bootstrap
token interactively instead of relying on file generation. Avoid copying private
token files into deployment images, logs, or backups of ordinary agent content.

Opaque login sessions can be revoked with `sturgis logout`. Browser mutations
require the HttpOnly session, a matching CSRF token, and the exact installation
origin. CLI bearer sessions still use owner authorization. Client configuration
and session files are private. `--output-file PATH` writes a full command response
to a new file with mode 0600; normal printed responses redact recognized secret
fields. Store API keys with `sturgis credential AGENT_ID NAME`, which prompts for
the value and returns a revisioned vault reference.

Agent vaults and worker IPC keys live outside writable workspaces. Credential
inspection exposes references and state, not stored values. Rotating or revoking a
credential invalidates readiness for configurations that depend on that revision.

## Build from source

The build needs CPython 3.12, a C++20 compiler, CMake, SQLite development headers,
Node.js/npm compatible with the lockfile, and Python build tooling. From the
checkout:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[test]' setuptools wheel
npm ci
bash packaging/build-wheel.sh .build/release
```

The script builds the web application and an independent release-mode native
binary, stages the package, then writes a platform wheel and SHA-256 sidecar.
It does not overwrite the instrumented native qualification build. A job that has
already produced and verified the exact web assets can use
`STURGIS_USE_BUILT_WEB=1`; that flag intentionally skips rebuilding them.

The installation test installs the actual wheel outside the checkout, verifies
the packaged web bytes, and uses its CLI to serve the interface, launch real native
workers in both modes, and back up/restore data through HTTP. The final aggregate
qualification and its source snapshot are recorded separately in
[Qualification](qualification.md).

## Choose a runtime mode

| Mode | Intended environment | Enforced boundary |
| --- | --- | --- |
| `self_hosted` | A trusted local installation | Separate worker processes and fixed agent scope; ordinary OS-user filesystem authority still applies to the process. |
| `hosted` | An operator-managed Linux host serving private agents | Per-worker user, mount, PID, IPC, and UTS namespaces; private writable agent roots; read-only program/dependency mounts; resource limits. |

Select hosted mode with `sturgis serve --mode hosted` or
`STURGIS_MODE=hosted`. Install `bubblewrap` and util-linux's `prlimit`. The service
identity and its outer environment must permit the required namespace operations.
Failure returns `isolation_unavailable`; the manager does not silently run a
hosted worker as an ordinary trusted process.

The hosted launcher mounts the selected agent's state, workspace, and private
directories writable. It mounts the program, required dependencies, and selected
system libraries/configuration read-only. Other agent roots, the shared control
root, and unrelated home directories are not mounted. Its capability set is
dropped. Authenticated, bounded UNIX IPC uses an operator-generated key and fixed
launch configuration; commands cannot select another scope, root, deployment
mode, or executable. A placement lock admits one manager per installation root.

Worker networking is shared with the host. NetworkPolicy constrains the supplied
HTTP adapters using exact origins, DNS answers pinned to socket destinations,
redirect checks, and bounded bytes/deadlines. This is not a network namespace for
the whole worker, a firewall for arbitrary plugin code, distributed tenant
placement, or protection from a compromised host kernel. Install trusted code into
the immutable program/dependency tree.

The hosted default `RLIMIT_AS` is 2,147,483,648 bytes per process. Operator
configuration allows 134,217,728 through 274,877,906,944 bytes. This is a hard
virtual address-space ceiling, not aggregate physical-memory isolation or a hard
RSS quota. Other launcher limits include 1,024 descriptors, an `RLIMIT_NPROC` of
256, and 86,400 CPU seconds. OS limits have their normal per-process/user
semantics; no cgroup accounting or cluster-wide resource quota is claimed.

## Publish behind HTTPS

For a service exposed beyond localhost, use an HTTPS reverse proxy and set its
exact public origin:

```sh
sturgis serve --host 127.0.0.1 --port 8787 \
  --public-origin https://agents.example.com --mode hosted
```

The hostname above is an operator example, not a provisioned service. The proxy
must preserve the external Host and Origin values expected by this installation
and proxy `/`, `/assets/`, and `/api/v1/`. Configure streaming/long-poll behavior
without buffering away incremental responses. Sturgis does not infer security
authority from arbitrary forwarded headers. A wildcard listener such as
`--host 0.0.0.0` requires an explicit `--public-origin`.

Additional account registration is off by default. Enable it deliberately with
`--allow-signup` or `STURGIS_ALLOW_SIGNUP=true`. Hosted mode itself does not enable
signup. The first owner's administrative status does not bypass another account's
agent ownership checks.

[packaging/sturgis.service](../../packaging/sturgis.service) is an example for
`/opt/sturgis` and the dedicated `sturgis` OS account. Create the account and install
the wheel before enabling it; adjust its public-origin environment for your proxy.
It uses a private systemd state directory and terminates the service process group
on shutdown. [packaging/Dockerfile](../../packaging/Dockerfile) is an unexecuted
Ubuntu 24.04 container example that accepts a matching prebuilt wheel through the
`STURGIS_WHEEL` build argument. It does not request privileged mode. Hosted workers
inside a container additionally depend on the outer namespace/security policy.

## Operator configuration

CLI `serve` options override saved configuration and environment values.

| Environment variable | Purpose / default |
| --- | --- |
| `STURGIS_ROOT` | Private installation data root; `~/.local/share/sturgis`. |
| `STURGIS_MODE` | `self_hosted` or `hosted`; default `self_hosted`. |
| `STURGIS_HOST`, `STURGIS_PORT` | Listener; `127.0.0.1`, `8787`. |
| `STURGIS_PUBLIC_ORIGIN` | Exact external HTTP(S) origin, required for wildcard listeners. |
| `STURGIS_NATIVE_BINARY` | Optional operator-selected native executable; bundled binary is preferred automatically. |
| `STURGIS_SOURCE_ROOT` | Immutable installed program root; normally derived from the package. |
| `STURGIS_WORKER_TIMEOUT` | Worker command timeout in seconds; default 30, supported range 0.1–300. |
| `STURGIS_WORKER_MEMORY_BYTES` | Hosted worker virtual address-space ceiling; default 2 GiB. |
| `STURGIS_PRIVATE_NETWORK_ORIGINS` | Comma-separated exact origins allowed to resolve to non-public addresses; empty by default. |
| `STURGIS_BROWSER_BINARY` | Canonical path to an operator-managed Chromium executable; unset by default. |
| `STURGIS_ALLOW_SIGNUP` | Exact `true` or `false`; default `false`. |

For a local model service, explicitly allow only its origin, for example
`sturgis serve --private-origin http://127.0.0.1:8080`. Then configure its complete
endpoint in the UI. The exception does not automatically grant every connection
access to that origin. Normal supported HTTP traffic defaults to public
destinations, with an 8 MiB response bound, 30-second deadline, three redirects,
and four concurrent requests per worker policy. Individual adapters can impose
smaller bounds.

HTTP command JSON is limited to 8 MiB and rejects duplicate keys, non-finite
values, and unknown fields. Outbound adapters request identity transfer encoding;
compressed response bodies are rejected. Redirects are allowed only for GET/HEAD,
must remain within granted origins, and lose credentials when crossing origins.

## Model and adapter compatibility

These are implemented protocol subsets, not a list of certified vendors.
Configuration IDs and model identifiers are chosen by the owner; credentials use
the private vault. Unsupported responses produce typed errors.

| Connection | Implemented request and response boundary |
| --- | --- |
| Model provider | POST to the exact configured chat-completions endpoint with `model`, `messages`, `stream:true`, `max_tokens`, and optional function tools. No authentication or Bearer authentication. Parses a bounded single-choice SSE stream, fragmented function calls, finish reasons, optional token usage, and terminal `[DONE]`. Tool-enabled probes require a canonical `invoke` proposal; text-only probes do not. No Responses API, arbitrary vendor OAuth, or universal multimodal contract is implied. |
| Web fetch (`http`) | Granted GET to the configured endpoint or an allowed URL; returns status, media type, URL, and base64 bytes. The fetch body is bounded to 64 KiB. |
| Web search (`search`) | GET with `q` and `limit`; response must be `{"results":[{"title":"...","url":"https://...","snippet":"..."}]}`. Configured results are bounded to 1–20. |
| MCP app server (`mcp`) | HTTP JSON-RPC tools protocol version `2025-11-25`: initialize, initialized notification, tools/list, tools/call, optional session header, and best-effort session DELETE. Exact selected tool names and catalog hash are pinned. Accepts JSON or one bounded SSE response; no paginated tool catalogs, persistent server-initiated stream, stdio transport, resources/prompts, or arbitrary remote JSON Schema execution. |
| Speech to text (`stt`) | GET base endpoint `/models` for readiness. POST `/audio/transcriptions` using multipart `model` and an audio artifact as `file`; expects JSON `text`. |
| Text to speech (`tts`) | Same model catalog probe. POST `/audio/speech` with `model`, `input`, `voice`, and `response_format:"wav"`; validates WAV bytes and stores an artifact. |
| Image generation (`image`) | Same model catalog probe. POST `/images/generations` with `model`, `prompt`, `n:1`, `response_format:"b64_json"`; accepts exactly one bounded PNG and stores an artifact. |
| Managed browser (`browser`) | Operator-managed Chromium using CDP pipes in an isolated process; rendered text and screenshot artifacts, with page HTTP mediated by NetworkPolicy. No uncontained remote CDP connection. |
| Incoming webhook (`webhook`) | Signed bounded JSON `{event_id,text,data}` at the configured ingress route; records external evidence, not owner input. |
| Paired device (`device`) | `device/1` one-time claim, signed sync/poll/respond, declared bounded operation schemas, and durable request/result identities. Requires a separate compatible device client. |

Provider readiness lasts five minutes for the exact configuration/credential
revision. Refresh and activation use actual probe evidence. Media readiness proves
catalog membership only (`operation_verified:false`); device readiness proves
pairing only (`actuator_verified:false`). Remote device reports remain external
evidence, and uncertain effects are not promoted to verified completion. Native
Windows/macOS/Linux actuators, branded messaging bundles, managed subscriptions,
and bundled speech/image weights are outside this release.

## Signed ingress and device implementers

External routes use `/api/v1/external/AGENT_ID/KIND/CONNECTION_ID/ACTION` and POST.
They are available only for a known active owner-configured connection. Webhooks
use `KIND=webhook`, `ACTION=receive`. Devices use `KIND=device` and `claim`, `sync`,
`poll`, or `respond`. Public owner-command routes cannot invoke internal ingress
commands. External routes are bounded to 120 requests per connection per minute.

Use `X-Sturgis-Timestamp`, `X-Sturgis-Nonce`, and `X-Sturgis-Signature` headers for
signed operations. Timestamp is integer Unix seconds; signatures are HMAC-SHA256
hex over the canonical bytes returned by
[`consumer.webhooks.signing_bytes`](../../consumer/webhooks.py) or
[`consumer.device.device_signing_bytes`](../../consumer/device.py). Sign the exact
raw body hash for webhook and device responses. Device sync/poll sign an empty
body while their HTTP JSON body is `{}`. Device sync uses signed revision zero to
discover the current configuration; other operations bind the current revision.
Nonce reuse with different content is rejected.

The unsigned device claim instead supplies `{pairing_token,device_id}`. It returns
a once-minted signing secret and the updated revision; persist that secret only in
the device client's private storage. Default pairing lifetime is 300 seconds,
with an allowed range of 1–900 seconds. Grant access after pairing, then use sync
to learn revision changes. Poll returns at most 16 pending requests. Respond sends
`{request_id,status,data}`, with status `success`, `target_error`, or `cancelled`.
Duplicate identical results are idempotent; changed results conflict. Late results
remain labeled late and cannot manufacture a settled effect.

## Managed browser requirements

Install a Chromium binary and its supporting files as an operator-managed,
read-only tree; install bubblewrap, prlimit, required libraries, and fonts. Pass
its executable with `--browser-binary /absolute/path/to/chrome` or
`STURGIS_BROWSER_BINARY`. The binary is not downloaded by normal Sturgis startup.

The browser gets a temporary profile, its own network namespace, CDP pipes, no
workspace/vault mounts, and bounded lifetime, requests, and response bytes. Page
HTTP is fulfilled through NetworkPolicy; raw browser networking remains isolated.
The default inspection bounds include 20 seconds, 64 requests, 16 MiB total
retrieved bytes, and 65,536 characters of rendered text, plus a viewport screenshot.

On the qualification host, a usable self-hosted Chromium renderer required a
256 GiB virtual address-space ceiling. A 64 GiB ceiling started its control process
but failed when creating a renderer. The browser launcher sets a hard limit no
higher than 274,877,906,944 bytes and never raises a lower inherited limit. This is
virtual reservation compatibility, not a requirement for 256 GiB of physical RAM.
Its additional sampled process-tree RSS watchdog is not a hard RSS quota.

Hosted workers retain their strict 2 GiB default. An operator may explicitly choose
`--worker-memory-bytes 274877906944`, but that does not resolve the separately
observed namespace restriction: on this host, the hosted worker's zero-capability
environment denies nested browser network initialization. Readiness returns
`browser_unavailable` with reason `network_namespace_denied`. A lower inherited
address-space limit can instead report `renderer_address_space_limit`. Do not
weaken worker containment or claim hosted browser availability from control-process
startup alone.

## Lifecycle, backup, and recovery

One data root has one placement owner. Use the service manager to stop the existing
server before starting another owner or upgrading its installed code. Accepted
messages are durably retained before wake. Idle suspension retains future due
work; an archived agent cannot auto-wake. Runtime recovery can interrupt a current
process, but unknown effects remain recorded and effectful commands are not
automatically replayed.

For an owned agent:

```sh
sturgis backup AGENT_ID ./agent.sturgis
sturgis restore ./agent.sturgis --name "Restored agent"
```

The same raw `sturgis-backup/1` ZIP works in the browser. CLI export verifies the
transport size and SHA-256 before exclusively creating a mode-0600 file. The HTTP
routes are POST `/api/v1/agents/AGENT_ID/backup` with `{}`, and POST
`/api/v1/agents/restore` with `{name,archive_base64,sha256,request_id}`. The backup response
contains `{format,archive_base64,sha256,bytes}`. The service accepts no caller-chosen
runtime root or scope.

Web and CLI clients send a stable restore `request_id`. The server binds it to the
authenticated account and a canonical digest of the requested name and verified
archive SHA-256. Same account, identity, and parameters replay the original
published agent and restore receipt; mismatched parameters return `conflict`.
Other accounts cannot retrieve that receipt using the same identity. The response
includes `restore.request_id`. Legacy callers may omit the field, but each such
request receives a new identity and cannot deduplicate an unknown prior request.

The CLI derives its default identity from the archive hash and name, so rerunning
the same command is safe after a lost response. Use `--request-id NEW_ID` for a
deliberate additional copy or a new attempt after an unpublished failure. The
browser retains the pending identity across ambiguous retries.

Snapshot ownership remains held while the worker is stopped and logical SQLite
contents plus files are copied. Export is bounded to 4 MiB compressed, 32 MiB
expanded, 512 files, and 8 MiB per file. Restore rejects traversal, symlinks,
special files, duplicate entries, unsupported schemas, hashes, and expansion-bound
violations. It regenerates profile roots and validates rebuilt memory through the
native protocol instead of importing arbitrary SQLite schemas.

Original content, source artifacts, knowledge revisions, canonical memory,
current derived memory, retractions, and labeled conversation history survive.
Original historical JSON is retained in immutable artifacts. Imported history
does not replay actions or act as trusted observation evidence. Credentials,
provider/connection activation, devices, permissions, plugins, schedules,
workflows, and child execution are not restored as live authority; procedures
arrive disabled. Reconnect those capabilities explicitly.

An unpublished restore stays durably fenced as `restoring`. Cancellation waits for
local writes before removing it; a new control owner discards only such
unpublished targets before lifecycle admission. Those request identities become
terminal `restore_interrupted` receipts; they do not restart automatically.
Lifecycle publication and its completed restore receipt commit in one transaction.
Once committed, a lost response, cancellation, or server crash leaves the same
published agent available for retry by request identity. Existing published agents remain.
Backups are private user data, not encrypted portable vaults, and may include
secrets deliberately written into ordinary files or conversations.

## Operational checks

`sturgis doctor --agent AGENT_ID` inspects local native availability, HTTP health,
authentication, and an owned runtime. GET `/api/v1/health` establishes control
process health only. In the UI, inspect runtime diagnostics, provider readiness,
connection readiness, and usage separately. A healthy server does not prove an
external service, model, browser, or actuator works.

The [HTTP contract](../../spec/sturgis-02/http-contract.md) describes the shared
envelope and core routes; unsupported domain commands fail explicitly. See
[Qualification](qualification.md) for the final exercised user journeys, installed
package evidence, coverage reports, and remaining limitations. Local fixture
services and namespace tests do not certify arbitrary commercial vendors or
production multi-host operation.

## Developer entry points

The web client and CLI both call the authenticated HTTP API. `ConsumerControl`
checks the account and agent ownership, then `Lifecycle` coordinates durable
admission and worker availability. `WorkerManager` owns the process;
`WorkerClient.request()` sends authenticated, bounded IPC to `WorkerServer`.
Inside that process, `AgentApplication.command()` dispatches the registered
consumer command. Its `CoreRuntime` retains scheduling, model execution, tool
authorization, journal, and native-memory ownership. The CLI does not run a
second agent loop.

The following map identifies current source owners and trusted composition
points. External clients should use the [HTTP contract](../../spec/sturgis-02/http-contract.md)
and signed protocols above. Python composition requires operator-controlled
configuration; possessing a Python class name does not grant a client permission
to choose an agent scope, root, or private IPC key.

| Python source | Entry points and ownership |
| --- | --- |
| [config.py](../../consumer/config.py#L14) | `Settings` and `Settings.from_env()` validate immutable installation configuration. |
| [api.py](../../consumer/api.py#L379) | `create_app(settings=None)` constructs the ASGI app and lifespan; `serve(settings=None)` starts its Uvicorn listener. `ConsumerControl` composes account, directory, lifecycle, and portability services. `SecurityHeaders` implements HTTP middleware. |
| [cli.py](../../consumer/cli.py#L367) | `main(argv=None)` is the installed `sturgis` entry point. `parser()` defines commands; `Client` uses the same HTTP service. `PrivateStore` owns private local CLI files. These CLI helpers are not a separate runtime SDK. |
| [contracts.py](../../consumer/contracts.py#L15) | `identifier()`, `object_body()`, and `public_record()` validate/project shared data; `success()` and `failure()` construct the `consumer/1` envelope. |
| [identity.py](../../consumer/identity.py#L20) | `Principal`, `Accounts`, and `AgentDirectory` represent authenticated identity, durable sessions, and owner-to-agent authorization. |
| [workers.py](../../consumer/workers.py#L29), [worker.py](../../consumer/worker.py#L22) | `WorkerConfig` fixes launch inputs; `WorkerManager` owns placement and process lifetime; `WorkerClient` implements private requests; `WorkerServer` authenticates and records their receipts. This IPC is an internal operator boundary. |
| [lifecycle.py](../../consumer/lifecycle.py#L19), [portability.py](../../consumer/portability.py#L255) | `Lifecycle` owns desired/observed runtime state, command admission, wakes, and snapshot fencing. `Portability.backup()`, `restore()`, and `recover()` coordinate bounded archives with that same owner. |
| [application.py](../../consumer/application.py#L53) | `AgentApplication` composes one private agent. Its `start()`, `close()`, and `command()` define application lifetime and dispatch; `bind_command()` registers a trusted command with allowed/required fields. |
| [content.py](../../consumer/content.py#L91), [work.py](../../consumer/work.py#L19), [usage.py](../../consumer/usage.py#L5) | `ConsumerData` owns attachment, workspace, knowledge, and canonical-memory operations. `WorkViews` exposes goals, commitments, schedules, workflows, inbox, and child-work operations through core owners. `UsageView` projects actual usage and unresolved reservations. |
| [vault.py](../../consumer/vault.py#L18), [providers.py](../../consumer/providers.py#L46), [permissions.py](../../consumer/permissions.py#L21) | `CredentialVault` stores scoped encrypted secrets; `ProviderSettings` binds readiness and model I/O to current revisions; `Permissions` applies human policy and exact-call approval around core tool authorization. |
| [connections.py](../../consumer/connections.py#L18), [connectors.py](../../consumer/connectors.py#L271) | `ConnectionOperation` describes a schema-bound executor. `ConnectionService.register_adapter()` registers operations and their probe; `register_tools()` exposes configured revisions through core dispatch. `install_connectors()` constructs the supplied `ConnectorSuite`. |
| [network.py](../../consumer/network.py#L75), [browser.py](../../consumer/browser.py#L73) | `NetworkPolicy` owns pinned, bounded outbound HTTP; `HTTPResult` and `HTTPStream` expose responses. `BrowserBackend` supplies managed rendering. `CDPPipe` is its internal transport, not a remote-browser integration contract. |
| [webhooks.py](../../consumer/webhooks.py#L12), [device.py](../../consumer/device.py#L17) | `WebhookIngress` and `DeviceService` admit attributed external evidence and device receipts. `signing_bytes()` and `device_signing_bytes()` define the canonical signing bytes for compatible external clients. |

Add trusted connection backends through `ConnectionOperation` and
`register_adapter()`, retaining the existing network, revision, permission, and
scheduler checks. Executable plugins use the core
[`Capability` / `PluginManifest` contracts](../../agent/plugin_sdk/__init__.py)
and [`PluginHost`](../../agent/plugin_host/__init__.py). Private implementation
helpers, command-table closures, process launch details, database schemas, and
underscore-prefixed functions are source navigation, not independently versioned
extension APIs. The [core guide](../core.md) describes the underlying owners.

The authored frontend lives under `frontend/src`; compiled `consumer/web/assets`
files are build outputs. These exports compose the client:

| Frontend source | Ownership |
| --- | --- |
| [main.tsx](../../frontend/src/main.tsx), [App.tsx](../../frontend/src/App.tsx#L50) | `main.tsx` mounts React, QueryClient, and motion providers. `App` owns installation/authentication, agent/conversation selection, page navigation, and notifications. |
| [Shell.tsx](../../frontend/src/components/Shell.tsx#L354), [ui.tsx](../../frontend/src/components/ui.tsx) | `Shell` consumes `ShellProps`/`ViewName` callbacks for navigation and account controls. `BrandMark`, `Button`, `Input`, `Textarea`, `Card`, `Badge`, `Skeleton`, `EmptyState`, `Tooltip`, `Dropdown`, `Modal`, `Tabs`, and `Select` provide shared presentation and interaction. |
| [lib/api.ts](../../frontend/src/lib/api.ts) | `api()` checks HTTP/envelope outcomes; `command()` carries a request identity. `ApiError`, `requestId()`, `fileBase64()`, `decodeText()`, and `downloadBody()` support transport and content handling. |
| [hooks/useAgent.tsx](../../frontend/src/hooks/useAgent.tsx) | `AgentContext`/`useAgent()` identify the selected account and agent. `useRuntimeState()`, `canWakeRuntime()`, and `pauseRuntimeQueries()` gate private reads on control-plane observations. `useAgentQuery()` owns scoped query caching; `useAction()` retains mutation identity/digest after unknown acknowledgements for explicit retries. |
| [hooks/useEvents.ts](../../frontend/src/hooks/useEvents.ts#L4) | `useEvents()` polls replayable conversation events, advances cursors, deduplicates events, and aborts obsolete polling. |
| [ConversationPage.tsx](../../frontend/src/pages/ConversationPage.tsx#L128) | `ConversationPage` renders history and composes drafts, attachments, message admission, and cancellation. Its `Composer` and `MessageBubble` helpers are internal. |
| [WorkPage.tsx](../../frontend/src/pages/WorkPage.tsx#L211), [KnowledgePage.tsx](../../frontend/src/pages/KnowledgePage.tsx#L31), [FilesPage.tsx](../../frontend/src/pages/FilesPage.tsx#L25) | `WorkPage`, `KnowledgePage`, and `FilesPage` expose their corresponding consumer commands and revision-aware results. |
| [ConnectionsPage.tsx](../../frontend/src/pages/ConnectionsPage.tsx#L137), [SettingsPage.tsx](../../frontend/src/pages/SettingsPage.tsx#L172) | `ConnectionsPage` manages connection readiness/grants and device pairing. `SettingsPage` manages providers, credentials, permissions, skills/plugins, runtime state, and portability. |
| [pages/shared.tsx](../../frontend/src/pages/shared.tsx) | `PageHeader`, `QueryState`, `Evidence`, `FormDialog`, and `Metric` share page rendering; domain projections are declared in [lib/types.ts](../../frontend/src/lib/types.ts). |

Frontend components and hooks are application modules, not a published plugin
SDK. They present observed state and send owner requests; the Python service and
core remain responsible for authority and durable effects.
