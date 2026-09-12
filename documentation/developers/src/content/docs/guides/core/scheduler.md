---
title: "Admission, leases and resource grants"
description: "Allocate work once and validate current authority at every effect boundary."
---

Scheduler is the shared owner of admission and execution claims. Planner can describe a task; attention can consider it eligible; only scheduler admission provides the current execution generation and lease.

```python
from agent.state import Journal
from agent.scheduler import Scheduler

journal = Journal(":memory:")
scheduler = Scheduler(journal, max_running=1)
work = scheduler.submit(
    "demo", "example", {"reason": "inspect status"},
    capabilities=["status.read"],
    resources={"tool_calls": 1, "elapsed_ms": 5000},
    dedupe_key="inspection-1",
)
admitted = scheduler.admit("demo", work.id, worker="example-owner")
generation = admitted.value["generation"]
scheduler.heartbeat("demo", work.id, generation)
scheduler.finish("demo", work.id, generation)
journal.close()
```

This example reserves standalone work without a goal. A real effect must still pass ToolDispatcher with a registered capability; do not treat the work record as an unrestricted execution token.

## Allocation and accounting

Resource dimensions are nonnegative integers defined by GoalService, including tokens, elapsed milliseconds, iterations, children and tool calls. Children reserve from their parents. Usage charges the ancestry and root goal once, avoiding duplicate spending authority.

`admit` respects global capacity and priority plus aging. `eligible_ids` lets an execution owner identify ready jobs, such as bindings that are not already busy; it does not bypass ordering or capacity.

At tool claim time, scope, generation, lease, ancestor state, capabilities, goal state and remaining allocation are checked in the intent transaction. Child workspace constraints come from admitted work, not model arguments.

## Factual usage survives failure

Known tool outcomes emit actual usage receipts. Indeterminate attempts retain reservations until reconciliation. Cancellation or goal pause does not erase already consumed resources. Inactive goals deny fresh admission/claims/heartbeats, while late factual usage and terminal cleanup remain possible.

An overrun records the real amount before blocking future work. Do not clamp a receipt to the planned limit to make the budget appear healthy. See [goals and budgets](../behavior/goals-budgets.md), [waits and timers](waits-timers.md), and [recovery](cancellation-recovery.md).

## Implementation sources

- [agent/scheduler/__init__.py](/reference/files/agent/scheduler/__init__.py/)
- [agent/goals/__init__.py](/reference/files/agent/goals/__init__.py/)
- [agent/tool_runner/__init__.py](/reference/files/agent/tool_runner/__init__.py/)

[Developer manual](../index.md)

