# Core design

This pass implements the core described in [architecture v0.2](../../.research/agent-architecture-v0.2.md), [module map](../../.research/agent-module-map-v0.2.md) and [contracts](../../.research/agent-interface-contracts-v0.2.md). This specification takes precedence for implementation scope. There is no app, UI, frontend, hosted product API, desktop/TUI shell or deployment in sturgis-01.

The deliverable is an importable Python 3.12+ package and a compiled C++20 memory worker. A programmatic runtime API composes durable admission, situation/context preparation, asynchronous providers, scoped tool actions, child work and durable delivery. ACP is a library facade attached to that runtime; it is not an application launcher. Test servers are local test infrastructure only.

## Ownership

| Lane | Production paths | Purpose |
| --- | --- | --- |
| Foundation | `agent/protocol`, `agent/state`, `agent/artifacts`, `agent/framing` | Strict shared contracts, SQLite transactions, evidence and bounded bytes. |
| Cognition | `agent/profile`, `agent/awareness`, `agent/goals`, `agent/commitments`, `agent/initiative`, `agent/attention`, `agent/planner` | Durable scope, observations, responsibilities, budgets, policy and plans. |
| Boundaries | `agent/channel_core`, `agent/channel_adapters`, `agent/delivery`, `agent/acp`, `agent/provider_catalog`, `agent/provider_adapters` | Reusable client/model transports and durable visible output. |
| Capabilities | `agent/plugin_sdk`, `agent/plugin_host`, `agent/skills`, `agent/tool_runner` | Versioned extension/procedure lifecycle and recorded restricted effects. |
| Native memory | `native/memory`, `agent/memory` | Temporal records, concepts, vector/lexical retrieval, generation publication, async access and sleep. |
| Scheduling | `agent/scheduler`, `agent/subagents` | One admission/lease authority, durable timers/wakes, hierarchical allocations and child responsibilities. |
| Improvement | `agent/meta_harness`, `agent/self_evolution` | Independent diagnosis/evaluation and staged code/harness activation/reversal. |
| Integration | `agent/context`, `agent/runtime` | Immutable context and independent supervised core execution. |

## Persistence and concurrency

Operational state uses one SQLite journal/record transaction boundary. Domain owners validate transitions; they do not create competing copies of authority. Revision checks and action claims run in short transactions without network or model calls. Async paths move potentially blocking storage operations off the event-loop thread. Long subprocess/native/provider work has independent tasks and bounded buffers.

Goal budgets are cumulative domain records. Scheduler reservations reference those records and ancestors, preventing children from manufacturing additional allowance. A wake is armed durably before work yields. Cancellation invalidates future claims; a previously authorized effect still needs evidence/reconciliation.

The C++ worker has a separate SQLite boundary. It receives complete bounded frames, validates scope/schema, and stages typed records with compatible embedding metadata. Exact records and source evidence remain authoritative; vectors are a retrieval projection. Generation publication checks its expected parent. Current retractions override old snapshots. Python coordinates native requests, query deadlines and daily consolidation, while live admission/delivery continue.

## Coverage and proof

The mandatory floor is **90% executable line coverage independently for Python and C++**. Python branch coverage is enabled and reported; native branch coverage is reported separately. Coverage includes all first-party production source, including unimported Python modules. Tests, generated output and external libraries are excluded by identity, not convenience. [Coverage policy](coverage.md) defines measurement and failure behavior.

Local proof uses real SQLite files, rooted workspaces, subprocess trees, socket/HTTP boundary fixtures, and the compiled native process. Remote-provider wire fixtures validate actual adapter I/O; they do not establish live paid-provider availability. Self-evolution tests build and evaluate harmless temporary candidates, never alter this checkout as a test side effect. No declared feature is complete solely because a unit test or coverage count passed.

The minimum supported concrete backends are SQLite operational/native storage, isolated Python plugin workers, restricted POSIX process/filesystem execution, a reusable async stream channel, and a real configurable HTTP streaming provider adapter. Their backend limits and unsupported operations must be explicit. Registry APIs permit additional providers/transports without adding product applications.
