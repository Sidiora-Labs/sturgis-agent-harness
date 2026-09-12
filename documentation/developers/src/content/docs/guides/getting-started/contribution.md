---
title: "Make a change without splitting authority"
description: "Use ownership boundaries, narrow changes and evidence-oriented tests."
---

Begin by identifying the record owner and the public boundary affected by your change. A new schedule option belongs to scheduler validation and occurrence calculation. A new screen belongs to the frontend projection and existing consumer command boundary. A new human approval rule belongs to the permissions wrapper, while scheduler grants remain responsible for execution allocation.

For an additional command, follow this sequence:

1. Implement the operation on its existing domain service.
2. Register an explicit method name and allowed/required parameters in `AgentApplication`.
3. Add it to the public HTTP command allowlist only if it is intended for authenticated owners.
4. Have the client send a stable identity for a mutation and the current revision for a compare-and-swap update.
5. Exercise success, stale authority and an ambiguous acknowledgment where effects are possible.

Do not expose generic attribute lookup, arbitrary journal writes, or a client-selected filesystem root. Those shortcuts move authority into request data and bypass the reasons for the original boundary.

## Transaction discipline

A domain update and its causal event should commit together. Use the shared `Journal.transaction()`; methods may nest through savepoints. Never await while holding its synchronous transaction. For asynchronous services, move a short complete synchronous boundary to `asyncio.to_thread`, then await the actual external operation after the transaction has closed.

An irreversible or uncertain effect requires an intent record and current execution authorization before it begins. A failed schema check is different from a command that reached a remote service and lost its response. Preserve that distinction in both code and tests.

## Review evidence

Prefer assertions about observable invariants: another owner cannot resolve the agent; a stale revision changes nothing; a cancelled process has actually stopped; a lost acknowledgment does not repeat an effect. A test that only reconstructs the implementation's dictionary is weak evidence.

Keep implementation, qualification and deployment claims separate. The current executable plan and task records live in the specification. The [qualification guide](qualification.md) explains the independent language floors; [release practice](../operations/release-migration.md) explains why source changes after a gate require fresh qualification.

## Implementation sources

- `spec/sturgis-02/spec.kvx` (live task state in the checkout; see the [published workflow](/reference/files/spec/workflow.kvx/))
- [spec/sturgis-02/execution.md](/reference/files/spec/sturgis-02/execution.md/)
- [agent/state/__init__.py](/reference/files/agent/state/__init__.py/)
- [consumer/application.py](/reference/files/consumer/application.py/)

[Developer manual](../index.md)

