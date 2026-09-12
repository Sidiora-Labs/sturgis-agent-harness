---
title: "Track accepted responsibilities"
description: "Propose and accept commitments, record blocked progress, and register durable waits or evidence-based fulfillment."
---

A commitment records a responsibility and who it is for. Its acceptance state is separate from its progress. Use it when “I intend to do this” must become an explicit responsibility that can later be fulfilled, cancelled, or failed.

## Use the web controls

Open **Work → Commitments**, choose **New commitment**, and fill **Responsibility** and **Who is it for?**. Save the proposal, then choose **Accept commitment**. An accepted commitment offers **Mark blocked** or **Mark in progress** while no durable wait is active. **Cancel commitment** releases a proposed or accepted responsibility with a recorded reason.

Expand **Inspect evidence** to see the current revision and supporting records. A blocked progress label does not itself register a wake trigger. Likewise, an accepted commitment does not automatically run a model task.

## Worked example: wait for a source correction

Create “Review the corrected maintenance dates” for beneficiary `owner`, then accept it. If you are waiting for the owner to respond in a conversation, use the CLI/API's durable wait rather than assuming the agent will infer the dependency later.

Save `wait.json`, using the actual accepted commitment revision and conversation ID:

```json
{"id":"COMMITMENT_ID","expected_revision":2,"trigger":{"kind":"user_response","conversation_id":"CONVERSATION_ID"},"after_seq":0}
```

```sh
sturgis command AGENT_ID commitments.wait --params-file wait.json
sturgis command AGENT_ID commitments.list
```

For an existing conversation, use an observed current cursor for `after_seq` when you mean a future response; zero permits matching earlier eligible events. The result includes the registered wait. `commitments.resume` takes the commitment ID and its current revision and only succeeds when the wait's conditions permit resumption.

## Additional CLI/API operations

`commitments.create` accepts description, optional beneficiary, explicit criteria, and a future expiry. `commitments.transition` accepts a permitted state, current revision, and evidence/reason where required. `commitments.progress` accepts `pending`, `in_progress`, or `blocked`; blocked progress needs a reason. `commitments.list` includes overdue status without changing an overdue record into success.

Supported trigger shapes include a time, schedule job, child terminal state, process exit, file/repository change, channel message, external condition, or user response. The trigger requires a matching operational producer; selecting a trigger name does not install a file watcher or remote connector. The web form does not expose a generic trigger editor.

## Expected result and recovery

Expect the commitment to remain accepted while its progress can be pending, in progress, blocked, or waiting. Fulfillment requires explicit checks and actual evidence, as described in [evidence](evidence.md). A narration that the promise is complete is insufficient.

On a revision conflict, reread the commitment. Do not replace a live wait by hand or mark it fulfilled to dismiss an error. Resolve the trigger or cancel the responsibility deliberately. Link execution through [workflows](workflows.md) or [schedules](schedules.md) when the responsibility requires model work.

## Command examples

These are command-specific examples, not a script to run every operation. Replace uppercase IDs, example endpoints, timestamps, and revision numbers with the actual intended values. Save one JSON object as `params.json`, then run `sturgis command AGENT_ID METHOD --params-file params.json`. Omit the file for `{}`. If acknowledgement is unknown, keep request and domain operation identities unchanged. For an acknowledged unavailable result, follow the feature-specific recovery instructions.

| Method | Example parameters or private-prompt command |
| --- | --- |
| `commitments.create` | `{"description":"Review corrected dates","beneficiary":"owner"}` |
| `commitments.list` | `{}` |
| `commitments.progress` | `{"id":"COMMITMENT_ID","progress":"blocked","expected_revision":2,"reason":"Waiting for corrected source"}` |
| `commitments.resume` | `{"id":"COMMITMENT_ID","expected_revision":3}` |
| `commitments.transition` | `{"id":"COMMITMENT_ID","state":"accepted","expected_revision":1}` |
| `commitments.wait` | `{"id":"COMMITMENT_ID","expected_revision":2,"trigger":{"kind":"user_response","conversation_id":"CONVERSATION_ID"},"after_seq":0}` |
