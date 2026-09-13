# Complete Sturgis documentation delivery

This index connects the repository documentation workflow to the two independently
built sites. The complete verification result is the source-bound
[full-site qualification receipt](../evidence/sturgis-docs/qualification.json).
A receipt is valid only while `python3 scripts/docs_gate.py verify-receipt` succeeds.
The actual rendered-site gate validates every local route, source line and symbol
anchor, and exercises search, navigation, themes, copying and mobile layouts.

## User manuals

Start with [Learn and use Sturgis](users/src/content/docs/index.md).
The user site teaches setup for self-hosted and hosted operation, accounts, models,
conversations, memory, knowledge, workspace files, goals, commitments, workflows,
schedules, delegation, connections, permissions, procedures, plugins, lifecycle,
backup, diagnostics and the CLI. The
[command cookbook](users/src/content/docs/reference/command-recipes.md) provides an
individual worked recipe, prerequisites, expected observations and recovery for
every public domain command. The [user coverage map](coverage/users.json) binds
these manuals to their reviewed implementation sources.

The [Model Gateway manual](users/src/content/docs/guides/model-gateway.md) covers preset and custom
connections, live discovery, manual import, free and local options, native account sign-in,
agent model selection, app keys, route management, limits and configuration transfer.
Its [standalone guide](users/src/content/docs/getting-started/standalone-gateway.md) runs the same
subsystem without agents. [Gateway developer guides](developers/src/content/docs/guides/gateway/architecture.md)
explain the independent state, protocol, account, network, HTTP and frontend boundaries.

## Developer manuals and implementation reference

Start with [Build with Sturgis](developers/src/content/docs/index.md) and the
[developer learning map](developers/src/content/docs/guides/index.md).
The manuals cover architecture, ownership, authority, protocol schemas, journal
transactions, context, scheduling, I/O, native memory, behavior, adapters,
extensions, consumer services, frontend structure, operation and qualification.
The [developer coverage map](coverage/developers.json) records reviewed source
relationships. The [complete reference manifest](coverage/reference.json) accounts
for every included source file, parsed definition and numbered source line.
Generated references complement authored explanations with exact syntax and
source spans; they do not establish that a behavior executed.

## Release and migration guidance

Read [release and migration](developers/src/content/docs/guides/operations/release-migration.md),
[packaging and deployment](developers/src/content/docs/guides/operations/packaging.md),
and the [user backup and restore manual](users/src/content/docs/settings/backup-restore.md).
Both docs sites build to static files and can be hosted independently at their own
origins. [Build and preview instructions](README.md) describe the local commands,
origin settings and mandatory documentation stage for future specs.

## Evidence, exclusions and limits

[Coverage evidence](../evidence/sturgis-docs/coverage-floors.json),
[browser results](../evidence/sturgis-docs/browser.json), and the
[full receipt](../evidence/sturgis-docs/qualification.json) retain the documentation
checks separately from historical runtime qualification. Read the
[product limits](users/src/content/docs/reference/limits.md) and
[qualification boundaries](developers/src/content/docs/guides/getting-started/qualification.md)
for supported operation and the limits of recorded proof. Syntax checks do not
execute every remote-provider recipe. Local docs delivery is not public hosting.

The source inventory explicitly excludes dependencies, generated bundles, caches,
secrets, recursive documentation content and mutable task coordination metadata.
Those classifications appear in the reference manifest. Native and consumer
qualification boundaries remain documented; documentation success does not
silently upgrade them to remote-vendor or production-deployment certification.
