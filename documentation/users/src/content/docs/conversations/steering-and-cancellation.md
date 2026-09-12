---
title: "Steer, stop, and retry active work"
description: "Change direction without losing input identity, and handle uncertain acknowledgements or effects truthfully."
---

Use steering to supply direction while work is active. Use cancellation when you want it to stop. Both are recorded inputs; neither rewrites history or guarantees that a remote operation already in flight can be undone.

## Use the composer controls

Select the active conversation. In **Message mode**, choose **Steer active work**, write the correction, and send it. The message is labeled as steering in history. Use **Stop** to submit a cancellation request for that conversation. Inspect the resulting work state rather than interpreting the click itself as completed cancellation.

When a message acknowledgement is lost, the composer shows **Retry message**. That retry preserves the same message identity and content. Do not replace its text or attached sources while its acknowledgement is uncertain. Once the original input is resolved, a different intended input can receive a new identity.

## Worked example: narrow a running task

Suppose the agent is reviewing a large document. Send this in steering mode:

> Focus on the maintenance section. Stop expanding the historical background, and state which source paragraphs support the maintenance summary.

Expect a steering entry associated with the same conversation. Already persisted output remains visible. New work may incorporate the direction at the next supported boundary; text already emitted is not erased.

If the task should end entirely, use **Stop**, then inspect **Work → Overview**. In the CLI:

```sh
sturgis stop AGENT_ID CONVERSATION_ID --message-id cancel_maintenance
sturgis events AGENT_ID --conversation CONVERSATION_ID --follow
```

Keep `cancel_maintenance` when retrying that exact cancellation. A generic steering request can be saved as `steer.json`:

```json
{"conversation_id":"CONVERSATION_ID","message_id":"steer_maintenance","text":"Focus only on the maintenance section.","kind":"steer"}
```

```sh
sturgis command AGENT_ID messages.send --params-file steer.json --request-id send_steer_maintenance
```

Replace the conversation ID before use. `messages.cancel` is the equivalent domain command behind the dedicated stop command.

## Interpret the result

Accepted input means the service has recorded the request. Running work means execution was admitted. A persisted output event means content exists durably. A terminal turn describes inference completion, failure, or interruption. These observations do not automatically settle an external side effect.

If a network operation times out after submission, its effects may remain unknown. Do not retry a potentially effectful operation under a new identity simply to make the UI show success. Inspect [evidence](../work/evidence.md), and use the operator's supported reconciliation path when available.

For a stalled runtime, first inspect [diagnostics](../settings/usage-diagnostics.md). **Recover runtime** replaces its process and preserves unresolved outcomes; it is more disruptive than requesting normal cancellation. Restarting the service is not a substitute for checking which work was already accepted.

## Command examples

These are command-specific examples, not a script to run every operation. Replace uppercase IDs, example endpoints, timestamps, and revision numbers with the actual intended values. Save one JSON object as `params.json`, then run `sturgis command AGENT_ID METHOD --params-file params.json`. Omit the file for `{}`. If acknowledgement is unknown, keep request and domain operation identities unchanged. For an acknowledged unavailable result, follow the feature-specific recovery instructions.

| Method | Example parameters or private-prompt command |
| --- | --- |
| `messages.cancel` | `{"conversation_id":"CONVERSATION_ID","message_id":"maintenance_cancel_1"}` |
| `messages.send` | `{"conversation_id":"CONVERSATION_ID","message_id":"maintenance_prompt_1","text":"Summarize the maintenance source.","kind":"prompt"}` |
