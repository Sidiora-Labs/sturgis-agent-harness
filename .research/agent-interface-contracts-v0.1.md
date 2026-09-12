# Agent interface contracts — v0.1

Companion to the [architecture schematic](agent-architecture-v0.1.md). Date: **12 September 2026**. These are proposed language-neutral contracts for a Python agent and a C++ memory engine. Examples are synthetic illustrations, not execution evidence. Executable schemas and implementations have not been created.

## 1. Shared event envelope

Every accepted user input, model-output event, tool observation, and operational transition has this outer structure. Typed payload schemas vary by `kind`.

```json
{
  "schema_version": "1.0",
  "event_id": "evt_103",
  "entity_id": "entity_1",
  "event_seq": 103,
  "kind": "tool.result",
  "source": {
    "source_type": "tool_adapter",
    "source_id": "terminal_adapter_1",
    "content_origin": "external_observation"
  },
  "correlation": {
    "run_id": "run_12",
    "action_id": "action_19",
    "attempt_id": "attempt_1",
    "caused_by_event_id": "evt_99"
  },
  "time": {
    "occurred_at": null,
    "observed_at": "2026-09-12T12:00:04Z",
    "recorded_at": "2026-09-12T12:00:04.010Z"
  },
  "payload_schema": "tool.result/1",
  "payload": {}
}
```

`payload` is filled with the kind-specific body; the empty object above only shows where that body belongs. The runtime assigns identifiers, scope, provenance, and journal sequence. Input text that imitates these fields cannot modify them.

- `event_seq` orders committed events for this entity. It does not assert external causal order.
- `occurred_at` is the source's claimed event time, if known; its trust follows that source. `observed_at` and `recorded_at` are runtime timestamps.
- `correlation` links a run, action, attempt, and immediate causal event where applicable. Missing relationships use null rather than fabricated IDs.
- Source-content authority is separate from transport authenticity. An authenticated tool can return inaccurate or untrusted third-party content.
- Producer delivery IDs deduplicate transport retries before a second accepted event is created. Identical text alone is not a deduplication key.

Representative kinds: `user.message`, `external.observation`, `model.output.delta`, `model.output.terminal`, `tool.request`, `tool.progress`, `tool.result`, `action.reconciled`, `commitment.changed`, `memory.published`, and `runtime.status`.

The version negotiator rejects incompatible major versions. Additive optional fields can be introduced under a compatible minor revision. Unknown enum values or payload schemas produce explicit incompatibility; they are never coerced into success.

## 2. Uniform model invocation

The model has one stable invocation surface, `invoke`, with the following argument shape:

```json
{
  "schema_version": "1.0",
  "tool": "web.fetch",
  "tool_version": "1",
  "arguments": {
    "url": "https://example.com/document",
    "method": "GET"
  },
  "expectation": {
    "description": "Retrieve the requested document",
    "observable": "HTTP response containing document content",
    "horizon_ms": 10000
  }
}
```

The same fields invoke `terminal.execute`, a file operation, a memory read exposed as a tool, or another capability. Tool-specific `arguments` validate against the selected registry entry. Tools may publish richer expectation schemas for machine-checkable assertions; free-text expectations remain claims rather than executable proof.

### Runtime request fields

| Field | Meaning |
| --- | --- |
| `action_id` | Logical intended action, retained across explicitly authorized retries. |
| `attempt_id` | One execution attempt of that action. |
| `request_id` | One dispatch submission; replaying an accepted submission reuses it. |
| `run_id` and `authority_generation` | Inference lineage permitted to initiate this action. |
| `preconditions` | Current object versions, required capabilities, and operation-specific conditions. |
| `deadline_at` | Wall-clock persistence of the budget; local execution uses monotonic elapsed time. |
| `idempotency` | Declared target support and key, if available. |
| `limits` | Runtime-applied output, concurrency, duration, and resource bounds. |
| `expectation_id` | Reference to the recorded pre-action expectation. |

Tool metadata specifies the argument schema, result payload schema, side-effect class, accepted target statuses, streams, cancellation semantics, and reconciliation operation if one exists. Pure/read operations and side-effecting operations can have different retry policies while retaining the same calling structure.

### Validation and dispatch

1. Buffer an incomplete provider tool call until its arguments are complete.
2. Parse strictly; reject duplicate JSON keys, invalid types, unknown tool versions, and unsupported fields under the chosen schema.
3. Validate scope, current run authority, and tool preconditions. Check object versions again at execution when the target can support conditional operations.
4. Persist the expectation, action, attempt, and dispatch record in one operational transaction.
5. Atomically claim the recorded attempt only if its `authority_generation` is still current. Otherwise persist cancellation/rejection without target dispatch. A lost wake-up signal is repaired by scanning ready records.
6. Execute the claimed attempt. The atomic claim is the dispatch-authorization boundary: later steering cannot guarantee that an already-authorized external operation will be prevented. Apply target-side conditional writes or fencing where supported, and retain cancellation/reconciliation for effects already in flight.

A rejected call returns stable error codes and field paths through the same result envelope. No target execution occurs. A bounded model correction cycle may follow; repeated malformed output ends that attempted operation with an explicit failure. Guessing at a malformed command is prohibited.

## 3. Uniform tool results

The following is the `tool.result/1` body inside the shared event envelope:

```json
{
  "request_id": "request_42",
  "action_id": "action_19",
  "attempt_id": "attempt_1",
  "tool": "terminal.execute",
  "tool_version": "1",
  "adapter_version": "1",
  "state": "completed",
  "outcome": "target_error",
  "data": {
    "media_type": "application/json",
    "schema": "terminal.result/1",
    "value": {
      "exit_code": 2,
      "stdout": "",
      "stderr": "Invalid option"
    }
  },
  "streams": [],
  "artifacts": [],
  "completeness": {
    "truncated": false,
    "missing_ranges": [],
    "observation_complete": true
  },
  "error": {
    "layer": "target",
    "code": "NONZERO_EXIT",
    "message": "The process exited with code 2",
    "field_errors": [],
    "retry_class": "never"
  },
  "effects": {
    "certainty": "unknown",
    "evidence_event_ids": []
  }
}
```

The illustrative operation contract classifies this exit code as an error. Some programs use nonzero codes for expected domain outcomes; that distinction belongs in their declared contract. The exit code always remains available regardless of classification.

### Valid lifecycle/outcome combinations

| State | Allowed outcome | Meaning |
| --- | --- | --- |
| `accepted` | null | Dispatch accepted; no outcome established. |
| `running` | null | Execution is observed in progress. |
| `completed` | `success`, `target_error`, `execution_error` | Terminal invocation result observed. |
| `rejected` | `rejected` | No dispatch due to validation, scope, or precondition failure. |
| `cancelled` | `cancelled` | Execution cancellation confirmed; effects have separate certainty. |
| `indeterminate` | `unknown` | Available evidence cannot establish a terminal outcome. |

Progress uses `tool.progress` with the same lifecycle meanings. A timeout alone is insufficient evidence of confirmed cancellation. An indeterminate attempt may later receive a separately linked reconciliation event; previous observations are retained.

`effects.certainty` is `none`, `observed`, or `unknown`. `none` requires evidence that no effect was attempted/applied under the operation contract. `observed` includes references to the effects actually observed; it does not imply every possible side effect is known. Cancellation and error results can still carry observed or unknown effects.

### Error layers

| Layer | Example | Interpretation |
| --- | --- | --- |
| `validation` | Invalid `arguments.url` | Correct the request; nothing was dispatched. |
| `precondition` | Object version has changed | Reobserve and reconsider applicability. |
| `transport` | Connection dropped after sending a request | The effect may need reconciliation. |
| `execution` | Local process could not be started | Invocation failure; distinguish from a process that ran and failed. |
| `adapter` | Tool wrapper cannot decode the returned body | Preserve raw evidence; do not invent the decoded result. |
| `target` | Observed HTTP 404 or operation-defined error exit | An actual target response, retaining its native status. |

`retry_class` is `never`, `safe`, or `reconcile_first`, and is set by the runtime/tool contract. It is not blanket authorization to perform another external effect. A target idempotency key helps only where the target actually honors it.

### Completeness, streams, and artifacts

Each streamed chunk carries a stable `stream_id`, channel, stream-local sequence, byte range, media type, and content or artifact reference. Stream sequence differs from journal event sequence. Terminal records state the final sequence/count or explicitly report incompleteness. Receivers deduplicate IDs and detect missing ranges.

Large text and binary results use typed artifacts with `artifact_id`, media type, byte size, content hash, storage scope, and availability/retention information. A bounded excerpt names its exact range. Media supported by the model is rendered through native media blocks by the provider adapter; unsupported content remains explicitly unsupported with a retrievable source.

The context manifest distinguishes exact content, excerpts, summaries, and deferred ranges. No summary is labeled as a full original result. A missing artifact is an observable failure rather than an empty result.

## 4. Context packet

`ContextPacket` is immutable after creation. Its logical fields are:

| Field | Meaning |
| --- | --- |
| `context_id`, `run_id` | Exact packet and consuming invocation. |
| `instruction_version`, `tool_registry_version` | Governing contracts. |
| `situation_revision`, `through_event_seq` | Operational snapshot and considered event boundary. |
| `memory_generation`, `memory_watermark` | Published memory snapshot and covered input boundary. |
| `blocks` | Typed, labeled context segments. |
| `manifest` | Source IDs, representations, included ranges, and pending ranges. |
| `budget` | Model/provider limit, tokenizer identity, estimated/actual input, reserved output, and exclusions. |

Every block records its kind, source, scope, timestamps, origin, and payload representation. Memory blocks additionally retain revision, validity, evidence links, and applicability. Original provider roles and causal message ordering survive serialization.

Input records remain pending while a request fails or the relevant content is deferred. A response from the provider establishes that a request was processed, not that the model correctly used every supplied fact. A separate responsibility status tracks whether a user request was addressed.

## 5. Memory records and C++ IPC

### Canonical memory revision

| Field group | Required content |
| --- | --- |
| Identity | `memory_id`, `revision`, `kind`, `scope_id`, stable `concept_ids`. |
| Content | Typed content, content hash, observed/reported/inferred origin. |
| Evidence | Source event IDs and exact source memory revisions. |
| Time | Event validity interval where known, recorded time, consolidation time. |
| Lifecycle | Candidate, active, contested, superseded, retracted, or expired. |
| Revision links | Explicit supersedes, retraction, derivation, and entity merge/split references. |
| Procedure extras | Preconditions, compatible tool versions, expected effects, failure detectors, validation evidence. |

Operational `ActionAttempt`, `Expectation`, and `Commitment` records remain separate from derived memory revisions. Concept labels and embeddings can change without changing their stable concept identity. Concept/entity merges are recorded and reversible.

### Embedding projection

Each vector binds `memory_id + revision + content_hash` to an `embedding_model_id`, model revision, dimension, and generation. Index publication requires alignment with the record revisions it claims to cover. Deleting or retracting a record suppresses it from retrieval even if an older index file still contains its vector.

### IPC envelope

Requests use `protocol_version`, `request_id`, `operation`, authenticated `scope`, a deadline, and typed arguments. Responses repeat the request ID and return `status`, typed data, generation/watermarks, completeness, and a structured error. Status is `ok`, `partial`, `unavailable`, `conflict`, or `invalid_request`.

Length-prefixed framing, maximum frame sizes, request multiplexing, per-request cancellation, and connection-level limits are part of the transport contract. Large transfers use bounded batches. Cancellation of a query releases its work where possible; cancellation of a publish request does not establish that publication failed. Reconnect and query the generation/operation ID to reconcile it.

Durable ingestion acknowledgment and searchable index visibility are separate fields. An ingestion watermark advances only through a contiguous handled source range; gaps are reported. Idempotent replay checks IDs and hashes, rather than applying the same revision twice.

### Generation publication

A staged generation contains its parent, source watermark, record revision set, index versions, validation report, and publication ID. Publication is a short compare-and-swap transaction after all required artifacts are durable. A mismatch returns `conflict`; the coordinator rebases or abandons staging. The engine recovers committed publication after a restart even if the caller did not receive its acknowledgment.

Old generations remain available under reader leases. Retractions are checked against a current exclusion registry at retrieval. A retraction received during an active inference triggers the scheduler's critical-change policy when it affects a pending action; an immutable old prompt cannot be silently rewritten.

## 6. Output delivery contract

User-visible model output uses `message_id`, `run_id`, stream-local `sequence`, content blocks, and a terminal status of completed, interrupted, failed, or superseded. The outbox persists these records before delivery.

Receivers acknowledge the highest contiguous sequence. Reconnect resumes after that cursor. Terminal records cannot skip missing chunks or imply the user read the content. Adapters without acknowledgments expose `sent_unconfirmed` rather than claiming acknowledged delivery.

Tool directives are routed to execution and represented to the user through lifecycle events. The delivery contract covers user-visible model content, not private provider reasoning. A finalization or memory-maintenance task cannot suppress already-persisted visible output.

## 7. Invariants shared by Python and C++

1. Stable IDs retain meaning across retries, compaction, and restart.
2. No external effect is dispatched before its action intent is durable.
3. No generated memory establishes authoritative external completion.
4. Accepted input is supplied, explicitly pending, or explicitly resolved; a processing watermark alone cannot erase it.
5. Published records and retrieval projections belong to a compatible generation.
6. Memory gaps, partial observations, unknown effects, and unconfirmed delivery remain explicit.
7. Sleep and index rebuilding cannot hold a foreground journal transaction.
8. Runtime metadata and external content cannot overwrite each other's authority.
9. Event deduplication does not imply universal exactly-once external execution.
10. Failures preserve enough state for reconciliation instead of silently manufacturing success.
