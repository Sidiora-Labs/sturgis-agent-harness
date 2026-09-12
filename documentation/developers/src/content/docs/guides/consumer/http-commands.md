---
title: "HTTP routes and strict command schemas"
description: "Use the one public consumer contract and its complete registered command catalog."
---

The HTTP API is rooted at `/api/v1`. Replies use `consumer/1` with `request_id`, `data` and `error`; an error is a bounded typed object. Unknown fields, duplicate JSON keys, nonfinite values and oversized bodies are rejected.

The public worker route is:

```http
POST /api/v1/agents/AGENT_ID/commands/goals.create
Content-Type: application/json

{
  "request_id": "create_goal_1",
  "params": {
    "objective": "Review the current source material",
    "limits": {"tokens": 20000, "tool_calls": 10}
  }
}
```

Supply actual cookie/CSRF or bearer authentication as described in [ownership](ownership-auth.md). Preserve this request identity after an unknown acknowledgment. Creating a goal records a proposed outcome; it does not execute it or establish achievement.

## Routes outside the command table

| Route | Purpose |
| --- | --- |
| GET `/health`, GET/POST `/bootstrap` | Process health and first-owner setup |
| POST `/auth/login`, `/auth/signup`, `/auth/logout`; GET `/auth/me` | Account/session lifecycle |
| GET/POST `/agents`; GET/PATCH `/agents/{id}` | Owned agent collection and revisioned metadata |
| POST `/agents/{id}/lifecycle/{action}` | Explicit start/status/suspend/recover |
| GET `/agents/{id}/events` | Scoped public event replay after a cursor |
| GET `/agents/{id}/attachments/{id}` and `/artifacts/{id}` | Safe authenticated downloads |
| POST `/agents/{id}/backup`, `/agents/restore` | Verified logical portability |
| POST `/external/{agent_id}/{kind}/{id}/{action}` | Fixed signed webhook/device ingress |

HTTP command classification grants read or write access, but all commands use POST; cookie-authenticated calls still require CSRF. Internal `_worker.*` and `ingress.*` operations are not public command aliases.

## How to read the command catalog

The following tables are derived from the application's explicit registrations and domain signatures, intersected with the HTTP allowlist. Required fields must be present; optional fields retain their implementation defaults. Unknown top-level fields are rejected. Nested dictionaries still pass the relevant domain validator: “object” is not permission to invent arbitrary policy, schedule, schema or evidence fields.

Revisions and stable IDs solve different problems. `expected_revision` prevents stale updates; `request_id` identifies transport execution; operation IDs identify content/memory mutations; message IDs identify conversation input. See [state](../core/state.md), [workflow services](content-work.md) and [retry recovery](../frontend/mutation-recovery.md).

### artifacts

| Command | Access | Required fields | Optional fields |
| --- | --- | --- | --- |
| `artifacts.read` | read | `id` | `offset`, `limit` |

### attachments

| Command | Access | Required fields | Optional fields |
| --- | --- | --- | --- |
| `attachments.get` | read | `id` | none |
| `attachments.list` | read | none | `limit` |
| `attachments.read` | read | `id` | `offset`, `limit` |
| `attachments.upload` | write | `name`, `media_type`, `data_base64`, `operation_id` | none |

### attention

| Command | Access | Required fields | Optional fields |
| --- | --- | --- | --- |
| `attention.policy` | write | `policy` | `expected_revision` |

### children

| Command | Access | Required fields | Optional fields |
| --- | --- | --- | --- |
| `children.cancel` | write | `id` | `reason` |
| `children.delegate` | write | `parent_work_id`, `objective`, `capabilities`, `resources` | `criteria`, `workspace_mode`, `retained` |
| `children.get` | read | `id` | none |
| `children.list` | read | none | none |

### commitments

| Command | Access | Required fields | Optional fields |
| --- | --- | --- | --- |
| `commitments.create` | write | `description` | `beneficiary`, `criteria`, `expires_at` |
| `commitments.list` | read | none | none |
| `commitments.progress` | write | `id`, `progress`, `expected_revision` | `reason` |
| `commitments.resume` | write | `id`, `expected_revision` | none |
| `commitments.transition` | write | `id`, `state`, `expected_revision` | `evidence`, `reason` |
| `commitments.wait` | write | `id`, `trigger`, `expected_revision` | `after_seq`, `expires_at` |

### connections

| Command | Access | Required fields | Optional fields |
| --- | --- | --- | --- |
| `connections.configure` | write | `id`, `kind`, `endpoint` | `credential_ref`, `options`, `expected_revision` |
| `connections.get` | read | `id` | none |
| `connections.grant` | write | `id`, `operations`, `expected_revision` | none |
| `connections.list` | read | none | none |
| `connections.probe` | write | `id`, `expected_revision` | none |
| `connections.revoke` | write | `id`, `expected_revision` | none |

### conversations

| Command | Access | Required fields | Optional fields |
| --- | --- | --- | --- |
| `conversations.create` | write | none | `title` |
| `conversations.get` | read | `id` | none |
| `conversations.list` | read | none | `query`, `include_archived` |
| `conversations.update` | write | `id`, `expected_revision` | `title`, `archived` |

### devices

| Command | Access | Required fields | Optional fields |
| --- | --- | --- | --- |
| `devices.get` | read | `connection_id` | none |
| `devices.list` | read | none | none |
| `devices.pair` | write | `connection_id`, `expected_revision` | `ttl` |
| `devices.revoke` | write | `connection_id`, `expected_revision` | none |

### diagnostics

| Command | Access | Required fields | Optional fields |
| --- | --- | --- | --- |
| `diagnostics.get` | read | none | none |

### files

| Command | Access | Required fields | Optional fields |
| --- | --- | --- | --- |
| `files.delete` | write | `path`, `expected_sha256` | none |
| `files.list` | read | none | `path` |
| `files.read` | read | `path` | none |
| `files.write` | write | `path`, `data_base64` | `expected_sha256` |

### goals

| Command | Access | Required fields | Optional fields |
| --- | --- | --- | --- |
| `goals.archive` | write | `id`, `expected_revision` | none |
| `goals.create` | write | `objective` | `limits`, `criteria`, `parent_id` |
| `goals.edit` | write | `id`, `expected_revision` | `objective`, `limits`, `criteria` |
| `goals.list` | read | none | none |
| `goals.transition` | write | `id`, `state`, `expected_revision` | `evidence`, `reason` |

### inbox

| Command | Access | Required fields | Optional fields |
| --- | --- | --- | --- |
| `inbox.decide` | write | `id`, `decision` | `conversation_id`, `resources` |
| `inbox.list` | read | none | none |

### initiative

| Command | Access | Required fields | Optional fields |
| --- | --- | --- | --- |
| `initiative.propose` | write | `source_event_id`, `action`, `signals`, `expires_at`, `dedupe_key` | `channel` |

### knowledge

| Command | Access | Required fields | Optional fields |
| --- | --- | --- | --- |
| `knowledge.add` | write | `attachment_id`, `title`, `operation_id` | `document_id`, `expected_revision` |
| `knowledge.delete` | write | `id`, `expected_revision`, `operation_id` | none |
| `knowledge.get` | read | `id` | `revision` |
| `knowledge.list` | read | none | `query`, `limit` |

### memory

| Command | Access | Required fields | Optional fields |
| --- | --- | --- | --- |
| `memory.correct` | write | `id`, `content`, `expected_revision`, `operation_id` | none |
| `memory.get` | read | `id` | `revision` |
| `memory.query` | read | none | `text`, `concepts`, `limit`, `embedding`, `at` |
| `memory.retract` | write | `id`, `reason`, `operation_id` | `revision` |

### messages

| Command | Access | Required fields | Optional fields |
| --- | --- | --- | --- |
| `messages.cancel` | write | `conversation_id`, `message_id` | none |
| `messages.events` | read | none | `conversation_id`, `after`, `limit` |
| `messages.send` | write | `conversation_id`, `message_id`, `text` | `attachments`, `kind` |

### overview

| Command | Access | Required fields | Optional fields |
| --- | --- | --- | --- |
| `overview` | read | none | none |

### permissions

| Command | Access | Required fields | Optional fields |
| --- | --- | --- | --- |
| `permissions.approve` | write | `event_id` | `expires_in` |
| `permissions.deny` | write | `event_id` | none |
| `permissions.list` | read | none | none |
| `permissions.pending` | read | none | none |
| `permissions.request` | read | `event_id` | none |
| `permissions.set` | write | `tool`, `mode` | `expected_revision` |

### plugins

| Command | Access | Required fields | Optional fields |
| --- | --- | --- | --- |
| `plugins.activate` | write | `id`, `expected_revision` | none |
| `plugins.disable` | write | `id`, `expected_revision` | none |
| `plugins.health` | read | `id` | none |
| `plugins.inspect` | read | `id` | none |
| `plugins.list` | read | none | none |
| `plugins.rollback` | write | `id`, `expected_revision` | none |
| `plugins.uninstall` | write | `id`, `expected_revision` | none |

### providers

| Command | Access | Required fields | Optional fields |
| --- | --- | --- | --- |
| `providers.activate` | write | `provider_id`, `expected_revision` | none |
| `providers.configure` | write | `provider_id`, `endpoint`, `model`, `context_limit`, `output_limit` | `tools`, `credential_ref`, `expected_revision` |
| `providers.credentials.list` | read | none | none |
| `providers.credentials.put` | write | `name`, `secret` | `expected_revision` |
| `providers.credentials.revoke` | write | `name`, `expected_revision` | none |
| `providers.inspect` | read | none | `provider_id` |
| `providers.list` | read | none | none |
| `providers.probe` | write | `provider_id`, `expected_revision` | none |

### schedules

| Command | Access | Required fields | Optional fields |
| --- | --- | --- | --- |
| `schedules.create` | write | `conversation_id`, `text`, `spec`, `resources` | `goal_id`, `missed_policy` |
| `schedules.list` | read | none | none |
| `schedules.state` | write | `id`, `state`, `expected_revision` | none |

### skills

| Command | Access | Required fields | Optional fields |
| --- | --- | --- | --- |
| `skills.enable` | write | `id`, `enabled`, `expected_revision` | none |
| `skills.install` | write | `manifest` | none |
| `skills.list` | read | none | none |
| `skills.rollback` | write | `id`, `expected_revision` | none |

### usage

| Command | Access | Required fields | Optional fields |
| --- | --- | --- | --- |
| `usage.summary` | read | none | none |

### work

| Command | Access | Required fields | Optional fields |
| --- | --- | --- | --- |
| `work.cancel` | write | `id` | `reason` |
| `work.get` | read | `id` | none |
| `work.list` | read | none | none |
| `work.overview` | read | none | none |

### workflows

| Command | Access | Required fields | Optional fields |
| --- | --- | --- | --- |
| `workflows.create` | write | `steps`, `budget` | `goal_id`, `commitment_id` |
| `workflows.get` | read | `id` | none |
| `workflows.list` | read | none | none |
| `workflows.revise` | write | `id`, `steps`, `expected_revision`, `reason` | none |
| `workflows.run` | write | `id`, `conversation_id`, `expected_revision` | none |
| `workflows.state` | write | `id`, `state`, `expected_revision` | `reason` |
| `workflows.step` | write | `id`, `step_id`, `state`, `expected_revision` | `evidence`, `reason` |


## Implementation sources

- [consumer/api.py](/reference/files/consumer/api.py/)
- [consumer/application.py](/reference/files/consumer/application.py/)
- [consumer/contracts.py](/reference/files/consumer/contracts.py/)
- [spec/sturgis-02/http-contract.md](/reference/files/spec/sturgis-02/http-contract.md/)
- [consumer/work.py](/reference/files/consumer/work.py/)
- [consumer/permissions.py](/reference/files/consumer/permissions.py/)
- [consumer/device.py](/reference/files/consumer/device.py/)

[Developer manual](../index.md)

