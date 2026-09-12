# Frozen collaboration contracts

Version `consumer/1`. Root owns changes to this contract and shared dependencies. Modules may add internal helpers; publish any new public interface before a dependent lane uses it. Use existing `agent.protocol.CoreError`, strict JSON, `Journal`, `Record` and core service APIs. No new `agent/` top-level package is introduced.

## Shared foundation (root)

`consumer.contracts` provides `VERSION`, `MAX_BODY=8388608`, `identifier(value)`, `object_body(value, allowed, required=())`, `public_record(record)`, `success(data, request_id=None)`, `failure(error, request_id=None)`. Replies are `{version, request_id, data, error}` with exactly one data/error branch. Identifiers are bounded ASCII `[A-Za-z0-9_-]`; public_record is for non-secret records only and returns `{id, revision, ...value}`. Consumer errors use CoreError, with bounded safe details. HTTP and worker adapters normalize errors once.

`consumer.config.Settings` defines canonical operator-owned `root`, `mode` (`self_hosted`/`hosted`), `host`, `port`, `native_binary`, `source_root`, `worker_timeout`, `bootstrap_token`, `allow_signup`, and optional operator-granted private-network origins. User input cannot change deployment mode, source roots or operator network exceptions. Async request handlers move blocking storage/CPU work off the event loop.

## Identity/configuration lane

Files: `consumer/identity.py`, `consumer/vault.py`, `consumer/providers.py`, `consumer/usage.py`; later `consumer/work.py`, `consumer/permissions.py`.

- `Principal` is an immutable authenticated user identity (id/email/admin/session identity). `Accounts(journal, bootstrap_token=..., allow_signup=False)` owns bootstrap/login/authenticate/logout/user disable and secure password/session records. Raw bearer/CSRF tokens only leave the mint/login call. Mutation authorization is derived server-side.
- `AgentDirectory(journal)` owns `create(principal, name)`, `list(principal)`, `authorize(principal, agent_id, permission='read')`, rename/archive. Durable agent records are in namespace `consumer.agents`, scope `control`, with server-generated id, owner_id, name, archived. Other users cannot resolve their data; platform administration does not silently confer private transcript access.
- `CredentialVault(root)` uses established authenticated encryption, private key/file permissions and immutable scoped references. `put(scope,name,secret,expected_revision=0)`, `get(scope,name)`, `list(scope)`, `revoke(scope,name,expected_revision)` are privileged APIs; public output contains only metadata. Each private worker has its own vault outside its workspace. Do not write credentials to Journal records, errors or provider descriptors.
- `ProviderSettings(core, vault, network_policy=None)` configures provider/model/limits/secret references, inspects redacted settings, probes a real endpoint and activates exact revisions. Runtime adapter credential injection is privileged. Update/rotation invalidates old capability readiness. Root owns integration with CoreRuntime; do not edit core provider code without coordination.
- `UsageView(core)` projects deduplicated actual receipts and unresolved reservations without summing ancestors or charging duplicates. Versioned operator price/entitlement metadata may calculate estimates; no payment-processor settlement is claimed.
- `WorkViews(core)` is the consumer command/projection adapter for goals, commitments, schedules, plans/workflows, children and initiative/inbox. `Permissions(core)` persists revisioned policy and exact bounded approval decisions; effective revocation fences core work. Human approval never marks an action successful. Publish concrete method signatures with tests before root composition.

## Content/lifecycle lane

Files: `consumer/content.py`, `consumer/portability.py`; later `consumer/workers.py`, `consumer/worker.py`, `consumer/lifecycle.py`, packaging under `packaging/`.

- `ConsumerData(core)` derives scope from core. Own bounded upload metadata/content, rooted file operations, knowledge documents/search/revisions, memory search/correction/retraction. Bytes use core.artifacts; metadata/source events use core.journal. Explicitly protect `.sturgis` internal data from file mutation. Do not add fake PDF/vision/transcription support.
- Source ingestion records exact canonical content/evidence before native memory ingestion. Public calls accept ids and revisions, never host paths. Publish async vs sync APIs clearly.
- `Portability` exports a quiesced/consistent versioned runtime archive with a hash manifest. Restore validates archives, traversal/symlinks/bounds, target ownership and explicit absolute-path relocation. Exclude credentials and live authority; do not replay unknown effects. Never copy live SQLite/WAL files without a consistency boundary.
- `WorkerManager` provides async `create/start/status/stop/delete`, with `WorkerClient.request(method, params, request_id=None, timeout=None)` over bounded authenticated UNIX frames. Launch configuration fixes runtime id/scope/root/workspace/mode. Each request uses HMAC authentication bound to payload/id/deadline and replay semantics; user-provided scope/path/env/argv is forbidden.
- Root owns `consumer.application.AgentApplication(config)` and async `start()`, `close()`, `command(method, params, request_id=None) -> JSON data`. Worker entry point instantiates this application after validating private launch config; root supplies implementation. Worker response envelopes use shared success/failure helpers.
- Hosted launch uses bwrap plus prlimit, immutable code/interpreter/system mounts, only private runtime/workspace writable, clean environment and separate user/PID/IPC/UTS namespaces. Fail closed if unavailable. The self-host process backend has the identical protocol; it is explicitly a trusted-local process mode, not hosted isolation evidence. Networking is shared and described honestly.
- `Lifecycle(journal, manager)` owns durable desired/observed state, operations, startup/readiness, ownership fencing and suspend/wake/recovery. Accepted user command payloads and stable ids persist before wake. It forwards to the worker's command method and marks admitted only after a durable response; ambiguous action commands cannot be automatically retried. Publish a small executable interface for API composition.

## Integration lane

Files: `consumer/connections.py`, `consumer/network.py`, `consumer/connectors.py`, `consumer/webhooks.py`, `consumer/device.py`; later `consumer/cli.py` and `consumer/__main__.py`.

- `ConnectionService(core, vault, network_policy)` owns scoped configuration, exact revisions, grants, credential refs, readiness and revocation. Credentials come from consumer.vault; do not implement another vault. `register_tools()` binds actual executors to core.tools and keeps scheduler/capability authority. Changed revisions invalidate readiness and stale dispatch. Delegated dispatch rejects unsupported workspace/resource constraints.
- `NetworkPolicy` implements explicit allowed schemes/origins, safe DNS/IP and redirect handling, time/byte/concurrency limits and configurable operator-only private-origin exceptions. Pin destinations sufficiently to avoid resolution/redirect policy bypass. Hosted user configuration cannot grant host/LAN/metadata access.
- Concrete adapters: bounded fetch/search, HTTP MCP initialize/list/call with remote schema validation, real browser/CDP, configured HTTP STT/TTS/image output, signed generic webhooks and paired device requests. Real local wire servers are acceptable external-provider boundaries; browser tests must operate an actual browser. Typed unavailable/error states are mandatory.
- Incoming webhook identity is externally sourced. Verify timestamp/nonce/body and durably deduplicate before emitting an attributed event; fixed connection grants/binding decide admission. It cannot become arbitrary owner instruction by asserting a participant or scope in the body. Remote device pairing identifies one device and allowed operations, with bounded cancellation/results; it is not a claim to implement every OS actuator.
- CLI uses the same HTTP contract as the web client: setup/serve/login/agents/chat/events/stop, inspect/manage work/content/connections, backup/restore and doctor. Root owns the HTTP route inventory; commands must not silently bypass consumer authentication or start another inference authority.

## Root application and transport

Files: `consumer/application.py`, `consumer/api.py`, `consumer/web/**`, shared build/dependency/verification files and integration tests. Composition uses the above services inside each private worker. The control API owns accounts and runtime lookup; it never forwards caller-selected scope.

The worker command namespace includes `overview`, `conversations.*`, `messages.send`, `messages.events`, `messages.cancel`, `attachments.*`, `files.*`, `knowledge.*`, `memory.*`, `work.*`, `goals.*`, `commitments.*`, `schedules.*`, `workflows.*`, `permissions.*`, `providers.*`, `connections.*`, `skills.*`, `usage.*`, `backup.*`. Commands are allowlisted; arbitrary attribute lookup is forbidden. Root publishes exact implemented command schema for client integration.

The HTTP base is `/api/v1`. Public routes: health, bootstrap status/claim and login. Authenticated routes include current identity/logout, agents, agent commands and replayable events, attachments/downloads. Bearer auth supports CLI; HttpOnly cookie plus CSRF and strict Origin/Host handling supports browsers. No credentials in URLs. Client output renders untrusted text safely; artifact previews use restrictive content types and disposition. No model output HTML execution.

An accepted message has a stable client id, content digest, agent id, conversation id and durable receipt. Delivery cursor/replay and inference completion remain separate. Attachments are resolved with scope checks and compiled as explicitly labeled bounded sources; missing/partial data remains explicit. The application registers a knowledge/artifact read tool for full content access instead of silently dropping large attachments.
