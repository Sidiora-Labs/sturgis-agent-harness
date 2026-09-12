---
title: "Set tool permissions and approve exact actions"
description: "Choose Ask first, Allow, or Deny and inspect single-use approvals tied to the original invocation."
---

Permissions control whether a model action may use a current tool. They are separate from connection grants: a connection must provide the capability, and the action must satisfy its own policy. Describing an action in a prompt does not bypass either layer.

## Set a policy in the interface

Open **Settings → Permissions**. Each tool shows whether it reads data or can change data/external systems, along with required capabilities. Choose **Ask first**, **Allow**, or **Deny** for the current tool version. If the UI says the tool changed, review the new descriptor before deciding again.

Under **Action requests**, expand the request evidence. Inspect its tool, arguments, work identity, workspace binding, and current state. Choose **Approve once** only for that exact invocation, or **Deny**. Approval still requires valid execution grants when the action runs; it does not itself execute the action or prove completion.

## Worked example: ask before a file-changing action

Find the current workspace-write tool in the policy list and set **Ask first**. Ask your tool-capable model to make a specific allowed edit. If the dispatcher records an approval-required invocation, inspect it under Action requests. Check the path and exact proposed content before approving.

The CLI inspection path is:

```sh
sturgis command AGENT_ID permissions.list
sturgis command AGENT_ID permissions.pending
```

Save `request.json` as `{"event_id":"REQUEST_EVENT_ID"}` and call `permissions.request` to inspect one request. Approve it with `approve.json`:

```json
{"event_id":"REQUEST_EVENT_ID","expires_in":300}
```

```sh
sturgis command AGENT_ID permissions.approve --params-file approve.json
```

Use `permissions.deny` with the event ID to deny instead. Do not supply a made-up event: the service requires a trusted rejected tool invocation whose actual error was `approval_required`.

## Policy changes through CLI/API

`permissions.set` accepts `tool`, `mode` (`ask`, `allow`, or `deny`), and `expected_revision`. Obtain the tool name and current policy revision from `permissions.list`; use zero only for an absent policy. This command updates one bound policy, not a universal authority to invoke any tool.

Approvals bind the tool/version, arguments, workspace, constraints, and relevant grants through the invocation fingerprint. Changed arguments or a changed tool do not inherit the old approval. The interface shows stale or expired requests rather than treating them as current decisions.

## Limits and recovery

Approval expiry defaults to 300 seconds and accepts 1–3,600 seconds. Approved action authority is single-use. An old request can become stale when configuration or capability binding changes, and pending requests are time-bounded.

If a request is stale, inspect the new action and obtain a fresh matching request. If you approved but see no completion, inspect [Work evidence](../work/evidence.md); approval is neither automatic retry nor an outcome receipt. A denied or timed-out action cannot be made successful by toggling Allow after the fact. Imported backups do not restore live permissions or pending approvals.

## Command examples

These are command-specific examples, not a script to run every operation. Replace uppercase IDs, example endpoints, timestamps, and revision numbers with the actual intended values. Save one JSON object as `params.json`, then run `sturgis command AGENT_ID METHOD --params-file params.json`. Omit the file for `{}`. If acknowledgement is unknown, keep request and domain operation identities unchanged. For an acknowledged unavailable result, follow the feature-specific recovery instructions.

| Method | Example parameters or private-prompt command |
| --- | --- |
| `permissions.approve` | `{"event_id":"REQUEST_EVENT_ID","expires_in":300}` |
| `permissions.deny` | `{"event_id":"REQUEST_EVENT_ID"}` |
| `permissions.list` | `{}` |
| `permissions.pending` | `{}` |
| `permissions.request` | `{"event_id":"REQUEST_EVENT_ID"}` |
| `permissions.set` | `{"tool":"workspace.write","mode":"ask","expected_revision":0}` |
