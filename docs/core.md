# Sturgis core developer guide

The Sturgis core is a Python library and a C++ memory worker. The core-only scope is recorded in [sturgis-01](../spec/sturgis-01/spec.kvx), with its parallel task lanes and joins in [execution.md](../spec/sturgis-01/execution.md). The current checkout also includes the [sturgis-02 consumer layer](sturgis-02/deployment.md), which composes these mechanisms behind the web client, CLI and private workers.

## Build and verify

The concrete execution backend targets Linux, Python 3.12+, C++20, CMake, SQLite development headers, GCC/gcov, bubblewrap and util-linux `prlimit`. Namespace creation must be permitted by the host. Missing isolation support produces a typed error; it does not fall back to unrestricted subprocesses.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[test]' setuptools wheel
npm ci
.venv/bin/python scripts/qualify_consumer.py
```

The native worker is `.build/native/sturgis-memory`. The native verifier builds incrementally, clears old counters, tests the actual worker, and enforces its independent coverage floor. The current aggregate verifier includes every Python production module and the consumer browser, with independent 90% executable-line floors for Python, C++ and authored TypeScript/JavaScript. Current reports live under `evidence/sturgis-02/`. The [sturgis-01 record](qualification.md) preserves its historical command and results; its core-only verifier is incompatible with a checkout containing the consumer frontend and must not be used as the current gate. Local HTTP fixtures establish adapter wire behavior; they do not certify a remote paid provider.

## Compose an agent

`CoreRuntime(root, scope=..., workspace=...)` owns one operational root. An exclusive process lock prevents two supervisors from recovering the same work. Profile configuration persists the canonical workspace and separate memory/schedule roots. Constructing the core grants no file, process, plugin installation or code activation capability to model work.

```python
from agent.runtime import CoreRuntime
from agent.channel_adapters import StreamChannelAdapter
from agent.channel_core import InboundMessage

# The embedding program supplies an existing channel connection and provider configuration.
core = CoreRuntime(
    "/var/lib/sturgis/example", scope="example", workspace="/srv/workspaces/example",
    memory_binary="/opt/sturgis/.build/native/sturgis-memory",
)
core.adapters.register("client", StreamChannelAdapter(reader, writer))
binding = core.channels.bind("example", "client", "account", "conversation", "participant")
core.catalog.register("example", "default", endpoint=provider_endpoint)
# Install a fresh capability observation obtained by the embedding program.
core.catalog.observe("example", "default", model=model_name,
                     context_limit=32768, output_limit=4096, tools=True, ttl=300)
core.attach_ingress(binding.id)

async with core:
    event = await core.accept(InboundMessage(
        "example", binding.id, "participant", "stable-input-id", "Inspect the current situation."
    ))
    await core.submit_event(event)  # Wait for inference completion; output delivery remains independent.
```

The example is embedding code, not an application entry point. The caller owns transport connection creation, credential acquisition and provider capability observation. For bearer providers, pass `credential` to the runtime and set catalog authentication accordingly; credentials are not persisted in context or profile records.

`CoreRuntime` requires a tool-capable provider by default. A trusted embedding may explicitly set `allow_text_only_provider=True`, as the consumer layer does. For a selected text-only model, context and serialization omit the tool schema; unsolicited tool proposals fail before dispatch. This option changes provider compatibility and grants no action authority.

`attach_ingress` consumes bounded transport frames containing `delivery_id`, `text`, optional `kind` (`prompt`, `steer`, `cancel`) and `artifacts`. Binding/account/participant authority comes from the registered connection. `accept` and ACP use the same durable admission path. ACP is obtained with `core.acp(emit=..., connection_id=...)`; its JSON-RPC facade handles initialization, session creation/loading, text prompts, cancellation and output updates without creating another agent loop.

## Execution and evidence

For a tool-capable provider, the model sees one `invoke` function and a versioned registry of capability payloads. Register tools on `core.tools`, with explicit JSON input/output schemas and required capabilities. Runtime work receives only its configured grants. The dispatcher records intent, obtains the current scheduler claim in the same transaction, then starts the effect. Stable request IDs deduplicate attempts before allocation. Unknown effects require reconciliation instead of automatic replay.

`file_executor(WorkspaceFs(root))` and `process_executor(ProcessRunner(root, allowed_programs))` provide restricted concrete backends. File mutations require preimage hashes when replacing existing data. Processes execute argument vectors in Linux namespaces with bounded environment, resources and output. Child authority can select a distinct workspace or a read-only shared root. Both backends enforce those scheduler-owned constraints; custom/plugin executors that lack constraint support reject delegated claims.

Each tool result distinguishes invocation lifecycle, target outcome, effect certainty and evidence. `runtime.turn_completed` means the bounded inference interaction completed. It never marks an operational goal achieved. Goal, commitment, plan-step and child-result owners require matching source events for their configured evidence checks. Model text remains attributed text.

## Continuous input and output

Admission, inference, tool execution, native memory, heartbeat and outbox delivery run independently. Visible text is committed to the outbox as it arrives. Delivery preserves per-stream sequence and explicit terminal state. A successful write to a transport is `sent_unconfirmed`; acknowledgment requires a negotiated receipt. Network failure leaves durable retry/failure records. Disconnected clients cannot be guaranteed immediate receipt.

Prompts on the same binding queue while its current inference runs. Steering fences the old work and signals cancellation, then admits a fresh turn. Already emitted text and already authorized effects remain recorded. Ordinary model requests use fixed context snapshots; new text is never represented as having been inserted into an in-flight provider request.

Large inputs retain UTF-8 byte cursors and explicit pending ranges. Context packets record roles, labels, revisions, source hashes, selection, budget, memory freshness and precise inclusions. The default budget counter conservatively counts UTF-8 bytes; it does not pretend to be a provider-specific tokenizer. Oversized required context fails explicitly. A reached iteration budget cannot silently discard an unread suffix.

On restart, the runtime replays durably admitted inputs that lack work records. Previously running inference becomes interrupted; pending outbox entries remain deliverable and running tool attempts become indeterminate. This preserves evidence without repeating potentially effective actions. Programmatic work records and context packets can be inspected through the journal.

## Memory and domain mechanisms

The [native protocol](../native/memory/PROTOCOL.md) defines temporal typed records, concept links, compatible embedding spaces, lexical/vector retrieval, explicit retraction, idempotent ingestion, staged generations, atomic publication and leases. Embeddings are retrieval projections over evidence-bearing records. The initial backend uses bounded exact scanning; large-scale approximate indexing is an extension, not a performance claim in this pass.

The [Python memory API](../agent/memory/README.md) coordinates the actual framed subprocess. Foreground context reads the latest scoped cache with its observation time and freshness metadata; it never waits for native retrieval or daily sleep. The default sleep consolidator deduplicates exact repeated content. An injected consolidator must provide valid evidence ranges and remains bounded. Publication/pruning progress and calendar-day cycles persist. Foreground admission preempts background sleep; later cycles resume recorded phases and idempotency keys. Memory generations cannot activate code or fulfill operational goals.

Public domain services are available on the core: `profiles`, `awareness`, `goals`, `commitments`, `initiative`, `attention`, `planner`, `scheduler`, `skills`, `plugins`, `tools`, `artifacts`, `channels`, `catalog`, `subagents` and memory services. Profile scope controls durable resources; awareness projects observations; goals own cumulative budgets; commitments own accepted responsibilities and durable waits. Initiative proposes work and attention evaluates configured proactive policy separately from context selection. Planner owns versioned dependencies and evidence checks. These methods are programmatic mechanisms; an embedding application can decide which control capabilities to register with the model.

Self-evolution is opt-in through explicitly governed evaluation and source authority. Its protected checks run outside candidate code, and exact build/shadow/canary artifacts precede activation. This authority is distinct from skills and memory publication. See module APIs and qualification tests for staged activation and reversal behavior.

The ACP facade targets the upstream [version 1 schema](https://github.com/agentclientprotocol/agent-client-protocol/blob/main/schema/v1/schema.json). It emits complete tool-call records with explicit status; it does not claim a broader protocol-version implementation.

`await core.delegate(parent_work_id, objective, capabilities=..., resources=..., criteria=...)` attaches a scoped child to the same runtime. Its output goes to a durable internal channel, while parent result admission checks actual evidence. The restricted `core.subagents.scout` path queries native memory under its child allocation. Use `configure_evolution(checks=..., authority=...)` to install governed improvement facilities.

Large tool envelopes remain intact in the journal and typed artifact store. The model and channel receive a bounded envelope with an artifact reference and explicit missing ranges; this is never labeled a complete inline observation. `core.artifacts.get(scope, id)` retrieves and integrity-checks the complete body.

`wait(work_id)` waits for inference state. `wait_output(work_id)` waits separately for its current outbox writes or reports transport failure/deadline; unconfirmed sends remain unconfirmed. ACP prompt completion uses this output barrier so prior updates are emitted before the stop response.

`core.schedule_prompt(binding_id, text, spec, resources=...)` persists an explicitly authorized schedule; the timer service materializes due occurrences into the same runtime admission/context/outbox path. Calendar, interval and missed-run policies remain owned by the scheduler.

Each model request reserves its context/output token allowance under scheduler authority. Missing provider usage keeps that token claim unsettled instead of reporting zero consumption. Actual observed iterations and elapsed time are still charged on failure or cancellation. `record_provider_usage(context_id, tokens)` admits a trusted provider receipt and settles the exact claim idempotently. Token-limit/refusal stops preserve partial text and suppress tool proposals.

## Module reference

The following names are defined in each linked module. This map identifies owners; use the composition and evidence rules above when combining them.

| Module | Principal types and responsibility |
| --- | --- |
| [acp](../agent/acp/__init__.py) | `AcpFacade` handles client requests; `ACPAdapter` serializes outbox updates to the supplied emitter. |
| [attention](../agent/attention/__init__.py) | `Attention` evaluates proactive policy and selects bounded context evidence. |
| [awareness](../agent/awareness/__init__.py) | `Awareness` maintains scoped observation projections. |
| [channel_adapters](../agent/channel_adapters/__init__.py) | `AdapterRegistry` resolves transports; `StreamChannelAdapter` reads and writes framed asynchronous streams. |
| [channel_core](../agent/channel_core/__init__.py) | `InboundMessage` carries input; `ChannelCore` binds identity and durably admits it. |
| [commitments](../agent/commitments/__init__.py) | `CommitmentService` records accepted responsibilities, progress and evidence-checked fulfillment. |
| [delivery](../agent/delivery/__init__.py) | `Delivery` claims ordered outbox entries and records transport results. |
| [framing](../agent/framing/__init__.py) | `FrameCodec` bounds and decodes length-prefixed JSON bytes. |
| [goals](../agent/goals/__init__.py) | `GoalService` owns outcome criteria, lifecycle and cumulative resource accounting. |
| [initiative](../agent/initiative/__init__.py) | `Initiative` records scored work proposals. |
| [meta_harness](../agent/meta_harness/__init__.py) | `SourceSnapshot` identifies source; `CheckSpec` defines protected checks; `MetaHarness` records diagnoses and executes evaluations. |
| [planner](../agent/planner/__init__.py) | `Planner` maintains versioned plans, dependencies and result criteria. |
| [plugin_host](../agent/plugin_host/__init__.py) | `PluginHost` manages isolated extension execution and lifecycle. |
| [plugin_sdk](../agent/plugin_sdk/__init__.py) | `Capability` and `PluginManifest` define validated extension contracts. |
| [profile](../agent/profile/__init__.py) | `ProfileRegistry` persists scoped configuration and canonical resource roots. |
| [protocol](../agent/protocol/__init__.py) | `CoreError`, `Event`, `ToolCall` and `ToolResult` define shared error, provenance and invocation/result contracts. |
| [provider_adapters](../agent/provider_adapters/__init__.py) | `HTTPProvider` executes streaming HTTP requests and emits normalized `ProviderEvent` records. |
| [provider_catalog](../agent/provider_catalog/__init__.py) | `ProviderCatalog` separates provider registration from expiring capability observations. |
| [scheduler](../agent/scheduler/__init__.py) | `Scheduler` owns admission, authority claims, allocation, timers and durable wakes. |
| [self_evolution](../agent/self_evolution/__init__.py) | `EvolutionAuthority` constrains improvements; `SourceEdit` describes exact edits; `SelfEvolution` stages, activates and reverses evaluated generations. |
| [skills](../agent/skills/__init__.py) | `SkillRegistry` discovers and versions scoped reusable procedures. |
| [subagents](../agent/subagents/__init__.py) | `Subagents` constrains delegation, child communication and result admission. |
| [tool_runner](../agent/tool_runner/__init__.py) | `ExecutionContext` carries execution authority; `ExecutorResult` carries backend observations; `ToolDispatcher` validates and records dispatch. [WorkspaceFs](../agent/tool_runner/files.py) and [ProcessRunner](../agent/tool_runner/process.py) implement bounded file and process effects. |
| [context](../agent/context/__init__.py) | `ContextBlock`, `ContextPacket` and `ContextCompiler` provide labeled source material, immutable snapshots and inclusion manifests. |
| [runtime](../agent/runtime/__init__.py) | `CoreRuntime` composes and supervises the core mechanisms. |
| [memory](../agent/memory/README.md) | `NativeMemoryClient` coordinates IPC; `MemoryIngestor` tracks source ingestion; `MemorySleep` persists consolidation and publication progress. |
| [state](../agent/state/__init__.py) | `Record` represents revisioned state; `Journal` owns scoped transactions, events and durable cursors. |
| [artifacts](../agent/artifacts/__init__.py) | `Artifact` identifies a typed body; `ArtifactStore` persists and verifies scoped content. |
| [native/memory](../native/memory/PROTOCOL.md) | The C++ `sturgis` namespace contains `Engine`, the SQLite-backed request handler, and `Failure`, the typed protocol error. Python consumers use IPC rather than a C++ ABI. |

Internal helpers are not extension entry points. Runtime `_ChildChannel` supplies the internal child output sink. Native `Statement` and `Transaction` manage SQLite lifetimes inside [engine.cpp](../native/memory/engine.cpp); [main.cpp](../native/memory/main.cpp) defines the worker's `main` framing loop. These names are documented for navigation, without a compatibility promise for direct use.

## Initial release and migration boundary

This is the first Sturgis core implementation. There is no predecessor data-format migration, application shell or UI in this release. Source authority, memory generations and durable operational state have distinct owners. Preserve the operational root across restarts; evolving incompatible persistent behavior state is explicitly unsupported by this backend. Existing project-agent implementations are not imported or migrated.
