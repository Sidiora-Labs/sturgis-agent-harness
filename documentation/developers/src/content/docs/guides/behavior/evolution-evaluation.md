---
title: "Governed source evolution and independent evaluation"
description: "Stage immutable source, verify it outside candidate control, and activate explicitly."
---

Self-evolution is an opt-in library mechanism. Default runtime composition grants no editing or activation authority. Installation code must supply an immutable `EvolutionAuthority` with a source root, permitted scopes and paths, protected paths, state schema and activation policy.

A source proposal uses exact bytes and an existing-file preimage:

```python
from hashlib import sha256
from pathlib import Path
from agent.self_evolution import SourceEdit

original = Path(source_root, "behavior.py").read_bytes()
candidate = evolution.stage(
    scope,
    [SourceEdit("behavior.py", replacement_bytes, sha256(original).hexdigest())],
    rationale="Handle the observed failure without changing the state schema.",
    evidence_event_ids=[observed_failure_event_id],
)
```

The replacement and event must come from the actual authorized change. New files require an absent preimage; `None` content deletes a permitted file. Source, staging and evaluator roots are separate.

## Evaluation cannot be self-certified

Before proposing a patch, `MetaHarness.diagnose(scope, evidence_event_ids, hypothesis=..., expected_observation=..., falsifier=...)` can record a bounded explanation of an observed failure. The expected observation and falsifier make that explanation reviewable. A diagnosis is a hypothesis linked to scoped evidence; it neither verifies its own cause nor creates editing, execution or activation rights.

MetaHarness runs fixed, uniquely named CheckSpec commands with immutable expectations and resource bounds. Its protected policy hash covers those definitions. Candidate code executes in confined disposable workspaces through the canonical dispatcher and scheduler authorization.

Run build, shadow and canary in order. Passing requires observed exit/output expectations and the exact completed/success canonical execution receipt. A candidate's written claim that its tests passed is not a verdict.

Activation requires explicit installation authority, all three passing phases for the exact source/check policy, and a current expected parent. It copies the exact staged source into an immutable generation and atomically changes the selected pointer. Build products in disposable check workspaces are not the promoted artifact.

## Rollback and limits

Trusted runtime `behavior.observed` events can report healthy or regression for an exact generation. Configured regression handling rolls back to the predecessor; explicit baseline restoration is also available. Selection affects fresh execution, not imported modules already running in Python.

Persistent behavior-state migrations are rejected by this backend. Rollback preserves receipts, generations and external effects; it cannot undo a remote operation. After exclusive restart, recover interrupted phases and require actual terminal evidence before continuing. See [tool effects](../integrations/tool-effects.md) and [release boundaries](../operations/release-migration.md).

## Implementation sources

- [agent/self_evolution/__init__.py](/reference/files/agent/self_evolution/__init__.py/)
- [agent/meta_harness/__init__.py](/reference/files/agent/meta_harness/__init__.py/)
- [agent/runtime/__init__.py](/reference/files/agent/runtime/__init__.py/)

[Developer manual](../index.md)
