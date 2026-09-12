# Evidence-based source generations

`SelfEvolution` stages source edits, asks `MetaHarness` to run fixed independent
checks, and selects a verified immutable source generation for subsequent
execution. It does not modify imported Python modules in the running process.
The execution owner reads `active(scope).value["snapshot"]` and starts fresh
execution from that exact snapshot. The tests demonstrate this boundary with
real confined Python processes before activation, after activation, and after
rollback.

Construction requires a shared `Journal`, a staging root, and a `MetaHarness`.
Omitting `authority` grants no editing or activation rights. Installation code
may supply `EvolutionAuthority(source_root, scopes=(), allowed_paths=(),
protected_paths=(), allow_activation=False, auto_rollback=True,
state_schema="1")`. Source, staging, and evaluator roots must be separate.
Authority is immutable and cannot be supplied by candidate code.

The public sequence is:

1. `stage(scope, edits, rationale=..., evidence_event_ids=...,
   expected_parent=None, state_schema=None)` captures the current source and
   applies bounded `SourceEdit(path, content, expected_sha256=None)` objects.
   Existing files require their exact preimage hash; `None` content deletes a
   file. New files require an absent preimage. Permitted and protected paths
   use path components, and all changes remain trace-linked hypotheses.
2. Await `evaluate(scope, candidate_id, phase=..., work_id=..., generation=...,
   cancel=None)` for `build`, then `shadow`, then `canary`. Each evaluation uses
   the canonical dispatcher and its mandatory scheduler authorization callback.
   Fixed exit/output expectations are compared by the host against real
   confined subprocess output. Narrative claims from a candidate confer no
   verification status. A passing verdict also requires its exact canonical
   completed/success execution receipt.
3. `activate(scope, candidate_id, expected_generation=...)` requires explicit
   installation activation authority, all three passing evaluations for the
   exact source/check policy, and the current parent generation. It durably
   copies source and changes the selected-generation pointer with a Journal
   compare-and-swap. Repeating the same completed activation is idempotent.
4. `observe(scope, generation_id, evidence_event_ids)` admits only runtime
   `behavior.observed` events with the exact generation and `healthy` or
   `regression` status. Configured regression handling calls rollback.
   `rollback(scope, expected_generation=..., reason=...,
   evidence_event_ids=())` restores the predecessor. `restore_baseline(scope,
   expected_generation=..., reason=...)` restores the installation baseline.

Rollback changes subsequent source selection; it preserves prior events,
evaluation receipts, source generations, and external effects. This backend
requires the same persistent-state schema across generations and rejects state
migrations explicitly. It does not claim to reverse an external side effect.

Call `recover(scope)` during exclusive restart recovery after execution is
stopped. Interrupted evaluations retain evidence, but missing terminal receipts
do not count as passing phases. Recovery restores the last contiguous passing
phase so evaluation can be retried under a new canonical attempt.

`MetaHarness(journal, root, dispatcher, checks=...,
allowed_programs=(sys.executable,))` requires nonempty uniquely named immutable
`CheckSpec` objects. Their command, input, expected exit/output, and resource
bounds form the governing policy hash. Its registered control tool requires
`evolution.evaluate`; there is no permissive authorization default. Standalone
`prepare`, async `run`/`evaluate`, `get_evaluation`, `verified`, and `recover`
methods support composition. `diagnose` records bounded hypotheses with scoped
evidence, expected observations, and falsifiers; diagnosis is never proof.

The isolation backend and operating-system prerequisites are documented in
[`tool_runner/README.md`](../tool_runner/README.md). Candidate source is copied
into disposable check workspaces. Candidate processes have no evaluator root,
Journal, staging store, or network access. Build artifacts produced by a check
are disposable; the promoted artifact is the exact staged source tree.
