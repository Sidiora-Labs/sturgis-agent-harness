---
title: "Create and version reusable procedures"
description: "Author trigger-based procedure packages, inspect selection limits, and enable, disable, or roll back their versions."
---

A procedure is a reusable set of instructions the agent can select when relevant. It is called a skill in the CLI/API. A procedure is not a scheduled job, executable plugin, new capability, or proof that its instructions were followed successfully.

## Author a procedure

Open **Settings → Procedures → Add procedure**. Enter **Procedure ID**, **Version**, **Description**, comma-separated **Trigger words**, and one **Procedure step** per line. Choose **Install procedure**.

The authored package appears with its version, state, and evidence. Current authored installs become available for selection. Use **Disable procedure** or **Enable procedure** to control that state. **Previous version** rolls back when a previous revision exists.

## Worked example: review source-backed writing

Create procedure ID `review_sources`, version `1`, description “Review a draft against its sources,” and trigger phrases `review sources, source review`. Enter these steps:

```text
List the source records used by the draft.
Separate direct observations from inferred statements.
Identify claims whose supporting source is missing or outdated.
Stop if the required source is unavailable; report the gap.
```

Ask the agent to “perform a source review” in an appropriate conversation. Expect the procedure to be eligible when its trigger matches and it fits the context budget. Selection is conditional, not a promise that every matching procedure always enters the model context.

For an API-authored package, save `procedure.json`:

```json
{"manifest":{"id":"review_sources","version":"1","description":"Review a draft against its sources","triggers":["source review"],"steps":["List the actual source records.","Flag unsupported claims."],"required_tools":[]}}
```

```sh
sturgis command AGENT_ID skills.install --params-file procedure.json
sturgis command AGENT_ID skills.list
```

Do not install the identical package twice; unchanged content is rejected as a duplicate. Publish a new version with changed steps when you intend a revision.

## Advanced fields and revisions

The CLI/API manifest requires `id`, `version`, `description`, `triggers`, `steps`, and `required_tools`. Optional lists include platforms, preconditions, validation, known_failures, stop_conditions, and inputs. The web form supplies an empty required-tools list; use the API when explicit tool requirements are needed.

`skills.enable` takes `id`, boolean `enabled`, and current `expected_revision`. `skills.rollback` takes ID and current revision. Revision numbers for concurrency control are distinct from your human-selected manifest version string.

## Limits and recovery

The manifest is bounded to 64 KiB, with nonempty triggers and steps and bounded identifier fields. Procedures can be excluded from selection when disabled, platform-incompatible, missing required tools, or outside the context budget. A procedure cannot grant those missing tools to itself.

On a stale revision, refresh `skills.list`. If rollback has no previous version, author the intended change explicitly instead. Backups restore procedure content disabled, so review and enable it after reconnecting capabilities. Learned procedure candidates and operator discovery roots belong to the core/integration surface; the consumer UI does not expose a marketplace or an automatic “train this skill” control.

## Command examples

These are command-specific examples, not a script to run every operation. Replace uppercase IDs, example endpoints, timestamps, and revision numbers with the actual intended values. Save one JSON object as `params.json`, then run `sturgis command AGENT_ID METHOD --params-file params.json`. Omit the file for `{}`. If acknowledgement is unknown, keep request and domain operation identities unchanged. For an acknowledged unavailable result, follow the feature-specific recovery instructions.

| Method | Example parameters or private-prompt command |
| --- | --- |
| `skills.enable` | `{"id":"review_sources","enabled":false,"expected_revision":1}` |
| `skills.install` | `{"manifest":{"id":"review_sources","version":"1","description":"Review source evidence","triggers":["source review"],"steps":["Identify the source for every claimed date."],"required_tools":[]}}` |
| `skills.list` | `{}` |
| `skills.rollback` | `{"id":"review_sources","expected_revision":2}` |
