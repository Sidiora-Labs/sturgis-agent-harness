# Admission and delegation

`Scheduler(journal, goals=None, max_running=4, lease_seconds=30, clock=None)` is the
single work and execution-grant owner. All synchronous methods use the shared
Journal transaction; asynchronous callers run these short boundaries in a worker
thread and never await while holding a transaction.

`submit(scope, kind, payload, capabilities=(), resources=None, goal_id=None,
parent_id=None, dedupe_key=None, priority=0, not_before=None, constraints=None,
retain_after_parent=False)` creates durable queued work. `admit(scope, work_id=None,
worker="runtime", eligible_ids=None)` grants one ready candidate, ordered by
priority plus age. Readiness IDs restrict candidates without bypassing that order
or the global running capacity. Resources use the GoalService integer dimensions.
Children reserve from parents; descendant usage charges each ancestor and the root
goal once. Standalone work may have an explicit allocation without a goal.

`authorize(scope, grant, ToolCall)` is the mandatory ToolDispatcher callback.
Current scope, ancestor generation, leases, capabilities, goal state and remaining
allocations are checked in the same transaction as the tool intent. Constraints
come from admitted work and cannot be supplied through model tool arguments.
`record_usage` preserves actual overages and deduplicates receipts;
`reconcile_usage` consumes actual `tool.usage` journal events. Unknown attempts keep
their reservations until explicit usage reconciliation, including after cancel.
`heartbeat`, `finish`, `cancel` and `recover` manage work lifetime and generation
fencing. Inactive goals deny fresh execution but still permit factual usage and
terminal cleanup. Expired leases with unresolved claims require reconciliation
and are never automatically replayed.

`register_wait(scope, owner_id, trigger, wait_id=..., after_seq=...,
expires_at=None)` durably arms a wake. `park` requires that armed record before
releasing compute. `fire_waits` persists one deduplicated wake work item and
requeues a matching parked work owner. `cancel_wait` cancels its pending wake.
Process waits use a process ToolResult's **attempt_id** as `process_id`: a stored
process executor provenance marker and matching terminal result must prove exit.
Process-shaped custom-tool results and indeterminate outcomes cannot wake them.

`schedule` supports one-shot instants, anchored intervals and weekday/local-time
calendars. Calendars specify an IANA timezone and DST fold policy (`first`, `second`,
`both`); nonexistent local times are skipped. `tick` handles missed runs with
`skip`, `run_once` or bounded replay. `set_schedule_state` pauses or cancels jobs.

`Subagents(journal, scheduler, workspace_root=..., memory=None, max_depth=4)` owns
child identity and results. `delegate` creates queued work of kind `child`, with
parent capability and resource ceilings. Isolated workspaces live below the parent
root; shared mode uses that root. Read-only constraints propagate to descendants.
`message`/`messages`, `wait_for`/`resume`, `heartbeat`, `recover`, `cancel` and
`archive` implement scoped messaging, sibling waits, cycle rejection and liveness.
Retained children may outlive a completed parent while keeping its allocation;
parent cancellation revokes them.

`submit_result(..., evidence_event_ids, summary, submission_id)` admits accepted,
partial or rejected results against explicit checks and scoped operational events
created after delegation. A narrative cannot establish success. Accepted results
finish child work and emit a terminal event. Partial and rejected results remain
active for correction. Archived identities retain their evidence.

`await scout(scope, child_id, **query)` uses the actual configured native memory
client. Scouts receive only `memory.read`, force the delegation scope, reject other
operations and execute under a scheduler query reservation. Synchronous journal
and grant work runs off the event loop; native IPC stays asynchronous. Actual
transport time is charged even when a read is cancelled or unavailable.
