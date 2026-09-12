---
title: "Profiles and observed self-state"
description: "Keep durable configuration separate from attributed awareness projections."
---

A profile owns explicit configuration and canonical resource roots. It is more than a presentation identity: runtime uses it to verify that the workspace and memory/schedule directories remain the recorded directories.

Profile roots must exist, remain within the workspace, and use distinct nonoverlapping resource directories. Default resources live beneath a scope-hashed `.sturgis/profiles/` partition. A resource check rejects changed paths, disabled profiles and escaped roots. Consumer content APIs separately keep internal paths out of public workspace file access.

`ProfileRegistry.update` uses an expected revision. Configuration cannot contain installation or self-evolution authority fields. Renaming an agent in the consumer UI does not change its immutable scope or substitute a new resource root.

## Awareness is an observation projection

A watcher has a registered source identity and active revision. It records a scoped object observation with an explicit dedupe key:

```python
watcher = core.awareness.register_watcher(
    core.scope, "repository", "repository_observer")
observed = core.awareness.observe(
    core.scope, "repository", "repository-main",
    {"head": observed_commit, "dirty": observed_dirty},
    dedupe_key=observation_identity,
)
situation = core.awareness.snapshot(core.scope)
```

The caller must obtain `observed_commit` and `observed_dirty` from the actual repository watcher. This API attributes and persists the observation; it does not inspect Git by itself. Reusing an observation identity with changed data conflicts.

The snapshot supplies current object observations and relevant goal/commitment projections for context. It exposes what has been observed, not an omniscient hidden world state. Stale or absent observations should remain distinguishable from a healthy system.

## Extend deliberately

A new watcher should define the exact object identity, source, observation cadence and failure behavior. It should not create work directly merely because something changed. Send an evidence-linked proposal through [initiative and attention](initiative-attention.md); scheduler admission remains separate.

Use [state transactions](../core/state.md) for revision consistency and [authority/evidence](../architecture/authority-evidence.md) when an observation will be used to establish a goal outcome.

## Implementation sources

- [agent/profile/__init__.py](/reference/files/agent/profile/__init__.py/)
- [agent/awareness/__init__.py](/reference/files/agent/awareness/__init__.py/)
- [agent/runtime/__init__.py](/reference/files/agent/runtime/__init__.py/)

[Developer manual](../index.md)

