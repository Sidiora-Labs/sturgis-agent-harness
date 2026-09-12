---
title: "Versioned skills and learned procedures"
description: "Select bounded procedures while preserving requirements, evidence and activation."
---

A skill is a versioned procedure package. It describes when it applies, what tools it requires, how to proceed, and optionally its inputs, validation, known failures and stopping conditions. It is not an executable plugin or a new capability grant.

```python
manifest = {
    "id": "inspect-notes", "version": "1",
    "description": "Read existing project notes and report observed facts.",
    "triggers": ["project notes"],
    "steps": ["Read the requested file.", "Summarize with its source reference."],
    "required_tools": ["workspace.read"],
    "stop_conditions": ["Stop if the file is unavailable."],
}
core.skills.install(core.scope, manifest)
selection = core.skills.select(
    core.scope, "Summarize the project notes",
    ready_tools=["workspace.read"], max_skills=4, max_bytes=8192)
```

The selection reports both included packages and exclusions. A matching trigger cannot compensate for an unavailable tool, wrong platform, disabled package or exceeded context budget.

## Discovery and precedence

Configured discovery roots are built-in, global and workspace directories containing bounded `.skill.json` files. Later roots override earlier package identities; stored profile records have highest precedence. Discovery rejects symlink sources and oversized packages. Installation validates finite JSON and explicit bounded procedure fields.

The consumer exposes install, enable, list and rollback operations with revision checks at its boundary. Selecting a skill does not automatically call its required tools; those still use current permissions and scheduler claims.

## Learning requires evidence and activation

Synthesis requires at least two distinct scoped completed/success tool-result events whose tool names belong to the procedure's requirements. It creates a candidate rather than immediately replacing the live skill. With configured review, a human review precedes readiness; activation publishes the approved candidate and records its revision.

This evidence establishes that those observed invocations succeeded. It does not prove every future use of the procedure will succeed, nor does it transform tool results into broader goal completion.

Rollback selects the prior stored version or withdraws a package with no predecessor. Keep original source and evidence references when investigating a learned procedure's failure. See [context selection](../core/context.md) and [tool outcomes](tool-effects.md).

## Implementation sources

- [agent/skills/__init__.py](/reference/files/agent/skills/__init__.py/)
- [consumer/application.py](/reference/files/consumer/application.py/)

[Developer manual](../index.md)

