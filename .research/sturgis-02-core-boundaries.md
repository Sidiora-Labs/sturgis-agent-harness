# Sturgis consumer-phase baseline

Inspection date: 2026-09-12. This is a source audit for the next specification, not a new implementation or interface decision. The user requested inspection of Hermes, Neo and Gideon first. The completed sturgis-01 specification remains active and unchanged.

## What the consumer layer can already use

| Mechanism | Current source evidence | Consumer work still needed |
| --- | --- | --- |
| Persistent agent ownership | [CoreRuntime](../agent/runtime/__init__.py#L49) owns one scope and locks its operational root; construction binds the durable workspace and composes the domain services. | Install/setup, account-to-agent ownership, lifecycle management and recoverable provisioning. A filesystem lock prevents duplicate local owners; it is not a distributed placement lease. |
| Identity and admission | [ChannelCore.bind/admit](../agent/channel_core/__init__.py#L22) preserve account, conversation, participant, scope and deduplication. [Runtime.accept](../agent/runtime/__init__.py#L240) accepts durable input. | Authenticate the human/device/channel, authorize membership, resolve scope on the server, and then bind admission. A caller-provided scope string is not consumer authentication. |
| Live interaction | [Runtime.start](../agent/runtime/__init__.py#L108) supervises independent admission, delivery, memory and heartbeat; [submit_event](../agent/runtime/__init__.py#L275) and [wait_output](../agent/runtime/__init__.py#L322) distinguish inference and output. | Consumer transport, reconnect cursor, history projection, stream presentation, interruption controls and accessible error/recovery behavior. |
| Client integration | [Runtime.acp](../agent/runtime/__init__.py#L310) exposes the ACP facade through caller-owned emitters. | An actual client connection host and authentication boundary. ACP remains one external interface to the same core. |
| Durable output | [Delivery](../agent/delivery/__init__.py#L63) records dispatch and supports negotiated acknowledgment/replay. | A consumer protocol must define what its acknowledgment means. Writing a socket cannot be presented as human receipt. |
| Profiles and settings | [ProfileRegistry](../agent/profile/__init__.py#L46) versions configuration and constrains workspace, memory and schedule roots. | User settings, provider setup, secret storage/rotation, installation credentials and device management. Profile changes cannot grant code-installation/evolution authority. |
| Providers and consumption | [ProviderCatalog](../agent/provider_catalog/__init__.py#L6) separates registration from fresh capability observations. [record_provider_usage](../agent/runtime/__init__.py#L582) settles factual model usage. | BYOK/managed-provider selection, capability refresh, account entitlements and usage presentation. Provider credentials currently enter runtime construction; no consumer secret manager is implemented. Resource reservations are not invoices or payment settlement. |
| Files and artifacts | [ArtifactStore](../agent/artifacts/__init__.py#L21) persists and integrity-checks scoped typed bodies. | Upload admission, ownership checks, preview/download, attachment processing, retention and user-controlled export/deletion. Storing bytes alone does not implement PDF/audio/image understanding. |
| Work and automation | [schedule_prompt](../agent/runtime/__init__.py#L196), [delegate](../agent/runtime/__init__.py#L173), and existing goals/commitments/planner/scheduler expose durable work mechanisms. | User-facing work views, proposals/acceptance, schedule editing, notification preferences and operational evidence. Another independent scheduler would split authority. |
| Extensions and learning | Plugin host/SDK, skills, native memory, sleep, meta-harness and opt-in self-evolution are composed or available to the runtime. | Discovery/install UX, capability approval, connection setup, memory inspection/correction, version visibility and explicitly governed improvement controls. A catalog entry is not an installed and working integration. |

## Constraints that apply to both installation modes

These are implications of the inspected core, not a selected product architecture.

1. Consumer surfaces should address the same persistent agent and durable operations through authenticated adapters. Conversation presentation must not replace the agent's goals, commitments, history and evidence with a second source of truth.
2. Hosted isolation requires more than the existing logical scope checks. Runtime roots, native memory, credentials, plugin workers and workspaces need an enforced ownership boundary. Tenant-controlled workspace tools must not expose control-plane credentials or other tenants' storage.
3. The current [process backend](../agent/tool_runner/process.py#L13) requires Linux bubblewrap, `prlimit` and working namespaces. It deliberately creates an isolated network and only granted mounts. A consumer browser/network/computer capability therefore needs a deliberate executor contract; it does not follow from registering an arbitrary shell command.
4. The inspected runtime uses local SQLite/filesystem durability and a native child process. Hosted compatibility must define volume ownership, placement/fencing, recovery and upgrades. Simply putting several web replicas in front of the same directory would not establish those properties.
5. A self-hosted installation should be able to supply local identity, its own provider credentials and local storage without a mandatory vendor account. A hosted installation can add account provisioning and managed infrastructure while retaining the same core behavior and exportable user-owned data. This is a proposed compatibility requirement to evaluate against the donor audit.
6. Background timers, accepted work and sleep persist. Any hosted suspension policy must define who wakes the runtime and how due work is retained. Scale-to-zero cannot silently turn a persistent agent into an agent that runs only while a browser tab is open.

## Evidence boundary

The existing [qualification report](../docs/qualification.md) documents sturgis-01's executed local tests and separate Python/C++ coverage. This inspection did not run tests, start services, change the core, add applications, or certify a hosted environment. Additional consumer and deployment features will need their own specification and verification scope.

The current [qualification script](../scripts/qualify.py#L21) deliberately rejects app/UI directories, and [Python coverage configuration](../pyproject.toml#L25) measures the `agent` package. A later consumer spec will need its own scope gate and complete production-code coverage denominator while preserving the existing 90% independent language thresholds. Merely retaining the core's green coverage would not qualify new consumer code. Any new client language also needs an explicit measured threshold and real user-path verification.
