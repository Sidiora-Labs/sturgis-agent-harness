---
title: "Logical backup and idempotent restore"
description: "Move content into a fresh agent without importing live authority or replaying history."
---

A `.sturgis` file is a raw ZIP using format `sturgis-backup/1`. HTTP encodes that ZIP as base64 and includes its byte length and SHA-256. Both clients verify those transport fields before using the archive; changing only the filename extension is not validation.

Current bounds include 4 MiB compressed archive size, 8 MiB per file, 32 MiB total expanded content, 512 files and 10,000 logical rows. Paths, ZIP entries, manifest fields, hashes, source records and allowed content are validated before publication. Archive path traversal and unbounded extraction are rejected.

## Export a consistent view

The owner must authorize the agent. Lifecycle obtains a safe stopped-worker snapshot, including the schedule/admission fence, before reading its files and databases. A running SQLite file copied without its owning lifecycle is not equivalent.

The export is logical content and historical evidence, not a transplant of the private runtime. Credentials, provider/connection/device authority, permissions, plugins, schedules, workflows, children and execution grants require deliberate reconnection or reactivation according to the archive's reconnect record.

Portable skill definitions are imported disabled. Historical messages remain historical, and imported work has no capabilities or live allocation. Retaining a source or procedure for inspection therefore does not silently enable the behavior that used it in the original agent.

## Restore safely

```json
{
  "name": "Imported research",
  "archive_base64": "<base64 of the verified raw ZIP>",
  "sha256": "<SHA-256 of those same ZIP bytes>",
  "request_id": "restore_research_1"
}
```

Use a real file and digest. The request ID is scoped to the authenticated account. Reusing it with the same name and archive hash returns the originally published agent. Changed parameters conflict. A distinct ID explicitly requests another copy.

Restore first creates a fresh server-assigned agent in restoring state. Scope, workspace and profile resources are relocated. Imported history uses `portable.import` provenance and historical states; it cannot become a fresh user request, active execution grant or completion receipt. Publication and the completed restore receipt commit together.

If an unpublished restore is interrupted, startup recovery removes its target and leaves a terminal `restore_interrupted` result. A new explicit attempt needs a new ID. After an unknown acknowledgment, retain the original ID until its outcome is known.

A logical agent backup does not replace an operator's private installation/key recovery strategy. See [release migration](../operations/release-migration.md) and [frontend recovery](../frontend/mutation-recovery.md).

## Implementation sources

- [consumer/portability.py](/reference/files/consumer/portability.py/)
- [consumer/cli.py](/reference/files/consumer/cli.py/)
- [frontend/src/pages/SettingsPage.tsx](/reference/files/frontend/src/pages/SettingsPage.tsx/)
- [consumer/lifecycle.py](/reference/files/consumer/lifecycle.py/)

[Developer manual](../index.md)
