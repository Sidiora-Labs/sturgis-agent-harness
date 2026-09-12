---
title: "Plugin contracts and lifecycle"
description: "Run versioned extension packages with explicit state and immutable host policy."
---

Plugins are executable extensions, distinct from text procedures. A PluginManifest identifies a package version, relative entry point, tools, commands, state schema, accepted predecessor schemas and required filesystem grants.

The SDK serves exactly one framed request and returns explicit state:

```python
from agent.plugin_sdk import serve

def handle(request):
    if request["operation"] == "health":
        data = {"ready": True}
    else:
        data = {"target": request["target"]}
    return {
        "data": data,
        "state": request["state"],
        "state_schema": request["state_schema"],
    }

if __name__ == "__main__":
    serve(handle)
```

This demonstrates the worker envelope. A real package must implement the host's declared operation and output schemas, including migration/health behavior it relies on. The handler receives no authority to alter host policy.

## Package installation and execution

PluginHost installs immutable package revisions in its own root. Entry paths cannot escape the package. This backend supports rooted `workspace.read` and `workspace.write` grants. Tools and commands register separately as `plugin.ID.tool.NAME` and `plugin.ID.command.NAME`, pinned to a package version and scope.

Execution uses the concrete confined process backend. Handler state is JSON, committed only after a valid result with the expected state schema. A state migration is explicit and checked; it is not arbitrary mutation of the core journal.

Activation, health checks, drain/disable, quarantine, rollback, uninstall and recovery remain host-owned lifecycle operations. The consumer applies revision checks and serializes asynchronous plugin mutations so two UI actions cannot race using the same old revision.

## Safety and completion

A plugin result is an observation candidate. It does not create a trusted process-exit provenance marker or an operational outcome merely by using similar field names. Delegated plugin calls are rejected where the plugin backend cannot enforce child workspace constraints.

Rollback restores selected package/state behavior for future execution. It does not undo external effects. Existing uncertain tool attempts still require [reconciliation](tool-effects.md).

For reusable instructions that do not require running extension code, use [skills](skills.md). For installation source changes governed by independent checks, use [self-evolution](../behavior/evolution-evaluation.md).

## Implementation sources

- [agent/plugin_sdk/__init__.py](/reference/files/agent/plugin_sdk/__init__.py/)
- [agent/plugin_host/__init__.py](/reference/files/agent/plugin_host/__init__.py/)
- [consumer/application.py](/reference/files/consumer/application.py/)

[Developer manual](../index.md)

