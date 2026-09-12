---
title: "Module ownership map"
description: "Find the single owner of each mechanism without creating duplicate control authorities."
---

The module boundaries are responsibilities, not independent agents. A module may participate in a request without owning its execution authority.

| Modules | Ownership and guide |
| --- | --- |
| `protocol`, `framing` | Shared semantic values and bounded byte envelopes; [protocol](../core/protocol.md) |
| `state`, `artifacts` | Transactions, revisioned records, causal events and immutable bytes; [state](../core/state.md), [artifacts](../core/artifacts.md) |
| `context`, `attention` | Exact context construction and explicit selection/policy decisions; [context](../core/context.md), [attention](../behavior/initiative-attention.md) |
| `profile`, `awareness` | Durable resource roots and attributed observation projections; [profiles and awareness](../behavior/profiles-awareness.md) |
| `goals`, `commitments`, `planner` | Outcome budgets, accepted responsibilities and dependency plans; [goals](../behavior/goals-budgets.md), [plans](../behavior/commitments-plans.md) |
| `initiative` | Evidence-linked work proposals, not permission to execute; [initiative](../behavior/initiative-attention.md) |
| `scheduler` | Shared admission, leases, allocation, timers and armed waits; [scheduler](../core/scheduler.md) |
| `runtime` | Supervision and composition of the continuous I/O loop; [runtime](../core/runtime.md) |
| `memory`, native worker | Ingestion, retrieval, sleep and generation publication; [memory](../memory/records-wire.md) |
| `tool_runner` | Validated intent, execution and typed observations; [tool authoring](../integrations/tool-authoring.md) |
| `plugin_sdk`, `plugin_host` | Extension contracts and governed isolated execution; [plugins](../integrations/plugins.md) |
| `skills` | Versioned procedural packages and evidence-backed synthesis; [skills](../integrations/skills.md) |
| `subagents` | Scoped children, messages and result admission; [subagents](../integrations/subagents.md) |
| `provider_catalog`, `provider_adapters` | Revisioned observed capabilities and model wire transport; [providers](../integrations/providers.md) |
| `channel_core`, `channel_adapters`, `delivery` | Authenticated bindings, transport adapters and durable output; [channels](../integrations/channels-delivery.md) |
| `acp` | Agent Client Protocol client integration boundary; [ACP](../integrations/acp.md) |
| `meta_harness`, `self_evolution` | Independent evaluation and governed source selection; [evolution](../behavior/evolution-evaluation.md) |

## Example: add a recurring research task

The consumer work adapter validates the owner's request. Planner may describe dependencies. Scheduler owns due times and missed-run policy. Runtime turns an occurrence into a normal model interaction. ToolDispatcher owns resulting calls; native memory can supply context. Delivery records output. None of these roles makes a second scheduler necessary.

Likewise, human permissions add conditions around the canonical tool authorization callback; they do not replace scheduler budgets. Account identity resolves the scope before that callback is reached. Use [authority and evidence](authority-evidence.md) to decide which boundary must reject an invalid request.

## Implementation sources

- [agent/runtime/__init__.py](/reference/files/agent/runtime/__init__.py/)
- [consumer/application.py](/reference/files/consumer/application.py/)
- [spec/sturgis-01/contracts.md](/reference/files/spec/sturgis-01/contracts.md/)

[Developer manual](../index.md)

