---
title: "Review proposals and set attention policy"
description: "Decide when proactive observations become suggestions or bounded work, with quiet hours and explicit budgets."
---

Initiative records a concrete proposal linked to an actual source event. Attention policy decides whether that proposal should be remembered, surfaced, deferred, or admitted as bounded work. A high score does not itself execute a tool or accept a responsibility.

## Review proposals in the web interface

Open **Work → Inbox**. Expand **Inspect evidence** on a proposal before choosing **Accept proposal** or **Dismiss proposal**. Accepting a usable, unexpired prompt creates scheduled work in a conversation. The current UI supplies an allowance of 64,000 tokens, eight iterations, 120,000 milliseconds, and eight tool calls. Use the CLI/API when you need a different explicit allocation or destination.

The interface has proposal decisions, not a complete attention-policy editor or a freeform proposal-authoring form. `initiative.propose` and `attention.policy` provide those advanced operations.

## Worked example: suggestions with quiet hours

Save `attention.json`:

```json
{"expected_revision":0,"policy":{"mode":"suggest","quiet_hours":{"timezone":"Europe/Berlin","start_minute":1320,"end_minute":480},"notification_limit":10,"minimum_score":0.6,"duplicate_seconds":3600}}
```

```sh
sturgis command AGENT_ID attention.policy --params-file attention.json
sturgis command AGENT_ID inbox.list
```

The initial revision is zero only when no policy exists. Keep the returned revision for later updates. The example defines 22:00–08:00 quiet hours and leaves admission subject to an owner decision. Quiet hours and duplicate suppression affect policy decisions; this is not a claim of a bundled email, SMS, or mobile push-delivery service.

To accept a pending proposal with a smaller allowance, save `accept.json`:

```json
{"id":"PROPOSAL_ID","decision":"accept","conversation_id":"CONVERSATION_ID","resources":{"tokens":16000,"iterations":2,"elapsed_ms":60000,"tool_calls":2}}
```

```sh
sturgis command AGENT_ID inbox.decide --params-file accept.json
```

Use `decision:"dismiss"` without execution resources to dismiss it.

## Advanced proposal and policy fields

`initiative.propose` requires `source_event_id`, a concrete string/action object, `expires_at`, `dedupe_key`, and six finite signals from 0 to 1: urgency, expected_value, confidence, interruption_cost, resource_cost, and duplication_penalty. The source event must already exist in this agent; fabricated event IDs fail. A channel is optional.

Policy modes are `disabled`, `remember`, `suggest`, and `bounded`. Bounded mode additionally requires a valid conversation and positive resource allocation. Limits include a notification count from 0–1,000, minimum score from 0–1, and duplicate window up to 30 days. Quiet hours use a real IANA timezone and minute-of-day values.

## Recovery

Expired candidates cannot be accepted as fresh work. Already-decided proposals cannot receive a second conflicting decision. Inspect the existing inbox record after an uncertain acknowledgement. If a policy revision conflicts, use the revision from your latest saved policy response rather than resetting to zero. Foreground workload can defer proactive admission; [Work](overview.md) and [diagnostics](../settings/usage-diagnostics.md) show the surrounding execution state.

## Command examples

These are command-specific examples, not a script to run every operation. Replace uppercase IDs, example endpoints, timestamps, and revision numbers with the actual intended values. Save one JSON object as `params.json`, then run `sturgis command AGENT_ID METHOD --params-file params.json`. Omit the file for `{}`. If acknowledgement is unknown, keep request and domain operation identities unchanged. For an acknowledged unavailable result, follow the feature-specific recovery instructions.

| Method | Example parameters or private-prompt command |
| --- | --- |
| `attention.policy` | `{"policy":{"mode":"suggest","minimum_score":0.6},"expected_revision":0}` |
| `inbox.decide` | `{"id":"PROPOSAL_ID","decision":"dismiss"}` |
| `inbox.list` | `{}` |
| `initiative.propose` | `{"source_event_id":"ACTUAL_SOURCE_EVENT_ID","action":"Review the corrected source","signals":{"urgency":0.5,"expected_value":0.8,"confidence":0.8,"interruption_cost":0.2,"resource_cost":0.2,"duplication_penalty":0},"expires_at":1893456000,"dedupe_key":"review_corrected_source_1"}` |
