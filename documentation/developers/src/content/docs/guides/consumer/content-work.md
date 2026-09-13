---
title: "Content, conversations and executable workflows"
description: "Connect product features to source artifacts and existing work owners."
---

ConsumerData owns uploaded attachments, public workspace content, knowledge revisions and the user-facing native-memory bridge. AgentApplication owns conversations, immutable message receipts and source-inclusion requests. These services reuse artifact, context and memory owners rather than maintaining a second conversation engine.

## Conversation history projection

`AgentApplication.conversation_list` applies archive and optional title/message search filters, then orders records by `(created_at, id)` descending. The ID is a stable tie-breaker for equal creation timestamps; updates and renames do not move a conversation as though it had just been created.

`AgentApplication.events` resolves an optional conversation through the same scoped owner and calls `Journal.routed_events` with only `user.prompt`, `user.steer`, `user.cancel`, `output.persisted`, and the completed/failed/interrupted runtime-turn kinds. Scope, public kind, and binding filtering happen before the page limit. Terminal events without an explicit binding resolve through their scoped runtime work record. This lets a saved thread be read directly even when its journal also contains extensive maintenance history.

The public envelope remains `{items, cursor, has_more}`. Each item retains its event ID, sequence cursor, kind, source as `origin`, and original payload. An exhausted page may advance the cursor across hidden events; clients must use the returned cursor instead of deriving one from item counts. See [routed event snapshots](../core/state.md#indexed-routed-event-pages) and [client catch-up](../frontend/data-events.md#event-replay). The projection preserves the original messages, output chunks, terminal evidence, and import provenance.

## Source-to-context walkthrough

1. Upload bounded base64 bytes with a stable operation ID.
2. Store the immutable artifact and explicit extraction status.
3. Add a supported text attachment as a knowledge document if durable retrieval is desired.
4. Send a message referencing only attachment IDs in the same agent.
5. Persist the source-inclusion request before core admission.
6. Supply labeled source blocks through the context callback.

Unsupported binary interpretation remains explicit. A stored image or PDF is not automatically OCR or vision understanding. Successfully extracted sources can be excerpted by the default 16 KiB attachment-context allowance; their start, end and total byte counts identify omitted content. Extraction above 256 KiB instead returns `deferred` with no extracted body or text ranges. Original bytes remain available through bounded content reads. Workspace file commands enforce relative public paths and preimages, excluding internal control content.

Knowledge updates are revisioned and preserve sources. Memory correction/retraction use native mutation identities; they do not rewrite the original operational event that supplied evidence. A correction creates a belief labeled `User correction`, linked to the correction event rather than independently verifying the original source. Retraction without a revision suppresses all existing and future revisions of that identity. There is no public undo, and a retracted record cannot be corrected in place. Supply an explicit revision when only that revision should be suppressed.

## WorkViews executes through the core

The work adapter exposes real goals, commitments, plans, schedules, child results, initiative decisions and inbox entries. A workflow defines step descriptions, dependencies, checks and per-step budgets under a shared budget.

`workflows.run` binds a workflow to an actual conversation. Its monitor advances ready agent steps by creating bounded scheduled core work, then uses observed terminal work/evidence for step transitions. It does not complete a plan merely because its description was saved. User- or child-assigned steps retain their distinct ownership.

Pausing the monitor joins any in-flight advancement before lifecycle snapshots. Cancelling or abandoning a workflow fences its associated work/schedules; previously observed evidence remains.

## Failure interpretation

A model interaction can complete while an evidence-checked step remains incomplete. A child can return a useful partial result without meeting every criterion. A goal can remain active while its current workflow is paused. Keep these distinctions visible in product projections instead of flattening them into one generic success flag.

Knowledge indexing and memory mutations can return a successful HTTP envelope containing an unavailable native result. That outer request has a completed worker receipt: replaying its `request_id` only returns the saved unavailable result. After native recovery, a backend or CLI caller resumes the content operation with a new outer `request_id` and the same domain `operation_id` and parameters. When acknowledgement itself is unknown, preserve both identities instead. A new knowledge revision is a different domain operation and needs its own operation ID.

UsageView reports factual consumption and reservations from the same execution owners. See [plans](../behavior/commitments-plans.md), [HTTP catalog](http-commands.md) and [context](../core/context.md).

## Implementation sources

- [consumer/content.py](/reference/files/consumer/content.py/)
- [consumer/application.py](/reference/files/consumer/application.py/)
- [consumer/work.py](/reference/files/consumer/work.py/)
- [consumer/usage.py](/reference/files/consumer/usage.py/)
- [agent/planner/__init__.py](/reference/files/agent/planner/__init__.py/)
- [agent/state/__init__.py](/reference/files/agent/state/__init__.py/)

[Developer manual](../index.md)
