---
title: "Goals and cumulative budgets"
description: "Represent outcomes with evidence checks and charge factual resource usage."
---

A goal describes an intended outcome. It starts proposed, becomes active through an explicit transition, and reaches achieved only when its configured evidence checks pass. The end of a model interaction is not a goal outcome.

```python
goal = core.goals.create(
    core.scope, "Produce an observed build result.",
    limits={"tool_calls": 10, "elapsed_ms": 120000},
    criteria=[{
        "id": "verified-build", "event_kind": "build.observed",
        "source": "build_observer",
        "matches": {"build_id": "build-42", "exit_code": 0},
    }],
)
goal = core.goals.transition(
    core.scope, goal.id, "active", expected_revision=goal.revision)
```

The evidence source in this example must be supplied by a real trusted build integration. Goal activation does not itself execute a command; submit work through scheduler with this goal ID.

## Budget dimensions

The shared integer dimensions include tokens, elapsed milliseconds, iterations, turns, children, tool calls, reviews, retries, processes, storage bytes and cost microunits. A goal's configured limits constrain accumulated usage plus reservations in those dimensions. A work item has its own explicit allocation.

A reservation identity binds an allocation. Actual usage receipts are idempotently charged; releasing a reservation returns its unused portion. Child usage reaches the ancestors and root goal once. Reusing the same receipt must not charge twice.

If actual consumption exceeds the plan, retain the real amount and mark the overrun. Fresh work is denied until an explicit edit resolves the limit. Reducing a configured limit below usage plus outstanding reservations is rejected. Cancellation cannot erase prior consumption.

## State changes

Active goals can pause, wait, enter review, become blocked, fail or be abandoned through their allowed transitions. Blocked/failed/abandoned transitions require reasons. Terminal goals cannot be edited; only terminal goals may be archived.

Consumer usage summaries expose factual resources, held allocation and availability of provider usage. They do not invent a price estimate: cost estimates remain absent unless authoritative data exists. See [scheduler](../core/scheduler.md), [permissions](../consumer/ownership-auth.md), and [work adapters](../consumer/content-work.md).

## Implementation sources

- [agent/goals/__init__.py](/reference/files/agent/goals/__init__.py/)
- [agent/scheduler/__init__.py](/reference/files/agent/scheduler/__init__.py/)
- [consumer/usage.py](/reference/files/consumer/usage.py/)
- [consumer/work.py](/reference/files/consumer/work.py/)

[Developer manual](../index.md)

