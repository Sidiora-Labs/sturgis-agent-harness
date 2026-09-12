---
title: "Back up and restore an agent"
description: "Export a consistent portable archive, restore into a fresh agent, and retry lost acknowledgements without duplicates."
---

A backup carries user content and historical evidence to a fresh owned agent. It is a bounded `sturgis-backup/1` ZIP file, conventionally named `.sturgis`. Web and CLI archives are interchangeable. They are private user data, not encrypted portable vaults.

## Before exporting

Let accepted work and output settle. Snapshot ownership remains held while the worker is quiesced/stopped and logical database contents and files are copied. The service exports logical JSON rather than copying a live SQLite/WAL database. If it reports `runtime_busy`, inspect Work and settle or cancel the relevant work first.

In **Settings → Agent & data**, choose **Export backup**. The browser verifies the transport size and SHA-256 before offering the raw ZIP download. Store it privately. Credentials deliberately pasted into ordinary documents or conversations can still be present in those user records.

## Worked example: move research to a new agent

```sh
sturgis backup AGENT_ID ./research.sturgis
sturgis restore ./research.sturgis --name "Restored research"
```

CLI export verifies format, byte length, and SHA-256 and creates a new output file with mode 0600; it does not silently overwrite an existing archive. Restore verifies the archive and returns a fresh agent ID owned by the authenticated account.

In the web interface, choose **Restore backup**, select that same raw `.sturgis` file, and supply the new agent name. After publication, inspect the new agent's Files, Knowledge, memory, and labeled conversation history. Reconnect the desired model and capabilities before requesting new work.

## What survives and what needs reconnection

Workspace files, original attachments/artifacts, knowledge revisions, canonical memory revisions, current derived memory, retractions, and labeled conversation history survive. Exact historical JSON is retained as immutable artifacts. Imported history cannot replay old inputs, certify new external observations, or reactivate previous work.

Vault values and IPC keys are excluded. Provider/connection configuration and activation history can remain inert in the historical snapshot, but live providers, connections, devices, grants, permissions, approvals, plugins, schedules, workflows, and child execution are not reactivated. Procedures arrive disabled. Review and reconnect authority explicitly on the target installation.

## Lost acknowledgement and deliberate extra copies

Both clients send a stable restore `request_id`. The service binds it to the authenticated account, requested name, and verified archive hash. Retrying that identity with unchanged parameters returns the original published agent. Changing the name or archive under the same identity conflicts.

The CLI derives its default identity from the name and archive hash. For an intentional additional copy:

```sh
sturgis restore ./research.sturgis --name "Restored research" --request-id second_copy
```

Keep that identity on an ambiguous retry. The browser retains pending identity metadata across ambiguous retries; it does not persist the raw archive in that retry record. An interrupted unpublished restore is removed and reports `restore_interrupted`; a new deliberate attempt requires a new identity. Once publication and its receipt commit, a lost response or crash does not delete the published agent.

## Bounds and failures

Archives allow at most 4 MiB compressed, 32 MiB expanded, 512 files, and 8 MiB per file. Logical arrays also have bounded record counts. Traversal, symlinks, special files, duplicate entries, unsupported schemas, wrong hashes, and expansion violations are rejected. Target roots and scope are assigned by the installation, never taken as executable host paths from the archive.

The HTTP export route takes `{}` and returns `{format,archive_base64,sha256,bytes}`. Restore takes `{name,archive_base64,sha256,request_id}`. Legacy callers can omit the request ID, but lose retry deduplication. If an archive is invalid, preserve the original for investigation; do not edit its manifest to hide missing content or bypass a bound.
