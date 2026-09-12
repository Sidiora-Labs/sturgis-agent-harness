---
title: "Commitments, dependency plans and completion"
description: "Separate an accepted responsibility from the steps used to discharge it."
---

A commitment records a responsibility to a beneficiary. It starts proposed and must be accepted before it can be fulfilled, failed or cancelled. Progress and waiting are separate from that lifecycle. A due time marks an accepted commitment overdue; it does not automatically prove failure.

Planner owns the versioned steps used to carry out work. Each step has an identity, description, dependencies, assignee, expectation, evidence checks and budget.

```python
plan = core.planner.create(core.scope, [
    {"id": "inspect", "description": "Inspect the current inputs",
     "budget": {"tool_calls": 2}},
    {"id": "report", "description": "Prepare a report from observations",
     "dependencies": ["inspect"], "budget": {"tool_calls": 1}},
], budget={"tool_calls": 3})
ready = core.planner.ready(core.scope, plan.id)
assert [step["id"] for step in ready] == ["inspect"]
```

This illustrates planning only. The consumer workflow adapter can schedule actual model work for ready agent steps; a plan dictionary alone executes nothing. Steps without outcome checks cannot be marked completed merely by supplying a narrative.

## Dependencies and revisions

Dependencies must refer to known unique step IDs and be acyclic. Assignees are the agent, the user, or an identified child. A plan can be revised while live only when no step is in progress. Unchanged steps retain their evidence and state; changed definitions return to the appropriate fresh state rather than inheriting proof for a different task.

A skipped dependency does not count as completed for `ready`. Plan completion requires all steps completed or skipped, with explicit reasons where required by the transition.

## Durable waiting

A commitment's `begin_wait` receives the scheduler registrar, trigger, cursor and expected revision. It arms the wake before recording waiting progress. `resume_wait` requires the matching fired wait. Cancellation invokes the configured wait cancellation seam, preventing an orphaned wake from reviving cancelled responsibility.

Use [waits and timers](../core/waits-timers.md) for trigger formats and [content/work services](../consumer/content-work.md) for user-facing execution. Goals own resource ceilings; commitments own accepted responsibility; planner owns dependencies. They should reference one another rather than duplicate those records.

## Implementation sources

- [agent/commitments/__init__.py](/reference/files/agent/commitments/__init__.py/)
- [agent/planner/__init__.py](/reference/files/agent/planner/__init__.py/)
- [consumer/work.py](/reference/files/consumer/work.py/)

[Developer manual](../index.md)

