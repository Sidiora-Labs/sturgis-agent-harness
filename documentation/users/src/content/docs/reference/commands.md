---
title: "Complete command and route reference"
description: "Find every allowlisted consumer command, its parameters, the feature manual, and the shared API/CLI request conventions."
---

For concrete inputs and command-specific outcomes, use the [95-command cookbook](command-recipes.md). Every recipe includes prerequisites and recovery guidance.

Every domain command below uses the same selected agent and authenticated boundary. Read its feature manual before executing a mutation. IDs shown in examples are placeholders; use actual owned IDs and current revisions. Optional parameters are marked as such or described by the linked manual.

## Worked example: inspect a knowledge revision

Save `document.json`:

```json
{"id":"DOCUMENT_ID","revision":1}
```

```sh
sturgis command AGENT_ID knowledge.get --params-file document.json
```

The underlying HTTP request is POST `/api/v1/agents/AGENT_ID/commands/knowledge.get` with:

```json
{"params":{"id":"DOCUMENT_ID","revision":1},"request_id":"inspect_document_revision_1"}
```

The service returns a `consumer/1` envelope containing `request_id`, `data`, and `error`; exactly one of data/error is present as a non-null result. A returned HTTP request ID is distinct from domain message or operation identities. The CLI prints the data or a typed error and applies its normal secret redaction.

The command route requires `params` and a stable request ID. It never accepts a caller-chosen runtime root, scope, deployment mode, or executable. It also does not dispatch arbitrary Python attributes or expose internal ingress commands. Read commands can wake a suspended runtime; use agent metadata/lifecycle status when you want only control-plane state.

## Account, lifecycle, and download routes

All paths in this table are under `/api/v1`. Browser mutations require the HttpOnly session, matching CSRF proof, and exact installation origin. CLI bearer requests use the same owner authorization.

| Method and path | Purpose and body |
| --- | --- |
| GET `/health` | Control process health; no private agent state. |
| GET `/bootstrap` | Whether first-owner setup is required and signup is allowed. |
| POST `/bootstrap` | First owner: `email`, `password`, `token`; creates a session. |
| POST `/auth/login` | `email`, `password`; creates a session. |
| POST `/auth/signup` | `email`, `password`; only when enabled. |
| GET `/auth/me` | Authenticated account. |
| POST `/auth/logout` | `{}`; revoke current session. |
| GET `/agents` | Owned agents; optional query `include_archived=true`. |
| POST `/agents` | `name`; create a fresh private agent. |
| GET `/agents/{agent_id}` | Owned metadata and observed runtime state. |
| PATCH `/agents/{agent_id}` | `expected_revision` plus either `name` or `archived:true`. |
| POST `/agents/{agent_id}/lifecycle/{action}` | `{}`; actions `start`, `status`, `suspend`, `recover`. |
| POST `/agents/{agent_id}/commands/{method}` | `params`, `request_id`; exact allowlist below. |
| GET `/agents/{agent_id}/events` | `after`, optional `conversation_id`, bounded `wait`; durable replay, optionally SSE. |
| GET `/agents/{agent_id}/attachments/{attachment_id}` | Authenticated original attachment download. |
| GET `/agents/{agent_id}/artifacts/{artifact_id}` | Bounded authenticated immutable artifact download. |
| POST `/agents/{agent_id}/backup` | `{}`; `{format,archive_base64,sha256,bytes}` result. |
| POST `/agents/restore` | `name`, `archive_base64`, `sha256`, stable `request_id`; fresh owned agent or same receipt on retry. |
| POST `/external/{agent_id}/webhook/{id}/receive` | Signed external event body; see [webhooks](../connections/webhooks.md). |
| POST `/external/{agent_id}/device/{id}/{action}` | Claim, sync, poll, respond; see [devices](../connections/devices.md). |

Legacy restore callers can omit request_id, but then cannot deduplicate a lost prior acknowledgement. The metadata routes do not expose password resets, account invitation management, arbitrary workspace relocation, or agent unarchive in this release.

## Read the domain tables

`none` means an empty parameters object. Stable `operation_id` is a required parameter for the content mutations that list it; generic CLI request IDs do not automatically populate it. `expected_revision` protects an existing record against concurrent changes, while `expected_sha256` protects exact file bytes. Keep all identities and parameters unchanged on an ambiguous retry.

When an acknowledged knowledge/memory response contains an unavailable native result, the outer request already has a completed receipt. After native recovery, use a new outer `request_id` with the same domain `operation_id` and parameters to resume that operation; see the [CLI retry guidance](../cli/using-the-cli.md#domain-commands-and-exact-retries).

The [CLI guide](../cli/using-the-cli.md) covers argument placement and private prompts. The [limits](limits.md) and [troubleshooting](troubleshooting.md) pages cover bounds and failure recovery. Model-visible connection tools still use normal dispatcher admission; there is no `connections.execute` command hidden behind this table.

## Connect speech and image services

Use the [connect speech and image services manual](../connections/media.md#command-examples) for worked examples and failure recovery.

| Command | Parameters |
| --- | --- |
| `artifacts.read` | `id`; optional `offset`, `limit` |

## Build and revise your source library

Use the [build and revise your source library manual](../knowledge/source-library.md#command-examples) for worked examples and failure recovery.

| Command | Parameters |
| --- | --- |
| `attachments.get` | `id` |
| `attachments.list` | optional `limit` |
| `attachments.read` | `id`, optional `offset`, `limit` |
| `attachments.upload` | `name`, `media_type`, `data_base64`, stable `operation_id` |
| `knowledge.add` | `attachment_id`, `title`, `operation_id`, optional `document_id`, `expected_revision` |
| `knowledge.delete` | `id`, `expected_revision`, `operation_id` |
| `knowledge.get` | `id`, optional `revision` |
| `knowledge.list` | optional `query`, `limit` |

## Review proposals and set attention policy

Use the [review proposals and set attention policy manual](../work/initiative-and-attention.md#command-examples) for worked examples and failure recovery.

| Command | Parameters |
| --- | --- |
| `attention.policy` | `policy`, `expected_revision` (optional) |
| `inbox.decide` | `id`, `decision`, `conversation_id` (optional), `resources` (optional) |
| `inbox.list` | none |
| `initiative.propose` | `source_event_id`, `action`, `signals`, `expires_at`, `dedupe_key`, `channel` (optional) |

## Inspect and manage delegated work

Use the [inspect and manage delegated work manual](../work/delegation.md#command-examples) for worked examples and failure recovery.

| Command | Parameters |
| --- | --- |
| `children.cancel` | `id`, `reason` (optional) |
| `children.delegate` | `parent_work_id`, `objective`, `capabilities`, `resources`, `criteria` (optional), `workspace_mode` (optional), `retained` (optional) |
| `children.get` | `id` |
| `children.list` | none |

## Track accepted responsibilities

Use the [track accepted responsibilities manual](../work/commitments.md#command-examples) for worked examples and failure recovery.

| Command | Parameters |
| --- | --- |
| `commitments.create` | `description`, `beneficiary` (optional), `criteria` (optional), `expires_at` (optional) |
| `commitments.list` | none |
| `commitments.progress` | `id`, `progress`, `expected_revision`, `reason` (optional) |
| `commitments.resume` | `id`, `expected_revision` |
| `commitments.transition` | `id`, `state`, `expected_revision`, `evidence` (optional), `reason` (optional) |
| `commitments.wait` | `id`, `trigger`, `expected_revision`, `after_seq` (optional), `expires_at` (optional) |

## Configure connection access and readiness

Use the [configure connection access and readiness manual](../connections/overview.md#command-examples) for worked examples and failure recovery.

| Command | Parameters |
| --- | --- |
| `connections.configure` | `id`, `kind`, `endpoint`, optional `credential_ref`, `options`, `expected_revision` |
| `connections.get` | `id` |
| `connections.grant` | `id`, `operations`, `expected_revision` |
| `connections.list` | none |
| `connections.probe` | `id`, `expected_revision` |
| `connections.revoke` | `id`, `expected_revision` |

## Start and continue a conversation

Use the [start and continue a conversation manual](../conversations/continuing-conversations.md#command-examples) for worked examples and failure recovery.

| Command | Parameters |
| --- | --- |
| `conversations.create` | `title` (optional) |

## Attach sources and find conversation history

Use the [attach sources and find conversation history manual](../conversations/sources-history-search.md#command-examples) for worked examples and failure recovery.

| Command | Parameters |
| --- | --- |
| `conversations.get` | `id` |
| `conversations.list` | `query`, `include_archived` (optional) |
| `conversations.update` | `id`, `expected_revision`, optional `title`, `archived` |
| `messages.events` | optional `conversation_id`, `after`, `limit` |

## Pair and manage a compatible device

Use the [pair and manage a compatible device manual](../connections/devices.md#command-examples) for worked examples and failure recovery.

| Command | Parameters |
| --- | --- |
| `devices.get` | `connection_id` |
| `devices.list` | none |
| `devices.pair` | `connection_id`, `expected_revision`, `ttl` (optional) |
| `devices.revoke` | `connection_id`, `expected_revision` |

## Inspect usage and runtime diagnostics

Use the [inspect usage and runtime diagnostics manual](../settings/usage-diagnostics.md#command-examples) for worked examples and failure recovery.

| Command | Parameters |
| --- | --- |
| `diagnostics.get` | none |
| `overview` | none |
| `usage.summary` | none |

## Create and safely edit workspace files

Use the [create and safely edit workspace files manual](../files/workspace.md#command-examples) for worked examples and failure recovery.

| Command | Parameters |
| --- | --- |
| `files.delete` | `path`, exact `expected_sha256` |
| `files.list` | optional `path` (defaults to `.`) |
| `files.read` | `path` |
| `files.write` | `path`, `data_base64`, optional `expected_sha256` (create-only when absent) |

## Create and manage goals

Use the [create and manage goals manual](../work/goals.md#command-examples) for worked examples and failure recovery.

| Command | Parameters |
| --- | --- |
| `goals.archive` | `id`, `expected_revision` |
| `goals.create` | `objective`, `limits` (optional), `criteria` (optional), `parent_id` (optional) |
| `goals.edit` | `id`, `expected_revision`, `objective` (optional), `limits` (optional), `criteria` (optional) |
| `goals.list` | none |
| `goals.transition` | `id`, `state`, `expected_revision`, `evidence` (optional), `reason` (optional) |

## Search, correct, and retract memory

Use the [search, correct, and retract memory manual](../knowledge/memory.md#command-examples) for worked examples and failure recovery.

| Command | Parameters |
| --- | --- |
| `memory.correct` | `id`, `content`, `expected_revision`, `operation_id` |
| `memory.get` | `id`, optional `revision` |
| `memory.query` | optional `text`, `concepts`, `embedding`, `at`, `limit` |
| `memory.retract` | `id`, `reason`, `operation_id`, optional `revision` |

## Steer, stop, and retry active work

Use the [steer, stop, and retry active work manual](../conversations/steering-and-cancellation.md#command-examples) for worked examples and failure recovery.

| Command | Parameters |
| --- | --- |
| `messages.cancel` | `conversation_id`, stable `message_id` |
| `messages.send` | `conversation_id`, stable `message_id`, `text`, optional `attachments` ids and `kind` (`prompt`/`steer`) |

## Set tool permissions and approve exact actions

Use the [set tool permissions and approve exact actions manual](../settings/permissions.md#command-examples) for worked examples and failure recovery.

| Command | Parameters |
| --- | --- |
| `permissions.approve` | `event_id`, `expires_in` (optional) |
| `permissions.deny` | `event_id` |
| `permissions.list` | none |
| `permissions.pending` | none |
| `permissions.request` | `event_id` |
| `permissions.set` | `tool`, `mode`, `expected_revision` (optional) |

## Manage installed extensions

Use the [manage installed extensions manual](../settings/plugins.md#command-examples) for worked examples and failure recovery.

| Command | Parameters |
| --- | --- |
| `plugins.activate` | `id`, `expected_revision` |
| `plugins.disable` | `id`, `expected_revision` |
| `plugins.health` | `id` |
| `plugins.inspect` | `id` |
| `plugins.list` | none |
| `plugins.rollback` | `id`, `expected_revision` |
| `plugins.uninstall` | `id`, `expected_revision` |

## Connect and select a model

Use the [connect and select a model manual](../models/connect-a-model.md#command-examples) for worked examples and failure recovery.

| Command | Parameters |
| --- | --- |
| `providers.activate` | `provider_id`, `expected_revision` |
| `providers.configure` | `provider_id`, `endpoint`, `model`, `context_limit`, `output_limit`, optional `tools`, `credential_ref`, `expected_revision` |
| `providers.inspect` | optional `provider_id` |
| `providers.list` | none |
| `providers.probe` | `provider_id`, `expected_revision` |

## Store, rotate, and revoke credentials

Use the [store, rotate, and revoke credentials manual](../models/credentials.md#command-examples) for worked examples and failure recovery.

| Command | Parameters |
| --- | --- |
| `providers.credentials.list` | none |
| `providers.credentials.put` | `name`, `secret`, optional `expected_revision`; privileged encrypted storage |
| `providers.credentials.revoke` | `name`, `expected_revision` |

## Schedule prompts and recurring work

Use the [schedule prompts and recurring work manual](../work/schedules.md#command-examples) for worked examples and failure recovery.

| Command | Parameters |
| --- | --- |
| `schedules.create` | `conversation_id`, `text`, `spec`, `resources`, `goal_id` (optional), `missed_policy` (optional) |
| `schedules.list` | none |
| `schedules.state` | `id`, `state`, `expected_revision` |

## Create and version reusable procedures

Use the [create and version reusable procedures manual](../settings/procedures.md#command-examples) for worked examples and failure recovery.

| Command | Parameters |
| --- | --- |
| `skills.enable` | `id`, `enabled`, `expected_revision` |
| `skills.install` | `manifest` |
| `skills.list` | none |
| `skills.rollback` | `id`, `expected_revision` |

## Understand ongoing work

Use the [understand ongoing work manual](../work/overview.md#command-examples) for worked examples and failure recovery.

| Command | Parameters |
| --- | --- |
| `work.cancel` | `id`, `reason` (optional) |
| `work.get` | `id` |
| `work.list` | none |
| `work.overview` | none |

## Run workflows and revise plans

Use the [run workflows and revise plans manual](../work/workflows.md#command-examples) for worked examples and failure recovery.

| Command | Parameters |
| --- | --- |
| `workflows.create` | `steps`, `budget`, `goal_id` (optional), `commitment_id` (optional) |
| `workflows.get` | `id` |
| `workflows.list` | none |
| `workflows.revise` | `id`, `steps`, `expected_revision`, `reason` |
| `workflows.run` | `id`, `conversation_id`, `expected_revision` |
| `workflows.state` | `id`, `state`, `expected_revision`, `reason` (optional) |
| `workflows.step` | `id`, `step_id`, `state`, `expected_revision`, `evidence` (optional), `reason` (optional) |
