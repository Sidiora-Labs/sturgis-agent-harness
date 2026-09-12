---
title: "Understand ongoing work"
description: "Choose the right work record and distinguish tracking, execution, progress, and evidence."
---

**Work** brings execution and longer-lived intentions into one view. Its tabs are Overview, Goals, Commitments, Schedules, Workflows, and Inbox. Use **Refresh work** when you need a fresh observation; automatic refresh is paused with private runtime queries when you suspend the runtime.

## Choose the right feature

| Your need | Use | What creating it means |
| --- | --- | --- |
| Define an outcome and resource ceiling | [Goal](goals.md) | A proposed outcome exists; activation still does not itself schedule a prompt. |
| Accept a responsibility for someone | [Commitment](commitments.md) | A proposal exists until you accept it. |
| Run a prompt later or repeatedly | [Schedule](schedules.md) | A durable future execution opportunity exists. |
| Run dependent steps | [Workflow](workflows.md) | A versioned plan exists; run it to admit its steps. |
| Give a bounded subtask to another worker | [Delegation](delegation.md) | A child must fit its parent's capabilities and resources. |
| Review proactive suggestions | [Inbox](initiative-and-attention.md) | A candidate is recorded, not necessarily accepted work. |

Before requesting execution, configure a ready provider and any required connections and permissions. A plan can be inspected without implying those dependencies are ready.

## Worked example: inspect a stalled summary

After submitting a source-summary prompt, open **Work → Overview**. Find the current work record and expand **Inspect evidence**. Check its identity, state, resource allocation, and any error. Use the conversation history to associate it with the accepted input.

The equivalent read path is:

```sh
sturgis command AGENT_ID work.overview
sturgis command AGENT_ID work.list
```

Save `work.json` with `{"id":"WORK_ID"}` and run:

```sh
sturgis command AGENT_ID work.get --params-file work.json
```

If you intend to cancel that work, save `cancel-work.json` with `{"id":"WORK_ID","reason":"Owner no longer needs this summary"}` and call `work.cancel`. The result identifies affected work. It fences new execution and requests cancellation of in-flight work; it does not certify that remote effects vanished.

## Read state carefully

Queued work has been accepted but is waiting for admission. Running work has a current execution slot. Waiting or blocked state records a dependency, resource, or policy problem. Completed model work can establish that a model turn ended while leaving external outcomes explicitly unasserted.

Resource allowances use tokens, iterations, elapsed milliseconds, and tool calls. The UI calls these Tokens, Model turns, Time in seconds, and Tool calls. They are limits, not a promise that a particular amount will be spent or a currency quote.

If a view fails, use its retry/refresh control and inspect [diagnostics](../settings/usage-diagnostics.md). Do not manually mark dependent work complete to bypass missing evidence. Use [evidence and completion](evidence.md) to decide whether the problem is execution, delivery, or an unverified external result.

## Command examples

These are command-specific examples, not a script to run every operation. Replace uppercase IDs, example endpoints, timestamps, and revision numbers with the actual intended values. Save one JSON object as `params.json`, then run `sturgis command AGENT_ID METHOD --params-file params.json`. Omit the file for `{}`. If acknowledgement is unknown, keep request and domain operation identities unchanged. For an acknowledged unavailable result, follow the feature-specific recovery instructions.

| Method | Example parameters or private-prompt command |
| --- | --- |
| `work.cancel` | `{"id":"WORK_ID","reason":"Owner ended this work"}` |
| `work.get` | `{"id":"WORK_ID"}` |
| `work.list` | `{}` |
| `work.overview` | `{}` |
