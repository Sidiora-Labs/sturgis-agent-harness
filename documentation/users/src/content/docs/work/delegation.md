---
title: "Inspect and manage delegated work"
description: "Understand bounded child tasks, parent authority, isolated workspaces, cancellation, and acceptance evidence."
---

Delegation gives a specific subtask to a child runtime with its own identity and recorded result. It is useful when a parent can divide work while retaining responsibility for accepting the result. A child summary is not evidence that an external effect occurred.

## What the web interface exposes

Open **Work → Inbox** and inspect **Delegated work**. Existing children show their objective or kind, state, and evidence. An active child exposes a cancellation control. The web interface does not have a general “create child” form; advanced delegation uses `children.delegate` through the authenticated CLI/API.

Before delegating, there must be an actual parent work record eligible to delegate, with enough resources and capabilities. You cannot turn a random ID, completed parent, or unrelated agent into delegation authority. Child resources and capabilities must fit the parent's remaining authority.

## Worked example: inspect a child before accepting its result

List the children created for your current work:

```sh
sturgis command AGENT_ID children.list
```

Save `child.json` with the returned ID:

```json
{"id":"CHILD_ID"}
```

```sh
sturgis command AGENT_ID children.get --params-file child.json
```

Inspect its state, workspace mode, constraints, and evidence. If the child says a source was checked, inspect the referenced source or observation. Do not treat the statement itself as an operational receipt.

For an eligible live parent, the command shape for creating a narrow child is `delegate.json`:

```json
{"parent_work_id":"LIVE_PARENT_WORK_ID","objective":"Review the provided brief for unsupported dates","capabilities":[],"resources":{"tokens":16000,"iterations":2,"elapsed_ms":60000,"tool_calls":0},"workspace_mode":"isolated","retained":false}
```

```sh
sturgis command AGENT_ID children.delegate --params-file delegate.json
```

This example intentionally grants no tools. Supply the necessary source context through the supported parent/task workflow; an isolated child does not automatically inherit every parent file or permission. To delegate tool work, request only capabilities actually held and delegable by the parent.

## Cancellation, retention, and limits

`children.cancel` accepts `id` and an optional reason. A parent cancellation or authority change can also fence descendants. Cancellation requests stop further execution, but unknown effects remain unknown. `retained` requests a durable child mode; it does not create another top-level user-owned agent or grant unlimited background execution.

If delegation fails for resources or capability attenuation, inspect the parent instead of increasing the child request blindly. A child cannot gain a connection grant or approval merely because the parent mentions it in text. Completion criteria, when supplied, use actual scoped evidence. The user interface does not expose a shortcut to promote a child's narration to verified success.

Use [workflows](workflows.md) when you need explicit dependencies between child and parent steps, and [evidence](evidence.md) when deciding whether a result satisfies the original outcome.

## Command examples

These are command-specific examples, not a script to run every operation. Replace uppercase IDs, example endpoints, timestamps, and revision numbers with the actual intended values. Save one JSON object as `params.json`, then run `sturgis command AGENT_ID METHOD --params-file params.json`. Omit the file for `{}`. If acknowledgement is unknown, keep request and domain operation identities unchanged. For an acknowledged unavailable result, follow the feature-specific recovery instructions.

| Method | Example parameters or private-prompt command |
| --- | --- |
| `children.cancel` | `{"id":"CHILD_ID","reason":"Parent no longer needs this review"}` |
| `children.delegate` | `{"parent_work_id":"LIVE_PARENT_WORK_ID","objective":"Review unsupported dates","capabilities":[],"resources":{"tokens":16000,"iterations":2,"elapsed_ms":60000,"tool_calls":2},"workspace_mode":"isolated","retained":false}` |
| `children.get` | `{"id":"CHILD_ID"}` |
| `children.list` | `{}` |
