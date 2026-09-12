---
title: "Read evidence and completion correctly"
description: "Separate model statements, durable records, operational observations, imported history, and verified outcomes."
---

Sturgis records several kinds of truth: what you requested, what was admitted, what the model said, what a tool reported, and what evidence supports an outcome. These records answer different questions. Reading them correctly prevents an interrupted or unverified action from looking complete.

## Inspect before changing state

In Work, expand **Inspect evidence** on the relevant record. Match the work, action, attempt, source, and revision to the operation you care about. In conversation history, distinguish persisted output from a terminal runtime event. Imported history is labeled as imported and cannot act as fresh operational authority.

`work.get`, `workflows.get`, `children.get`, `goals.list`, and `commitments.list` expose the supporting records through the CLI/API. `messages.events` and the events route expose the bounded public conversation stream; they are not an unrestricted dump of every private journal event.

## Worked example: a finished draft versus a sent message

A workflow step says “Draft a maintenance update.” Its default completion check can be satisfied by a `runtime.turn_completed` event whose payload explicitly says the external outcome is not asserted. That is useful evidence that drafting finished.

A step says “Send the maintenance update to a device.” The same model-turn event does not establish delivery or the device's effect. Inspect the connection's actual action/result evidence and any unresolved effect status. If the response was lost, preserve the original identity while investigating.

For an advanced goal that intentionally tracks model-turn completion, a criterion has this shape:

```json
{"id":"draft_finished","event_kind":"runtime.turn_completed","source":"runtime","matches":{"external_outcome":"not_asserted"}}
```

Supply it in `goals.edit` or `goals.create` only when that is the intended outcome. Later, `goals.transition` to `achieved` requires an `evidence` list of actual matching event IDs. Replace placeholders with observed IDs; this example does not create those observations or certify a world effect.

## What outcome checks require

A check has exactly `id`, `event_kind`, `source`, and nonempty `matches`. The matching keys address payload fields, including dotted paths. Up to 128 checks and a bounded list of evidence IDs are supported. Events must exist in the correct scope and match the declared source, kind, and expected payload.

Checks reject model/memory/child-narrative sources. Goals and commitments with no explicit checks cannot be marked achieved/fulfilled by supplying a persuasive paragraph. Workflow steps added through the UI have a narrower model-turn completion basis that remains visible.

## Failure and recovery

`missing_checks`, `missing_evidence`, or `check_failed` means the requested outcome is not supported by the supplied record. Fix the criterion if it describes the wrong outcome; otherwise obtain the real observation. Do not replace an evidence source with imported history or an unrelated same-scope event.

An uncertain effect needs a source- and action-linked reconciliation path supplied by the runtime or integration. The consumer interface does not expose arbitrary “declare success” authority. [Permissions](../settings/permissions.md), [cancellation](../conversations/steering-and-cancellation.md), and [device results](../connections/devices.md) each preserve that distinction.
