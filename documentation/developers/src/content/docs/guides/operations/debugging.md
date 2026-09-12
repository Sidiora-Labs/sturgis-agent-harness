---
title: "Debug failures by causal boundary"
description: "Find the earliest unsupported claim and preserve unknown effects while investigating."
---

Start with the client request identity and agent, then follow control command, private receipt, input event, work generation, context packet, tool attempt and output stream. A generic “request failed” banner cannot identify which boundary failed.

## Worked incident

The user sees partial text and then an error. First confirm whether the control command was accepted and whether its private receipt is completed, failed or indeterminate. Find the corresponding core work and terminal event. Inspect the context/source manifest and provider terminal reason. Then check whether any tool intent was admitted and whether it has a known result or unresolved reservation.

If the provider refused or reached its output limit, partial text is expected and tool proposals must not execute. If a tool had already been authorized and its acknowledgment disappeared, keep it indeterminate and use a trusted observer. Do not retry it merely to make the UI green.

## Common failures

| Symptom/code | Boundary to inspect |
| --- | --- |
| `host_denied`, `origin_denied`, `csrf_failed` | Exact installation origin/host, cookie mode and CSRF proof |
| `worker_unavailable`, `isolation_unavailable` | Private roots, executable paths, process ownership and namespace availability |
| `worker_indeterminate`, `result_unavailable` | Original request identity and durable receipt; no fresh effect retry |
| Provider readiness/credential errors | Configuration revision, pinned credential currency and actual probe |
| `context_budget` | Required blocks, serialization overhead, output reservation and pending ranges |
| `budget_exceeded`, `goal_inactive` | Actual usage, held claims, goal state and parent allocation |
| Memory unavailable/partial | Native process diagnostic, dispatch state, generation/watermark and query bounds |
| `runtime_busy` during suspend | Active work, output, pending message commands and unresolved claims |

Diagnostics must remain bounded and avoid secrets. Worker stdout/stderr is not automatically a safe public error body. Typed errors are preferable to dumping internal exception strings.

A missing usage receipt remains an accounting fact even after the work is terminal. An archived runtime cannot be woken simply to inspect a historical receipt through ordinary command paths. Restore startup recovery must precede lifecycle admission.

Use [cancellation/recovery](../core/cancellation-recovery.md), [native transport](../memory/records-wire.md) and [frontend retry recovery](../frontend/mutation-recovery.md) for the corresponding repair contracts.

## Implementation sources

- [consumer/application.py](/reference/files/consumer/application.py/)
- [consumer/workers.py](/reference/files/consumer/workers.py/)
- [consumer/lifecycle.py](/reference/files/consumer/lifecycle.py/)
- [agent/runtime/__init__.py](/reference/files/agent/runtime/__init__.py/)
- [agent/tool_runner/__init__.py](/reference/files/agent/tool_runner/__init__.py/)
- [agent/memory/client.py](/reference/files/agent/memory/client.py/)
- [consumer/usage.py](/reference/files/consumer/usage.py/)

[Developer manual](../index.md)

