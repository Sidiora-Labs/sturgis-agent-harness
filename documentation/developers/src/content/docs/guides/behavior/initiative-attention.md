---
title: "Initiative and attention"
description: "Score evidence-linked proposals and allocate context without acquiring execution authority."
---

Initiative records a concrete proposed action linked to an existing scoped source event. Its six signals are urgency, expected value, confidence, interruption cost, resource cost and duplication penalty. Every signal is finite and lies in 0–1. Scoring is deterministic; it does not dispatch work.

```python
import time

candidate = core.initiative.propose(
    core.scope, source_event_id, "Review the newly observed failure.",
    signals={"urgency": 0.8, "expected_value": 0.9, "confidence": 0.8,
             "interruption_cost": 0.2, "resource_cost": 0.2,
             "duplication_penalty": 0.0},
    expires_at=time.time() + 3600, dedupe_key="review-failure-42",
)
decision = core.attention.evaluate_initiative(
    core.scope, candidate.id,
    policy={"mode": "suggest", "notification_limit": 10},
    workload="idle",
)
```

The source event must already exist. A duplicate proposal identity binds the same content and expiry; changed reuse conflicts.

## Proactive policy

Modes are disabled, remember, suggest and bounded. Quiet hours use an IANA timezone and minute-of-day boundaries. Notification limits, score thresholds, duplicate windows, expiry and current workload influence the decision.

Possible outcomes include remember, ignore, batch, schedule, notify, ask and start bounded work. Eligibility for bounded work is still not a scheduler grant. The consumer work adapter turns accepted inbox decisions or configured eligible proposals into bounded scheduled work through the existing runtime.

These are in-app attention and work mechanisms. They do not imply a separate mobile push or email delivery service.

## Context attention

The second Attention interface selects context candidates under a budget. Each candidate has an ID, kind and measured cost; required IDs must remain accounted for. Its selection record explains what was selected or deferred. ContextCompiler then enforces role, byte-range and causal-pair rules.

Do not mix the two policies: suppressing a proactive notification should not silently remove the current user's mandatory input, and high context relevance should not authorize a tool call. See [context](../core/context.md), [scheduler](../core/scheduler.md) and [module ownership](../architecture/module-ownership.md).

## Implementation sources

- [agent/initiative/__init__.py](/reference/files/agent/initiative/__init__.py/)
- [agent/attention/__init__.py](/reference/files/agent/attention/__init__.py/)
- [consumer/work.py](/reference/files/consumer/work.py/)
- [agent/context/__init__.py](/reference/files/agent/context/__init__.py/)

[Developer manual](../index.md)

