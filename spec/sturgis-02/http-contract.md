# Consumer HTTP and application contract

The Python control API uses `/api/v1` and the `consumer/1` reply envelope in `contracts.md`. This is the shared contract for the web client and CLI. Domain routes never accept caller-supplied scope, runtime root, filesystem host path, deployment mode or process configuration.

| Method/path | Meaning |
| --- | --- |
| GET `/health` | Process health and contract version; no private agent data |
| GET `/bootstrap` | Whether first-owner bootstrap is required |
| POST `/bootstrap` | Claim first owner with operator token, email and password |
| POST `/auth/login` | Exchange email/password for opaque bearer session and CSRF token |
| POST `/auth/signup` | Register only when the operator enables signup |
| GET `/auth/me` | Current authenticated account |
| POST `/auth/logout` | Revoke current session |
| GET, POST `/agents` | List owned agents; create with a display name |
| GET, PATCH `/agents/{id}` | Read owned agent metadata; revisioned rename/archive |
| POST `/agents/{id}/lifecycle/{action}` | `start`, `status`, `suspend`, or `recover` a private runtime through lifecycle ownership |
| POST `/agents/{id}/commands/{method}` | Strict allowlisted worker command, with JSON `params` and stable `request_id` |
| GET `/agents/{id}/events` | Replay durable public events after a cursor; optional conversation filter and bounded wait |
| GET `/agents/{id}/attachments/{id}` | Safe authenticated attachment download |
| GET `/agents/{id}/artifacts/{id}` | Bounded authenticated artifact download |
| POST `/agents/{id}/backup` | Empty body; consistent raw ZIP archive encoded for transport with format, byte length and SHA256 |
| POST `/agents/restore` | `name`, `archive_base64`, `sha256`; optional stable `request_id`, supplied by both clients |
| POST `/external/{agent_id}/{kind}/{id}/{action}` | Fixed signed webhook/device boundary; owner-configured route and operation |

All mutation JSON is bounded and rejects duplicate keys, non-finite values and unknown fields. Browser authentication uses HttpOnly sessions, separate CSRF proof and exact operator-configured origin/host matching; CLI bearer requests use the same authorization. Login responses contain minted credentials only at the authentication boundary. Worker credentials are never returned by inspection.

## Application commands

| Command | Parameters |
| --- | --- |
| `overview` | none |
| `conversations.create` | `title` (optional) |
| `conversations.list` | `query`, `include_archived` (optional) |
| `conversations.get` | `id` |
| `conversations.update` | `id`, `expected_revision`, optional `title`, `archived` |
| `messages.send` | `conversation_id`, stable `message_id`, `text`, optional `attachments` ids and `kind` (`prompt`/`steer`) |
| `messages.cancel` | `conversation_id`, stable `message_id` |
| `messages.events` | optional `conversation_id`, `after`, `limit` |
| `attachments.upload` | `name`, `media_type`, `data_base64`, stable `operation_id` |
| `attachments.list` | optional `limit` |
| `attachments.get` / `attachments.read` | `id`; read optionally `offset`, `limit` |
| `files.list` / `files.read` | workspace-relative `path` |
| `files.write` | `path`, `data_base64`, optional `expected_sha256` (create-only when absent) |
| `files.delete` | `path`, exact `expected_sha256` |
| `knowledge.add` | `attachment_id`, `title`, `operation_id`, optional `document_id`, `expected_revision` |
| `knowledge.list` | optional `query`, `limit` |
| `knowledge.get` | `id`, optional `revision` |
| `knowledge.delete` | `id`, `expected_revision`, `operation_id` |
| `memory.query` / `memory.get` | bounded native query fields / `id`, optional `revision` |
| `memory.correct` | `id`, `content`, `expected_revision`, `operation_id` |
| `memory.retract` | `id`, `reason`, `operation_id`, optional `revision` |
| `providers.configure` | `provider_id`, `endpoint`, `model`, `context_limit`, `output_limit`, optional `tools`, `credential_ref`, `expected_revision` |
| `providers.list` / `providers.inspect` | none / optional `provider_id` |
| `providers.probe` / `providers.activate` | `provider_id`, `expected_revision` |
| `providers.credentials.put` | `name`, `secret`, optional `expected_revision`; privileged encrypted storage |
| `providers.credentials.list` / `providers.credentials.revoke` | none / `name`, `expected_revision` |
| `connections.configure` | `id`, `kind`, `endpoint`, optional `credential_ref`, `options`, `expected_revision` |
| `connections.list` / `connections.get` | none / `id` |
| `connections.grant` | `id`, `operations`, `expected_revision` |
| `connections.probe` / `connections.revoke` | `id`, `expected_revision` |
| `usage.summary` | none |

## Work, permissions and extensions

The following commands use the same strict worker envelope. Optional fields follow
the defaulted Python command signature; nested bodies retain their domain validation.

| Command | Parameters |
| --- | --- |
| `work.overview` | none |
| `work.list` | none |
| `work.get` | `id` |
| `work.cancel` | `id`, `reason` (optional) |
| `goals.list` | none |
| `goals.create` | `objective`, `limits` (optional), `criteria` (optional), `parent_id` (optional) |
| `goals.edit` | `id`, `expected_revision`, `objective` (optional), `limits` (optional), `criteria` (optional) |
| `goals.transition` | `id`, `state`, `expected_revision`, `evidence` (optional), `reason` (optional) |
| `goals.archive` | `id`, `expected_revision` |
| `commitments.list` | none |
| `commitments.create` | `description`, `beneficiary` (optional), `criteria` (optional), `expires_at` (optional) |
| `commitments.transition` | `id`, `state`, `expected_revision`, `evidence` (optional), `reason` (optional) |
| `commitments.progress` | `id`, `progress`, `expected_revision`, `reason` (optional) |
| `commitments.wait` | `id`, `trigger`, `expected_revision`, `after_seq` (optional), `expires_at` (optional) |
| `commitments.resume` | `id`, `expected_revision` |
| `schedules.list` | none |
| `schedules.create` | `conversation_id`, `text`, `spec`, `resources`, `goal_id` (optional), `missed_policy` (optional) |
| `schedules.state` | `id`, `state`, `expected_revision` |
| `workflows.list` | none |
| `workflows.get` | `id` |
| `workflows.create` | `steps`, `budget`, `goal_id` (optional), `commitment_id` (optional) |
| `workflows.revise` | `id`, `steps`, `expected_revision`, `reason` |
| `workflows.run` | `id`, `conversation_id`, `expected_revision` |
| `workflows.state` | `id`, `state`, `expected_revision`, `reason` (optional) |
| `workflows.step` | `id`, `step_id`, `state`, `expected_revision`, `evidence` (optional), `reason` (optional) |
| `children.list` | none |
| `children.get` | `id` |
| `children.cancel` | `id`, `reason` (optional) |
| `children.delegate` | `parent_work_id`, `objective`, `capabilities`, `resources`, `criteria` (optional), `workspace_mode` (optional), `retained` (optional) |
| `inbox.list` | none |
| `inbox.decide` | `id`, `decision`, `conversation_id` (optional), `resources` (optional) |
| `initiative.propose` | `source_event_id`, `action`, `signals`, `expires_at`, `dedupe_key`, `channel` (optional) |
| `attention.policy` | `policy`, `expected_revision` (optional) |
| `permissions.list` | none |
| `permissions.pending` | none |
| `permissions.request` | `event_id` |
| `permissions.set` | `tool`, `mode`, `expected_revision` (optional) |
| `permissions.approve` | `event_id`, `expires_in` (optional) |
| `permissions.deny` | `event_id` |
| `devices.list` | none |
| `devices.get` | `connection_id` |
| `devices.pair` | `connection_id`, `expected_revision`, `ttl` (optional) |
| `devices.revoke` | `connection_id`, `expected_revision` |
| `skills.install` | `manifest` |
| `skills.enable` | `id`, `enabled`, `expected_revision` |
| `skills.rollback` | `id`, `expected_revision` |
| `skills.list` | none |
| `plugins.list` | none |
| `plugins.inspect / plugins.health` | `id` |
| `plugins.activate / plugins.disable / plugins.rollback / plugins.uninstall` | `id`, `expected_revision` |
| `diagnostics.get` | none |
| `artifacts.read` | `id`; optional `offset`, `limit` |

Unsupported commands return a typed error; they never dispatch through arbitrary attribute names.

## Durable conversation semantics

A message receipt records its client identity, exact content digest, conversation and agent before a dormant worker is woken. Same identity plus different content conflicts. Accepted input, routed work, persisted output, transport delivery and inference terminal state are separate facts. Event replay is cursor based, scoped and idempotent; reading an event does not certify a world effect.

Attachments resolve only within the selected agent. The application stores an immutable source inclusion request before admission, and supplies labeled source blocks to the core context compiler. Unsupported binary interpretation and omitted suffixes remain explicit; registered bounded content-read tools can retrieve more data. Conversation history comes from real core input/output records.

## Lifecycle, provider and portability behavior

GET `/agents/{id}` includes the observed control-plane runtime state. The web client
consults this before background worker reads; suspend pauses them, and start/resume
re-observes status before enabling private queries. Archiving durably fences worker
commands, historical receipt lookup and scheduled wake; stopping a process alone is
not archival.

Text-only providers can run conversations. A consumer runtime suppresses both the
canonical invoke schema and model-visible tool descriptions when the selected model
lacks tool support. A returned tool proposal fails before dispatcher admission.
Provider probe observations remain tied to current configuration/credential revisions.

Both clients exchange `.sturgis` raw ZIP files, validating length and SHA256 before
use. Restore request identities are scoped to the authenticated account. Same identity
and parameters returns the original published agent; changed parameters conflict.
Publication and its receipt commit together. Interrupted unpublished restores are
removed during startup recovery, and their identities remain terminal instead of
silently replaying effects. A distinct identity explicitly requests another copy.
Imported history retains `portable.import` provenance and never becomes fresh input,
active authority or a request to replay old work. The [deployment guide](../../docs/sturgis-02/deployment.md)
records bounds and reconnection requirements.
