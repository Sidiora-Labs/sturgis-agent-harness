---
title: "Cancellation, restart and unknown outcomes"
description: "Stop future authority without inventing what happened to an existing effect."
---

Cancellation changes what may happen next. It does not retroactively prove that a process, file write or remote operation never happened.

The runtime fences the work generation and signals cancellation. ProcessRunner performs actual namespace/process cleanup before reporting a confirmed stopped execution. A generic executor that cannot prove termination remains indeterminate. Already emitted text stays in the outbox and already authorized effects stay in the journal.

## Worked incident: acknowledgment lost

A tool records intent, obtains its claim, changes an external system, then loses its response when the owner process dies. After restart:

1. The core takes exclusive ownership before recovering records.
2. Previously running inference becomes interrupted.
3. In-flight tool attempts become indeterminate.
4. Their unresolved reservations remain held.
5. A trusted observer checks the external system and supplies causal reconciliation evidence.
6. Only that evidence can settle the terminal result and measured usage.

Replaying the tool because the user did not see a response could duplicate the effect. Returning “failed” with zero usage would also be false.

## Input recovery is narrower

Durably admitted input lacking a work join can be materialized after restart. An undispatched retained consumer message can be forwarded after recovery. These are missing-admission cases, not permission to repeat a dispatched effect.

Control commands in a dispatching state become indeterminate on control restart. A subsequent same-identity request consults the private worker receipt. The frontend and CLI must preserve the original request identity across an unknown acknowledgment.

## Shutdown order

Pause product workflow scheduling before core admission. Check active work, outbox and unresolved claims before suspending a worker. The live worker returns its lifecycle snapshot after quiescence so Lifecycle can validate idle state before stopping it. The subsequent filesystem/database backup snapshot requires the worker to be stopped and the lifecycle fence held throughout copying. Close asynchronous monitors before closing their journal.

For a confirmed failure, keep the typed error and original partial output. For an unknown outcome, keep uncertainty visible until evidence resolves it. [Tool reconciliation](../integrations/tool-effects.md), [lifecycle](../consumer/lifecycle.md) and [frontend recovery](../frontend/mutation-recovery.md) describe the matching APIs.

## Implementation sources

- [agent/runtime/__init__.py](/reference/files/agent/runtime/__init__.py/)
- [agent/tool_runner/__init__.py](/reference/files/agent/tool_runner/__init__.py/)
- [agent/tool_runner/process.py](/reference/files/agent/tool_runner/process.py/)
- [consumer/lifecycle.py](/reference/files/consumer/lifecycle.py/)
- [consumer/workers.py](/reference/files/consumer/workers.py/)

[Developer manual](../index.md)
