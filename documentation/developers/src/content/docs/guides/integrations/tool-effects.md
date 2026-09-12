---
title: "Errors, idempotence and effect reconciliation"
description: "Represent target failures and unknown world effects without unsafe replay."
---

A request identity binds the canonical call, work and generation. Reusing it with identical data returns its existing result; changed reuse conflicts. The identity must be stable before dispatch, not generated again after a timeout.

Distinguish validation rejection, a completed target error, confirmed cancellation and an indeterminate effect. A remote HTTP failure after transmission may leave the world outcome unknown. A tool timing out does not justify reporting zero consumption.

## Exact reconciliation payload

A trusted backend observer can reconcile an indeterminate attempt after checking the real external system:

```python
observation = journal.append(scope, "tool.observation", {
    "schema_version": "tool-observation/1",
    "request_id": request_id,
    "work_id": recorded_request.value["work_id"],
    "request_event_id": original_request_event.id,
    "result": observed_terminal_result.as_dict(),
    "usage": measured_resources,
}, source="tool_observer")

admitted = dispatcher.reconcile(
    scope, request_id, observed_terminal_result,
    evidence_event_ids=[observation.id], usage=measured_resources)
```

All referenced values must come from the recorded original request and an actual external observation. The proposed result initially has an empty evidence-ID list. Reconciliation attaches the admitted observation IDs after validating the causal join.

The entire canonical result is attested: action/attempt/tool, lifecycle/outcome, data, error, completeness and effect certainty. The original tool request must match scope, work and invocation arguments. An unrelated success event or generated narrative does not suffice.

## Usage after uncertainty

Known terminal outcomes emit deduplicated `tool.usage` in the result transaction. If measured local usage exists, reconciliation reuses it and the observer must attest matching counts. A crash without measurements does not invent zero usage. The observer may supply actual measurements; otherwise `usage: None` leaves the reservation unsettled even if terminal evidence is sufficient.

`tool_observer` is an explicitly trusted in-process source, not a cryptographic principal. Do not publish a generic append-event tool that allows model data to impersonate it.

For consumers, human approval authorizes a future exact call; it does not rewrite a rejected old request into success. See [permissions](../consumer/ownership-auth.md) and [cancellation](../core/cancellation-recovery.md).

## Implementation sources

- [agent/tool_runner/__init__.py](/reference/files/agent/tool_runner/__init__.py/)
- [agent/protocol/__init__.py](/reference/files/agent/protocol/__init__.py/)
- [agent/scheduler/__init__.py](/reference/files/agent/scheduler/__init__.py/)
- [tests/integration/test_evidence_admission.py](/reference/files/tests/integration/test_evidence_admission.py/)

[Developer manual](../index.md)

