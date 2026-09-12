---
title: "Glossary and state vocabulary"
description: "Understand the identities, records, evidence, resource states, and trust boundaries used throughout Sturgis."
---

Use these terms when reading a result or describing a problem. Similar labels often identify different layers, and choosing the correct identity matters when retrying or inspecting work.

| Term | Meaning in Sturgis |
| --- | --- |
| Account / owner | The authenticated identity that owns agents. Administrative status does not bypass another owner's agents. |
| Agent | A private space containing conversations, work, files, knowledge, memory, and configuration. |
| Runtime / worker | The current process executing an agent. Its lifetime is shorter than the durable agent identity. |
| Profile / scope | Server-assigned runtime resource and data boundaries. Consumer commands do not choose arbitrary roots or scopes. |
| Conversation | A continuing thread and delivery binding inside one agent. |
| Message ID | Stable identity for one exact prompt, steering input, or cancellation. |
| Request ID | Client identity for an API/worker operation; repeated identical requests can resolve an earlier acknowledgement. |
| Operation ID | A domain-specific mutation identity, required by uploads, knowledge revisions, and memory corrections/retractions. |
| Revision | Concurrency version of a saved record. It is not a model name, document title, or skill version string. |
| Cursor | Position in an ordered event stream used for replay. It is not an event ID. |
| Attachment | Uploaded original source bytes and extraction metadata belonging to an agent. |
| Artifact | Immutable stored bytes identified by content/type; used for sources, outputs, and exact history. |
| Knowledge document | Versioned extracted source text plus its index references. |
| Memory | Canonical or derived recall context with sources, revisions, and possible retractions. |
| Retraction | A recorded withdrawal from current recall that preserves history. |
| Goal | Desired outcome and limits; proposed/active status alone does not admit model execution. |
| Commitment | An accepted responsibility with beneficiary, progress, and fulfillment criteria. |
| Workflow / plan | Versioned dependency steps, budgets, assignees, and checks. |
| Schedule | Durable due-time rule that can admit a prompt occurrence. |
| Child | Bounded delegated work under a parent's authority. |
| Initiative | An evidence-linked proposal, not automatically an accepted task. |
| Attention policy | Rules deciding how proposals are remembered, surfaced, deferred, or admitted. |
| Grant | Capability authority to use a configured operation. |
| Permission / approval | Policy for a tool, or single-use authorization for an exact current invocation. |
| Readiness | Time/configuration-bound evidence that a probe succeeded, with adapter-specific meaning. |
| Allocation / reservation | Allowed or held resources. They are not automatically actual consumption or monetary charges. |
| Receipt / evidence | Durable observation supporting an input, operation, result, or state change. |
| Unknown effect | An external outcome that has not been reliably settled; a timeout does not turn it into success or failure. |
| Imported history | Preserved records from a backup with inert provenance; not new input or active authority. |

## Worked example: name the failed layer

“My agent failed” can refer to several different situations. A browser may be reconnecting while the accepted message still runs. A model may have finished while an external device result is unknown. A memory query may be unavailable while the control service and file browser are healthy.

Describe the narrow observation instead: “Request `R` admitted message `M` into conversation `C`; work `W` is waiting, and provider readiness is stale.” This preserves what is known without pretending you have diagnosed the cause. IDs in this sentence are placeholders for actual records, not interchangeable values.

## Use state labels carefully

Queued, running, waiting, blocked, and terminal states describe execution. Proposed, accepted, active, fulfilled, achieved, and abandoned belong to particular intention domains. `Ready` on a media connection means catalog membership, and on a device means pairing; neither proves a later operation.

When unsure which record to inspect, start with [Work overview](../work/overview.md) and [diagnostics](../settings/usage-diagnostics.md). Use [the command reference](commands.md) for exact parameter names and [troubleshooting](troubleshooting.md) for recovery that retains evidence.
