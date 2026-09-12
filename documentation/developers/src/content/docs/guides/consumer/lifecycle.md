---
title: "Dormancy, archive and scheduled wake"
description: "Stop compute only after durable input, output and wake ownership are settled."
---

Lifecycle stores desired and observed runtime state in the control journal. Process existence alone does not define whether an agent is running, suspended, archived or still being restored.

A safe suspend operation pauses the product workflow monitor, fences new core model/background admission, and inspects actual work, output, pending commands and unresolved reservations. It records the next scheduled wake before stopping the worker that owns the schedule. A cold agent may need to start briefly to inspect its durable scheduler before it can safely suspend.

## Worked sequence

1. The owner sends an explicit suspend request.
2. The worker quiesces workflow creation and core admission.
3. Lifecycle validates the idle snapshot and any future wake instant.
4. Desired suspended state and next wake are persisted.
5. WorkerManager stops the process and observed state becomes suspended.

If quiescence finds active work or undelivered output, suspension fails. The worker is resumed where possible; the client must reread the observed control state rather than assuming the requested transition happened.

## Inputs while compute sleeps

Lifecycle durably retains undispatched message send/cancel parameters. Other command records retain a digest and status while their parameters remain transient. An unknown dispatched command consults a receipt; it is not automatically forwarded again.

The lifecycle poller can wake a suspended runtime for its recorded due schedule or retained input. Restoring and archived agents remain fenced. Startup marks interrupted control dispatches indeterminate and recovers unpublished restores before ordinary lifecycle admission.

Archiving is stronger than stopping a process. It durably blocks commands, receipt lookups and scheduled wake. Metadata revision checks prevent a stale archive action from affecting the current agent state.

## Client behavior matters

GET agent metadata returns control-plane lifecycle state without starting a stopped private worker. Background UI queries must consult it first. Query invalidation, window focus and event polling must not wake an intentionally suspended runtime. Start/resume is explicit and requires a fresh observation before private reads resume.

Backups reuse lifecycle's stopped-worker snapshot boundary. See [frontend data](../frontend/data-events.md), [backup/restore](backup-restore.md), and [runtime pause](../core/runtime.md).

## Implementation sources

- [consumer/lifecycle.py](/reference/files/consumer/lifecycle.py/)
- [consumer/work.py](/reference/files/consumer/work.py/)
- [consumer/application.py](/reference/files/consumer/application.py/)
- [consumer/workers.py](/reference/files/consumer/workers.py/)
- [agent/runtime/__init__.py](/reference/files/agent/runtime/__init__.py/)

[Developer manual](../index.md)

