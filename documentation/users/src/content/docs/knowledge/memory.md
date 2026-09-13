---
title: "Search, correct, and retract memory"
description: "Inspect remembered records and provenance, add corrections as new revisions, and handle unavailable or stale recall."
---

Memory provides recalled context from recorded experience and source-backed content. It is separate from the conversation transcript and source library. A remembered statement can be revised; changing it should preserve what was previously recorded and why the correction was made.

## Search and inspect

Open **Knowledge → Agent memory**, enter an idea or fact in **Search memory**, and choose **Search memory**. Inspect each result's content, kind, concepts, sources, and revision. A result is contextual evidence, not a new tool permission or an independently verified external outcome.

Through the CLI, save `query-memory.json`:

```json
{"text":"maintenance date","concepts":["observatory"],"limit":20}
```

```sh
sturgis command AGENT_ID memory.query --params-file query-memory.json
```

The consumer query accepts `text`, `concepts`, `embedding`, `at`, and `limit`. `at` filters the validity time of the current record view; it does **not** reconstruct what the agent knew at a historical moment. Use embeddings only when you have a compatible vector representation; the UI does not offer a manual vector editor. `memory.get` accepts a record ID and optional revision for direct inspection.

## Worked example: update a changed date

After verifying the corrected original source, choose **Correct memory** on the record stating the old date. Enter the complete corrected wording and choose **Save correction**. Expect a new record revision; do not expect the previous revision to be overwritten invisibly.

The correction is stored as a belief labeled **User correction**, linked to the correction event. That provenance records your instruction; it does not independently verify the original source or turn your wording into an observed fact. Keep the supporting source available and state any uncertainty in the correction itself.

The command form is `correct.json`:

```json
{"id":"MEMORY_ID","content":"The maintenance visit was in May, according to the corrected source.","expected_revision":3,"operation_id":"correct_maintenance_may"}
```

```sh
sturgis command AGENT_ID memory.correct --params-file correct.json --request-id correct_maintenance_may
```

Reread the memory result and confirm the new content and revision. Use the current revision rather than assuming it will always advance from 3 to 4 in a concurrently used agent.

## Retract a record

Retraction without a `revision` suppresses every revision of that memory identity, including future revisions. There is no public undo operation. To suppress only a specific revision, use the CLI/API with an explicit `revision`, as in the command example below.

The web **Retract** action retracts the whole identity: provide a **Reason**, review that scope, and confirm **Retract memory**. The affected material leaves current recall while its history remains. `memory.retract` accepts `id`, `reason`, `operation_id`, and optional `revision`. Use a reason such as “Original source withdrawn by its author” rather than erasing context about why the memory changed.

## Freshness, failure, and limits

The native memory process can be unavailable even when the conversation page or control service works. An unavailable query is not an empty successful search. Check status, freshness, ingestion lag, and [diagnostics](../settings/usage-diagnostics.md) before concluding that a record is absent.

Corrections and retractions preserve native status envelopes. If the response is an error, the dialog remains available for review. When HTTP acknowledgement is uncertain, retry with the same outer `request_id`, domain `operation_id`, and parameters to recover the original receipt.

A received successful HTTP envelope can still contain an unavailable native result. After memory recovers, retry the same domain `operation_id` and parameters with a **new outer `request_id`**; the old outer ID only replays its completed unavailable receipt. For the correction example, retain `correct_maintenance_may` in `correct.json` and use `--request-id correct_maintenance_may_resume_1`. A revision conflict means someone changed the record: retrieve the latest content before applying a new correction.

Background memory consolidation may update derived recall without changing original source bytes. Its sleep/freshness process is not an exposed button for training model weights. For replacing a whole source or index, use [knowledge revisions](source-library.md); for changing a workspace document, use [Files](../files/workspace.md).

Consolidation has one recorded cycle per day, using UTC in the standard runtime, and runs when background work can be admitted. Once that day's cycle is complete, later idle checks do not create additional sleep jobs. Interrupted or unfinished cycles can resume when their ownership permits. Regular ingestion and recall updates continue independently, so daily consolidation does not mean new inputs wait until the next day to become eligible for memory. Existing work and memory history remain available.

## Command examples

These are command-specific examples, not a script to run every operation. Replace uppercase IDs, example endpoints, timestamps, and revision numbers with the actual intended values. Save one JSON object as `params.json`, then run `sturgis command AGENT_ID METHOD --params-file params.json`. Omit the file for `{}`. If acknowledgement is unknown, keep request and domain operation identities unchanged. For an acknowledged unavailable result, follow the feature-specific recovery instructions.

| Method | Example parameters or private-prompt command |
| --- | --- |
| `memory.correct` | `{"id":"MEMORY_ID","content":"The corrected source identifies May.","expected_revision":1,"operation_id":"correct_date_1"}` |
| `memory.get` | `{"id":"MEMORY_ID","revision":1}` |
| `memory.query` | `{"text":"maintenance","limit":20}` |
| `memory.retract` | `{"id":"MEMORY_ID","reason":"Source withdrawn","revision":2,"operation_id":"retract_date_1"}` |
