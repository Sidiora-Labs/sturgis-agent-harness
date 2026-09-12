# Agent interface contracts — v0.2

Companion to the [architecture schematic](agent-architecture-v0.2.md). Date: **12 September 2026**. These are proposed language-neutral contracts for a Python agent and a C++ memory engine. Examples are synthetic illustrations, not execution evidence. Executable schemas and implementations have not been created.

The [complete module map](agent-module-map-v0.2.md) identifies the single owner of each record below. `protocol` defines the shared shapes; the owning domain validates transitions through the operational transaction service. ACP retains its external wire format.

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
    "work_id": "work_8",
    "delegation_id": null,
    "conversation_id": "conversation_1",
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

Child work carries an authenticated `scope_id`, `agent_instance_id` and delegation lineage. For one root entity, its child events share the operational entity journal; child scope and provenance remain distinct. A child cannot pick an event scope from model text. Cross-entity collaboration would require a separate explicitly authorized exchange contract.

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

The same fields invoke `terminal.execute`, a file operation, a memory read exposed as a tool, or `subagents.delegate`, `skills.start`, or another registered capability. These control tools use the same envelope even when they create admitted internal work rather than immediately touching an external target. Tool-specific `arguments` validate against the selected registry entry. Tools may publish richer expectation schemas for machine-checkable assertions; free-text expectations remain claims rather than executable proof.

### Runtime request fields

| Field | Meaning |
| --- | --- |
| `action_id` | Logical intended action, retained across explicitly authorized retries. |
| `attempt_id` | One execution attempt of that action. |
| `request_id` | One dispatch submission; replaying an accepted submission reuses it. |
| `run_id`, `work_id`, `authority_generation` | Admitted inference/work lineage permitted to initiate this action. |
| `scope_id`, `agent_instance_id`, `delegation_ancestry` | Runtime-bound responsibility scope and complete ancestor grant/revision references. |
| `behavior_generation`, `capability_snapshot_id` | Pinned behavior, tool/plugin versions and compatible capability registrations. |
| `preconditions` | Current object versions, required capabilities, and operation-specific conditions. |
| `deadline_at` | Wall-clock persistence of the budget; local execution uses monotonic elapsed time. |
| `idempotency` | Declared target support and key, if available. |
| `limits` | Runtime-applied output, concurrency, duration, and resource bounds. |
| `expectation_id` | Reference to the recorded pre-action expectation. |

Tool metadata specifies the argument schema, result payload schema, side-effect class, accepted target statuses, streams, cancellation semantics, and reconciliation operation if one exists. Pure/read operations and side-effecting operations can have different retry policies while retaining the same calling structure.

### Validation and dispatch

1. Buffer an incomplete provider tool call until its arguments are complete.
2. Parse strictly; reject duplicate JSON keys, invalid types, unknown tool versions, and unsupported fields under the chosen schema.
3. Validate admitted work, scope, current run authority, delegation ancestry, available allocation, and tool preconditions. Check object versions again at execution when the target can support conditional operations.
4. Persist the expectation, action, attempt, and dispatch record in one operational transaction.
5. Atomically claim the recorded attempt only if its `authority_generation` and every ancestor grant are still current, the required scope still applies, and its allocation permits execution. Reserve the applicable resource allocation in the same authority transaction. Otherwise persist cancellation/rejection without target dispatch. A lost wake-up signal is repaired by scanning ready records.
6. Execute the claimed attempt. The atomic claim is the dispatch-authorization boundary: later steering cannot guarantee that an already-authorized external operation will be prevented. Apply target-side conditional writes or fencing where supported, and retain cancellation/reconciliation for effects already in flight.

`scheduler` owns admission, authority generations and allocations; `tool-runner` owns the action/attempt lifecycle and consumes those grants through a shared atomic claim operation. Updating a grant and checking it must not be separate non-atomic read/write steps. `subagents.delegate` routes an admitted, recorded control operation into the delegation owner; a child subsequently uses the same tool-runner path.

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
| `instruction_version`, `tool_registry_version`, `behavior_generation` | Governing contracts and pinned behavior. |
| `attention_decision_id`, `profile_revision`, `plan_revision` | Selection decision and relevant domain snapshots. |
| `capability_snapshot_id`, `skill_revisions`, `provider_descriptor_revision` | Pinned extension/procedure versions and target provider capabilities. |
| `scope_id`, `agent_instance_id`, `work_id` | Authorized recipient and admitted work identity. |
| `situation_revision`, `through_event_seq` | Operational snapshot and considered event boundary. |
| `memory_generation`, `memory_watermark` | Published memory snapshot and covered input boundary. |
| `blocks` | Typed, labeled context segments. |
| `manifest` | Source IDs, representations, included ranges, and pending ranges. |
| `budget` | Model/provider limit, tokenizer identity, estimated/actual input, reserved output, and exclusions. |

Every block records its kind, source, scope, timestamps, origin, and payload representation. Memory blocks additionally retain revision, validity, evidence links, and applicability. Original provider roles and causal message ordering survive serialization.

Input records remain pending while a request fails or the relevant content is deferred. A response from the provider establishes that a request was processed, not that the model correctly used every supplied fact. A separate responsibility status tracks whether a user request was addressed.

The supporting `context` compiler owns ContextPacket construction. `framing` owns only bounded transport bytes. Its `FrameCodec` contract specifies length/header encoding, maximum bytes, buffered partial frames, complete-frame emission, clean EOF versus truncation and oversize/error behavior. Native-memory IPC uses its length-prefixed codec; ACP stdio retains the ACP newline format. No transport codec selects, summarizes or relabels model evidence.

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

Receivers with a negotiated receipt capability acknowledge the highest contiguous sequence. Reconnect resumes after that cursor. Terminal records cannot skip missing chunks or imply the user read the content. Adapters without acknowledgments expose `sent_unconfirmed` rather than claiming acknowledged delivery.

Tool directives are routed to execution and represented to the user through lifecycle events. The delivery contract covers user-visible model content, not private provider reasoning. A finalization or memory-maintenance task cannot suppress already-persisted visible output.

## 7. Client, channel, and provider records

### Channel binding and output routing

`ChannelBinding` is owned by `channel-core`: `binding_id`, revision, entity/conversation scope, authenticated participant references, adapter ID/version, external session ID, connection generation, reply target, negotiated capabilities, and last capability observation. Reconnection can replace a connection without replacing the conversation or entity. Session IDs alone are not authentication credentials.

`InboundSubmission` includes adapter identity, connection/session correlation, producer delivery ID where available, exact content/artifact references, source timestamps and transport capabilities. Admission returns `accepted(event_id)` only after durable commit. A request without a stable producer retry key cannot be safely deduplicated by matching its text; its ambiguous retries retain that limitation.

`OutputIntent` identifies entity/conversation, originating run/work, recipient scope, reply binding, stream/message IDs, audience, content type and terminal relationship. `channel-core` resolves a compatible route. `delivery` owns its durable outbox record, monotonically ordered stream sequence, send attempts, receipt/replay cursor and status. An unresolved destination remains a pending delivery record with a reason.

Visible content is persisted before capability-dependent rendering. If a channel cannot display a modality, its declared fallback can supply a faithful representation or an accessible artifact reference plus an explicit limitation. Unsupported media must not become empty success. Standard channel adapters and `acp` both obey these ownership rules.

### ACP boundary

`AcpPromptTurn`, owned by `acp`, stores the ACP connection/session and JSON-RPC request correlation, internal accepted input ID, attached work IDs, visible stream IDs, cancellation event, update-order cursor and terminal stop reason. The exact external fields remain ACP fields; these internal names are proposed runtime records.

| External operation | Internal contract |
| --- | --- |
| Initialize/session setup | Negotiate the actual version/capabilities, then bind through channel-core. |
| Prompt request | Persist canonical input and prompt-turn correlation; no early terminal response merely because input was accepted. |
| Session update | Serialize supported persisted content/status using the negotiated ACP schema. |
| Cancel notification | Persist scoped cancellation, revoke pending prompt-attached work and propagate to tools/children. |
| Prompt response | Emit the applicable stop reason after the required ordered updates; unrelated persistent responsibilities retain their explicit status. |

ACP capabilities determine which optional operations can be exposed, including client-side filesystem/terminal facilities. `provider-adapters` and the internal C++ IPC protocol remain independent of this client protocol. [ACP initialization](https://agentclientprotocol.com/protocol/v1/initialization)

The adapter must specify a tested mapping from canonical tool lifecycles to the selected ACP tool-update schema. Canonical `completed` with `target_error` is not rendered as successful work. If an internal state such as indeterminate has no exact wire state, preserve its explanation in supported content/status and retain its precise internal state; never invent a standard ACP enum. Prompt cancellation does not prove an external effect was undone. [ACP tool calls](https://agentclientprotocol.com/protocol/v1/tool-calls), [ACP prompt lifecycle](https://agentclientprotocol.com/protocol/v1/prompt-turn)

Baseline ACP update notifications provide no receiver receipt. Their outbox state remains `sent_unconfirmed` after a successful write. A lost connection can leave a prompt response's reception unknown; a session load cannot establish reception of its individual prior updates. Version/capability-specific behavior must be validated before adding remote transport, replay or receipt promises. See the [ACP module schematic](agent-module-map-v0.2.md#3-acp-integration-without-losing-the-persistent-entity).

### Provider descriptor and invocation

`provider-catalog` separates static `ProviderSpec` records (identity, transport, authentication mode, endpoint/defaults and display metadata) from refreshed model/capability snapshots. The latter are a deliberate extension of the reference catalog. `ProviderDescriptor` includes provider/model identifier, descriptor revision, adapter version, supported modalities, tool/structured-output/streaming/cancellation features, context/output limits, tokenizer reference, configured routing constraints, availability, observation time and credential reference. Credentials themselves do not enter model context or memory.

Supporting connection/authentication services resolve credential references under profile scope. Dynamic model availability never changes the static authentication contract.

`ModelInvocation`, owned by `provider-adapters`, includes invocation/run/work IDs, immutable context ID, descriptor revision, requested features, explicit generation parameters, budget/limits, cancellation generation, provider request ID if returned, stream cursors and terminal status. The scheduler selects or authorizes a compatible candidate under routing policy; adapters report actual acceptance, usage and failures.

Canonical provider events distinguish visible content delta, complete tool-call proposal, usage, terminal status and error. Private provider reasoning is not user-visible content. Provider-specific response items retain enough linkage for subsequent valid tool-call/result serialization. A fallback is a new recorded invocation with its own compatible packet; an adapter may not silently drop roles, media, required tool semantics or accepted input to make a request fit.

## 8. Situation, goals, commitments, and admitted work

Every versioned domain record includes its stable ID, entity/scope, revision, creating causal event, provenance, timestamps and applicable evidence references. State changes check an expected revision and append the corresponding transition event atomically. Generated interpretations are tagged as such.

| Record and owner | Domain fields and transition rules |
| --- | --- |
| `ProfileSnapshot` — profile | Stable profile ID, canonical data/workspace roots, configuration/resource snapshots, explicit identity/preferences, revision and authority references. Configuration changes produce revisions; inferred preferences do not silently become explicit ones. |
| `WorldObject` projection — awareness | Stable internal ID, external namespace/ID, observed version, properties, relationships, available capability references, freshness and uncertainty. A renamed object retains identity only with evidence; vector similarity cannot establish sameness. |
| `SituationSnapshot` — awareness | Watcher/source registrations, ingestion cursors and normalized observation ledger references; Through-event cursor, canonical goal/commitment/action references, current observations, runtime/self capability and health state, expectation assessments, unresolved contradictions and snapshot revision. It projects domain state without owning all its transitions. |
| `Goal` — goals | Outcome, rationale, priority, scope, parent goal, success criteria, token/time/iteration budgets, usage, continuation key and evidence; proposed → active ↔ paused → achieved/abandoned. Reopening requires an explicit revision with a reason. |
| `Commitment` — commitments | Accepted responsibility, beneficiary, goal/input linkage, due/trigger conditions, satisfaction criteria, responsible agent/delegation and evidence. Proposed → accepted → fulfilled/cancelled/failed; progress can be pending/in_progress/blocked. Overdue is computed from unresolved responsibility plus time, not a terminal state that removes it. |
| `Plan` — planner | Routing/classification decision, goal/commitment revisions, agent/user/child assignments, steps/dependencies, result checks, plan/step budget references, preconditions, capability/skill versions, expectations, alternatives and readiness. Candidate → active ↔ paused → completed/superseded/abandoned, with blocked steps retained explicitly. A completed plan is not proof its goal was achieved. |
| `AttentionDecision` — attention | Discriminated union of proactive policy decisions and admitted context-selection decisions, defined below. Their fields and timing differ. |
| `InitiativeTrigger` — initiative | Source change/timer, motivating goal/commitment, permitted scope, relevance rationale, duplicate key/cooldown and proposal disposition. Trigger detection does not itself dispatch work. |

`ProactiveAttentionDecision` carries candidate/source ID, expected value/confidence/urgency, interruption/resource/duplication signals, expiry, policy/profile revision, workload/quiet-hour/notification-budget checks, reason and disposition. Dispositions include ignore, remember, batch, schedule, suggest/ask, bounded work and notify. Each actionable disposition becomes ordinary admitted work or a durable timer; even a notification uses delivery. `ContextAttentionDecision` carries admitted work, situation revision, pinned provider limit, required/selected sources, exact ranges, focus, allocations and exclusions/deferrals. The context compiler consumes this second variant. It must not require a provider budget before proactive work is admitted.

Awareness separates observed action results from expected consequences. An `ExpectationAssessment` links the recorded expectation, relevant object versions, evidence, assessor and method. Its status is pending/satisfied/violated/unknown, with confidence only when meaningful. A model assessment cannot fabricate an authoritative target receipt. Goals and commitments apply their own satisfaction criteria to these assessments.

### Work proposal, admission, and cancellation

`WorkProposal` includes proposal ID/deduplication key, source owner, goal/commitment/plan/input references, requested kind, required capabilities, dependencies, urgency/deadline, estimated budget, target scope, lifetime and acceptance criteria. `initiative`, `planner`, `subagents`, sleep and `self-evolution` submit proposals. Accepted user inputs reach scheduler admission directly.

`goals` owns cumulative goal budgets/usage, including applicable token/time/turn/child/retry/process/storage/cost limits. Scheduler allocations reference and atomically reserve against that ledger and ancestor ceilings. Settling an attempt uses a deduplicated usage event; lease recovery cannot create a fresh independent goal budget.

`WorkItem`, owned by `scheduler`, records its proposal/input references, state, allocation, scope, resource claims, lease owner/expiry, authority generation, delegation ancestors, behavior/capability versions, dependencies, progress cursor and cancellation cause. Admission is a durable decision: queued → admitted → running → waiting/completed/failed/cancelled; waiting work can become runnable again. Deferral records preserve why work has not been admitted. Lease expiry removes new dispatch eligibility and starts recovery; it does not prove an external action stopped.

The schedule registry supports interval/calendar/cron rules, timezone/DST resolution, pause/resume, missed-run policy and bounded catch-up. One timer service records `timer_id`, owner, due instant, timezone/cycle key where relevant, payload/reference, generation and disposition. It serves deadlines, plan/initiative wakeups, leases, daily sleep and evolution monitoring. Notifications may repeat; durable trigger IDs and domain transitions deduplicate them. Required projector and ingress/egress service loops are supervised infrastructure, while expensive jobs require scheduler allocation.

`CancellationRequest` names the authenticated requester/scope, target prompt/work/delegation, ancestor relationship, policy and causal event. The scheduler durably revokes applicable new dispatch eligibility before reporting cancellation as admitted. Distinct work states track requested cancellation, confirmed execution cancellation and remaining indeterminate effects. Reassigning a responsibility requires a new recorded grant; it must not resurrect the old generation.

`WaitSubscription` records owner/responsibility, typed trigger, source/cursor, expected event or predicate, deadline, revision and armed/fired/cancelled/dispatched disposition. Waiting for child/process completion, file changes, channel messages, external conditions or time must atomically arm the wake and transition work to waiting before releasing compute. Recovery checks both source cursors and pending subscriptions; repeated notifications cannot create duplicate continuation actions. Fulfillment/cancellation explicitly retires obsolete subscriptions.

An authorization transaction prevents a new claim racing past a committed revocation in the same operational authority store. It cannot recall a previously authorized remote operation. Target-side version checks and fencing provide stronger protection where actually supported.

## 9. Extension, skill, and delegation records

### Plugin declarations and publication

`PluginManifest` follows `plugin-sdk` schemas: package ID/version/hash, publisher/source provenance, SDK/protocol compatibility, exports with versioned schemas, dependencies, requested scopes, resource needs, configuration fields, activation/health/migration/deactivation hooks, commands as well as tools, cancellation/streaming and state-migration contracts. Required scopes are declarations to validate against configured authority.

`PluginInstance` is owned by `plugin-host`: manifest hash, isolated worker identity, lifecycle state, actual grants, resource limits, health, registrations, drain cursor, mutable-state schema version, migration lineage, compatible rollback target, quarantine and uninstall disposition. `CapabilitySnapshot` points to active tool/channel/provider/skill versions and their hosts. Publication validates compatibility and required dependencies before making new work eligible.

Host startup, health and drain operations are infrastructure; agent-directed tool effects remain recorded tool-runner attempts. A plugin callback does not gain an unrestricted bypass merely because it runs during a lifecycle hook. Isolation/grants must constrain the actual worker, not rely solely on manifest text.

Accepted work pins its registrations. A draining instance receives no newly admitted attempts; existing work completes, is explicitly cancelled, or becomes indeterminate for reconciliation. Plugin-local mutable state migrates under a staged compatibility contract with explicit recovery; code rollback alone cannot restore incompatible state. A new version cannot reinterpret an old action's arguments/results. Capability withdrawal can revoke future eligibility even when a version remains pinned; pinning is compatibility, not immunity from revocation.

### Skills

`SkillRevision`, owned by `skills`, contains stable skill ID, revision/content hash, procedure/instruction/assets, applicability, preconditions, required grants and tool versions, completion/failure criteria, source memory revision references and validation evidence. Lifecycle is candidate → available → deprecated/withdrawn; activation is explicit and versioned.

Selection yields a pinned skill reference for planner/context compilation. Procedural memory can suggest a candidate, while skill publication validates and owns its runnable package. Scripts execute through tool-runner; multi-step procedures create plan/work records. A skill cannot elevate retrieved instructions above controlling authority or create its own scheduler.

### Subagents and findings

`Delegation`, owned by `subagents`, contains delegation ID, parent work/responsibility, child instance ID, objective, criteria, input/evidence references, bounded scope/capabilities, ancestry grants, shared resource allocation, dependencies, deadline, lifetime/cancellation relationship and declared visible-output route, workspace mode, message/wait references, heartbeat and retention policy. A memory-scout specialization restricts tools to bounded scoped reads and returns source revisions/evidence rather than mutating memories. State is proposed → admitted → running → submitted → resolved, with explicit failed/cancelled states. Submission is distinct from acceptance.

`ChildRuntimeRecord` links the child to a workspace mode (read-only parent, shared parent, isolated copy, or dedicated workspace), scoped root identity, mailbox cursor, wait subscriptions, heartbeat/lease, runtime status, orphan/recovery disposition and retention policy. Shared workspaces require resource coordination and version/preimage checks; isolated copies do not imply their changes were merged into the parent workspace. A memory scout permits only scoped memory reads, with pinned evidence/version selectors and no ordinary tools, filesystem access, mutation or user delivery.

`ChildResult` contains stable submission/revision IDs, delegation ID, findings, exact evidence/artifact references, affected object versions, accomplished criteria, unresolved items, side-effect/action references and confidence/uncertainty. `ResultAdmission` records accepted/partial/rejected, checks performed, reason, admission revision and downstream responsibility references. Shape and evidence checks can be deterministic; any model-assisted review is separately admitted work. A child cannot be the sole authority for its own success claim.

Partial or rejected findings leave an explicit repair/replan disposition. Parent goals and commitments retain their canonical satisfaction transitions. Descendants consume ancestor allocations rather than receiving unbounded independent budgets. Creation and dependency changes reject cycles. Revocation applies at new dispatch claims across ancestry, while old effects remain tracked. Retained children require explicit lifetime records and never survive a prompt cancellation by accidental task orphaning.

## 10. Evolution, harness, and version activation

`HarnessDiagnosis`, owned by `meta-harness`, records bounded trace references, causal-fault classification, falsifiable hypotheses, expected observations, allowed repair surfaces and candidate/evaluation links. General process health/restart belongs to runtime supervision.

`EvolutionCandidate`, owned by `self-evolution`, records candidate ID, base behavior generation, exact diff/artifact hashes, affected modules, motivating evidence, expected benefit, requested change scope, compatibility/migration requirements, evaluation plan, observation criteria and rollback reference. Targets can include permitted procedures, heuristics, lower-authority context templates, extension implementations, and Python/C++ code.

`EvaluationRun`, owned by `meta-harness`, records the scheduler-admitted work ID, pinned candidate/environment/data hashes, fixed governing checks, resource limits, measured outcomes, failures/regressions, artifacts and completion status. Candidate generation cannot rewrite the governing checks or certify itself. Evaluation replay must not resend historical external effects.

Candidate lifecycle is proposed → staged → evaluating → eligible/rejected → activated → observing → retained/rolled_back. An eligible result is still subject to configured activation scope and compatibility; it does not grant new change authority. Runtime supervision manages the publication worker; `self-evolution` owns its candidate and activation transitions. `meta-harness` supplies independent diagnosis and evaluation evidence.

`BehaviorGeneration` references exact compatible profile-policy/template, skill, plugin, provider-adapter and runtime revisions as applicable, plus evaluation/activation evidence. Publication uses an expected-parent check and a durable activation record. New work can select the published generation; existing work retains its admitted versions unless explicitly cancelled and replanned.

Code/harness candidates pass through isolated source/build verification, shadow evaluation, bounded canary allocation, durable promotion and post-promotion watchdog/reversal. Native/code upgrades require isolated artifact verification and a compatible process/state migration protocol in implementation. A schema migration with irreversible effects cannot claim recovery through a pointer rollback alone. Historical work, observations and user output retain their original generations. Rollback changes future behavior and does not undo past real-world effects.

`MemoryGeneration` and `BehaviorGeneration` are independent IDs and publications. Sleep can produce an evolution proposal; memory consolidation itself cannot activate new code or rewrite commitments.

## 11. Invariants shared by Python and C++

1. Stable IDs retain meaning across retries, compaction, and restart.
2. No agent-directed tool effect is dispatched before its action intent is durable. Channel and provider transport operations retain their own admitted operation/output records.
3. No generated memory establishes authoritative external completion.
4. Accepted input is supplied, explicitly pending, or explicitly resolved; a processing watermark alone cannot erase it.
5. Published records and retrieval projections belong to a compatible generation.
6. Memory gaps, partial observations, unknown effects, and unconfirmed delivery remain explicit.
7. Sleep and index rebuilding cannot hold a foreground journal transaction.
8. Runtime metadata and external content cannot overwrite each other's authority.
9. Event deduplication does not imply universal exactly-once external execution.
10. Failures preserve enough state for reconciliation instead of silently manufacturing success.
11. One scheduler owns work admission and shared allocations, including children and background jobs.
12. Each domain has one canonical transition owner; projections, plans and child narratives cannot silently replace it.
13. Extension and behavior activation cannot rewrite already accepted attempts or expand governing authority.
14. External ACP lifecycle and delivery capabilities are mapped explicitly rather than imposed on the persistent entity.
