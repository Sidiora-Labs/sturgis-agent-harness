---
title: "Scoped subagents and result admission"
description: "Delegate within parent budgets and validate child evidence before completion."
---

A child is an explicit identity and work allocation under a parent. It does not acquire a new top-level scope, permission set or unlimited budget. Delegation checks ancestry, depth, parent capabilities, resources and workspace constraints in the shared journal transaction.

```python
async def delegation_integration_example(core, parent_work_id):
    return await core.delegate(
        parent_work_id, "Inspect the child workspace and report observations.",
        capabilities=["workspace.read"],
        resources={"tokens": 10000, "elapsed_ms": 30000,
                   "iterations": 2, "tool_calls": 2},
        workspace_mode="isolated",
    )
```

The embedding host awaits this function with its running CoreRuntime and the ID of a live admitted parent. The parent must already hold those capabilities and sufficient remaining allocation. Runtime creates actual child model work on a durable internal channel. Defining the function does not delegate work; a descriptor alone is not a running collaborator.

## Workspace and lifetime

Isolated child roots live beneath the parent's permitted root. Shared mode uses that root; inherited read-only restrictions still apply. Descendants cannot escape the root by replacing existing path prefixes. Unsupported custom tools and plugins reject delegated claims rather than ignore constraints.

Retained children can outlive a completed parent while retaining their parent allocation. Parent cancellation revokes them. Child expiry, heartbeat, recovery and archival remain separate recorded states.

Parent/child messages validate the sender's relation and current lineage. Sibling waits are restricted to a common parent and reject dependency cycles. The scheduler wait is armed before the child releases its compute slot.

## Evidence-bearing results

A submission includes a stable submission ID, summary and evidence event IDs. Checks use scoped operational events created after delegation. Accepted results finish child work and emit a terminal event. Partial or rejected results remain available for correction; a narrative does not establish success.

A memory scout is narrower: it receives only `memory.read`, queries the actual native client in the delegation scope, and consumes a scheduler reservation. Journal boundaries run off the event loop while native IPC remains asynchronous. Actual transport time is charged even on cancellation or unavailability.

Read [budgets](../behavior/goals-budgets.md) before splitting work and [durable waits](../core/waits-timers.md) before adding coordination behavior.

## Implementation sources

- [agent/subagents/__init__.py](/reference/files/agent/subagents/__init__.py/)
- [agent/scheduler/__init__.py](/reference/files/agent/scheduler/__init__.py/)
- [agent/runtime/__init__.py](/reference/files/agent/runtime/__init__.py/)

[Developer manual](../index.md)
