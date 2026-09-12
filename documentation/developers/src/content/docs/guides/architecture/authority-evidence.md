---
title: "Authority, evidence and outcomes"
description: "Keep permission to act separate from observations and claims of completion."
---

Authority answers whether an action may begin. Evidence answers what a permitted observer actually established. Completion is a domain decision made using explicit evidence checks. Treating these as separate mechanisms prevents a plausible model response from manufacturing success.

The consumer authenticates an owner and resolves a private scope. Permissions and connection policy constrain which capabilities are available. Scheduler grants bind work identity, generation, resource allocation and delegated workspace constraints. ToolDispatcher checks them immediately before execution.

## A concrete outcome criterion

Suppose an embedding application registers a trusted watcher for a build backend. A goal can require an operational event shaped like this:

```python
criteria = [{
    "id": "build-passed",
    "event_kind": "build.observed",
    "source": "build_observer",
    "matches": {"build_id": "build-42", "exit_code": 0},
}]
```

The application must actually obtain the backend observation and append it under that trusted source. Later, the goal owner receives the event ID and calls its evidence-checked transition. Merely putting this dictionary in a prompt establishes nothing.

Checks require explicit event kind, source and nonempty payload matches. Dot-separated paths select payload fields. Generated model, memory and child-summary sources cannot serve as operational completion checks. Evidence resolution is scoped.

## Reconciliation has a stricter causal join

An unknown tool attempt requires an exact `tool.observation` from `tool_observer`, including its earlier request event and full proposed terminal result. Matching an unrelated event with a success-shaped payload is insufficient. The observer is a trusted in-process boundary, not cryptographic attestation by itself.

Do not expose generic journal append or reconciliation capabilities that allow generated content to choose trusted provenance. If an external service provides signed receipts, validate them in the backend integration before constructing the operational observation.

Read [tool effects](../integrations/tool-effects.md) for the actual reconciliation payload and [goals](../behavior/goals-budgets.md) for budget and achievement transitions.

## Implementation sources

- [agent/awareness/evidence.py](/reference/files/agent/awareness/evidence.py/)
- [agent/tool_runner/__init__.py](/reference/files/agent/tool_runner/__init__.py/)
- [consumer/permissions.py](/reference/files/consumer/permissions.py/)
- [agent/goals/__init__.py](/reference/files/agent/goals/__init__.py/)

[Developer manual](../index.md)

