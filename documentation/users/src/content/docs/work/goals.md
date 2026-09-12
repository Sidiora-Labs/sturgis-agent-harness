---
title: "Create and manage goals"
description: "Record desired outcomes, activate bounded intentions, revise allowances, and require evidence for achievement."
---

A goal records an outcome you care about, its resource limits, state, and evidence. It is useful for keeping work aligned over time. Creating or activating a goal does not automatically invent tasks or start a model conversation; connect it to scheduled or planned work deliberately.

## Use goals in the web interface

Open **Work → Goals** and choose **New goal** or **Create a goal**. Fill **Desired outcome**, review **Compute allowance**, and save. The new goal is proposed. Choose **Activate goal** when you are ready to make it active.

Active goals offer **Pause goal**. Paused or otherwise resumable goals expose **Resume goal**. **Edit goal** changes the objective or allowance. **Abandon goal** ends the intention and fences future associated execution. **Archive goal** is available for terminal goals; it does not delete their evidence.

## Worked example: prepare a source-based brief

Set the desired outcome to “Prepare a maintenance brief supported by the uploaded observatory notes.” Give it a suitable allowance, such as 64,000 tokens, eight model turns, 120 seconds, and eight tool calls. Activate it, then create a [workflow](workflows.md) or [schedule](schedules.md) associated through the CLI/API if you need its `goal_id` relationship.

CLI creation uses `goal.json`:

```json
{"objective":"Prepare a source-based maintenance brief","limits":{"tokens":64000,"iterations":8,"elapsed_ms":120000,"tool_calls":8}}
```

```sh
sturgis command AGENT_ID goals.create --params-file goal.json
sturgis command AGENT_ID goals.list
```

Save the returned ID and revision in `goal-state.json`:

```json
{"id":"GOAL_ID","state":"active","expected_revision":1}
```

```sh
sturgis command AGENT_ID goals.transition --params-file goal-state.json
```

`goals.edit` accepts `id`, `expected_revision`, and changed `objective`, `limits`, or `criteria`. `goals.archive` accepts the terminal goal's ID and current revision.

## Achievement and recovery

The CLI/API additionally supports explicit outcome criteria, parent goals, and transitions such as waiting, reviewing, blocked, achieved, and failed. These transitions follow the current state graph; they are not arbitrary labels. An `achieved` transition requires actual scoped evidence satisfying explicit checks. The web goal form does not collect those checks, so an empty-criteria goal cannot be certified achieved merely by clicking or supplying a persuasive summary.

Pausing or ending a goal cancels associated nonterminal work and pauses associated active schedules. Lowering limits does not erase already recorded usage or reservations. On a revision conflict, list goals again and review intervening changes. On missing checks or evidence, add an appropriate criterion through `goals.edit` and obtain real supporting observations; see [evidence](evidence.md). Never fabricate event IDs to force a terminal state.

## Command examples

These are command-specific examples, not a script to run every operation. Replace uppercase IDs, example endpoints, timestamps, and revision numbers with the actual intended values. Save one JSON object as `params.json`, then run `sturgis command AGENT_ID METHOD --params-file params.json`. Omit the file for `{}`. If acknowledgement is unknown, keep request and domain operation identities unchanged. For an acknowledged unavailable result, follow the feature-specific recovery instructions.

| Method | Example parameters or private-prompt command |
| --- | --- |
| `goals.archive` | `{"id":"TERMINAL_GOAL_ID","expected_revision":4}` |
| `goals.create` | `{"objective":"Prepare a source-based brief","limits":{"tokens":16000,"iterations":2,"elapsed_ms":60000,"tool_calls":2}}` |
| `goals.edit` | `{"id":"GOAL_ID","expected_revision":2,"objective":"Prepare a corrected source-based brief"}` |
| `goals.list` | `{}` |
| `goals.transition` | `{"id":"GOAL_ID","state":"active","expected_revision":1}` |
