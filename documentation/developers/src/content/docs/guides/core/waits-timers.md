---
title: "Durable waits and calendar schedules"
description: "Arm before yielding and let the scheduler materialize due work."
---

A wait must be durable before execution releases its slot. Otherwise an event can arrive between checking a condition and subscribing to it, leaving the agent asleep after the fact it needed has already occurred.

`register_wait` records the owner, exact trigger, cursor and optional expiry. `park` requires that armed record. `fire_waits` persists one deduplicated wake item and requeues a matching parked work owner. Cancellation also cancels the pending wake.

Each trigger includes its `kind` and exactly the fields below. An external-condition match must be a nonempty object; identities must be bounded strings.

| Kind | Additional fields |
| --- | --- |
| `time` | `at` as an epoch instant |
| `schedule` | `job_id` |
| `child_terminal` | `child_id` |
| `process_exit` | `process_id` |
| `file_changed`, `repository_changed` | `object_id` |
| `channel_message`, `user_response` | `conversation_id` |
| `external_condition` | `connector`, `matches` |

## A scheduled prompt

For a real consumer conversation, the application calls the runtime's schedule owner:

```python
import time

schedule = core.schedule_prompt(
    binding_id, "Review the current project status.",
    {"kind": "once", "at": time.time() + 3600},
    resources={"tokens": 20000, "elapsed_ms": 60000,
               "iterations": 4, "tool_calls": 8},
)
```

The binding must already exist and the caller must have authority to schedule this work. At the due instant, scheduler materializes an occurrence and runtime routes it through normal context, grants and outbox delivery. A schedule does not create a second model loop.

One-shot, anchored interval and weekday/local-time calendar specifications share this mechanism. Calendar jobs use an IANA timezone. DST fold policy chooses the first, second or both repeated instants; nonexistent local times are skipped. Missed runs use `skip`, `run_once` or bounded replay. Do not replace these semantics with “sleep for 24 hours” when the user's intent is a local calendar time.

## Process-exit evidence

A process wait uses the process tool result's `attempt_id` as its `process_id`. The scheduler checks persisted executor provenance and terminal proof. An arbitrary plugin result with an `exit_code`-shaped field cannot wake the wait. Indeterminate outcomes also cannot establish exit.

A commitment wait and a sibling-child wait use their own owners but the same arming principle. See [commitments](../behavior/commitments-plans.md) and [subagents](../integrations/subagents.md).

## Implementation sources

- [agent/scheduler/__init__.py](/reference/files/agent/scheduler/__init__.py/)
- [agent/commitments/__init__.py](/reference/files/agent/commitments/__init__.py/)
- [agent/runtime/__init__.py](/reference/files/agent/runtime/__init__.py/)

[Developer manual](../index.md)
