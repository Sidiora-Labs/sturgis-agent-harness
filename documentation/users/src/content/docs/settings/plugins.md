---
title: "Manage installed extensions"
description: "Inspect operator-installed plugins, activate or disable them, and use health, rollback, or uninstall through supported commands."
---

Extensions provide executable capabilities installed by an operator. They differ from [procedures](procedures.md), which are instruction packages. The consumer interface manages already installed extensions; it does not download arbitrary URLs, browse a public marketplace, or install unreviewed packages on the operator's behalf.

## Inspect in the interface

Open **Settings → Procedures → Installed extensions**. Each installed extension shows its identity, status, and evidence. Use **Activate extension** or **Disable extension** for its active state. **Roll back** selects a supported previous version when one exists.

Activation refreshes the runtime's registered plugin tools and effective capabilities. It does not authorize every model action: grants, permissions, and the tool's declared boundary still apply. Treat code installed into the immutable program/dependency tree as operator-trusted code.

## Worked example: inspect before activation

Suppose your operator installed a plugin called `notes_export`. First inspect it through the CLI:

```sh
sturgis command AGENT_ID plugins.list
```

Save `plugin.json`:

```json
{"id":"notes_export"}
```

```sh
sturgis command AGENT_ID plugins.inspect --params-file plugin.json
```

Review the actual installed status and available versions. Health checks require an active plugin and otherwise return `inactive`. Save `activate-plugin.json` with the currently observed revision:

```json
{"id":"notes_export","expected_revision":2}
```

```sh
sturgis command AGENT_ID plugins.activate --params-file activate-plugin.json
sturgis command AGENT_ID plugins.health --params-file plugin.json
```

The example only applies if the operator really installed that plugin. It does not name a bundled or certified extension. After activation, inspect its tool policies before requesting effects.

## Lifecycle commands

| Command | Required parameters | Use |
| --- | --- | --- |
| `plugins.list` | none | Discover installed records. |
| `plugins.inspect` | `id` | Review status and versions without host installation paths. |
| `plugins.health` | `id` | Request the installed backend's health observation. |
| `plugins.activate` | `id`, `expected_revision` | Activate an available version. |
| `plugins.disable` | `id`, `expected_revision` | Remove active use. |
| `plugins.rollback` | `id`, `expected_revision` | Select supported previous code. |
| `plugins.uninstall` | `id`, `expected_revision` | Invoke the backend's uninstall lifecycle; CLI/API only. |

## Failure and limits

Use positive current revisions for lifecycle mutations. If another change wins first, inspect the plugin and decide again. Health failure is an observation to investigate, not a reason to bypass validation. A missing previous version cannot be manufactured by repeating Roll back.

Uninstall requires an installed, disabled, or quarantined plugin with no pending transition. Disable an active plugin and inspect the new revision before requesting uninstall.

Disabling or uninstalling affects future availability and does not prove that an earlier external effect was reversed. Hosted filesystem/process confinement is not a network firewall for arbitrary plugin code, so operators must trust what they install. Portable restore does not reactivate plugins; reconnect and review them on the target installation. See [permissions](permissions.md) and [diagnostics](usage-diagnostics.md) when an active extension is still unable to run a tool.

## Command examples

These are command-specific examples, not a script to run every operation. Replace uppercase IDs, example endpoints, timestamps, and revision numbers with the actual intended values. Save one JSON object as `params.json`, then run `sturgis command AGENT_ID METHOD --params-file params.json`. Omit the file for `{}`. If acknowledgement is unknown, keep request and domain operation identities unchanged. For an acknowledged unavailable result, follow the feature-specific recovery instructions.

| Method | Example parameters or private-prompt command |
| --- | --- |
| `plugins.activate` | `{"id":"INSTALLED_PLUGIN_ID","expected_revision":2}` |
| `plugins.disable` | `{"id":"INSTALLED_PLUGIN_ID","expected_revision":3}` |
| `plugins.health` | `{"id":"INSTALLED_PLUGIN_ID"}` |
| `plugins.inspect` | `{"id":"INSTALLED_PLUGIN_ID"}` |
| `plugins.list` | `{}` |
| `plugins.rollback` | `{"id":"INSTALLED_PLUGIN_ID","expected_revision":3}` |
| `plugins.uninstall` | `{"id":"INSTALLED_PLUGIN_ID","expected_revision":4}` |
