---
title: "Release evidence and migration boundaries"
description: "Qualify exact inputs and distinguish portability from live-state migration."
---

A release claim belongs to exact implementation/build inputs and the gate that exercised them. Keep the source manifest, independent coverage reports, browser evidence and packaged artifact identity together. Documentation should explain their scope without upgrading local fixtures into live vendor or cloud certification.

The Sturgis-01 report records the earlier core-only qualification. Its counts and source hash must not be rewritten to describe later consumer behavior. The current aggregate gate requalifies the core with the consumer layer and independently checks Python, C++ and authored web lines.

## Worked release decision

Suppose a provider parsing fix changes one branch after the full gate passed. Focused tests can establish the fix locally, but the previous aggregate source hash no longer describes the candidate release. Stabilize the change, rerun the relevant final qualification, rebuild or verify the exact packaged bytes, and record fresh evidence.

A docs-only wording correction may use the independent documentation gate while preserving product-source qualification. A change to an executable tutorial or generated-source extractor belongs to the docs gate's inputs, not merely visual copy.

## Three migration paths

An ordinary process restart reopens the same operational root and preserves scope, receipts and memory. It interrupts prior inference and does not replay uncertain effects.

Logical backup/restore creates a fresh agent. It relocates scope and roots, preserves historical provenance, and requires reconnection/regranting of execution-related facilities. It is not a way to copy live authority or silently resume old schedules.

Self-evolution selects verified immutable source for fresh execution under explicit installation policy. Its backend requires the same persistent behavior-state schema; incompatible state migrations are rejected. Rollback changes source selection, not external effects.

The native database schema is versioned and unknown versions fail closed. Do not edit a schema version merely to bypass incompatibility. Design and qualify a real migration before claiming support.

Read [packaging](packaging.md), [backup](../consumer/backup-restore.md) and [governed evolution](../behavior/evolution-evaluation.md). Use [documentation contribution](../getting-started/documentation.md) when changing the manuals or their reference pipeline.

## Implementation sources

- [scripts/qualify_consumer.py](/reference/files/scripts/qualify_consumer.py/)
- [spec/sturgis-02/coverage.md](/reference/files/spec/sturgis-02/coverage.md/)
- [consumer/portability.py](/reference/files/consumer/portability.py/)
- [agent/self_evolution/__init__.py](/reference/files/agent/self_evolution/__init__.py/)
- [native/memory/PROTOCOL.md](/reference/files/native/memory/PROTOCOL.md/)
- [docs/qualification.md](/reference/files/docs/qualification.md/)
- [docs/sturgis-02/qualification.md](/reference/files/docs/sturgis-02/qualification.md/)

[Developer manual](../index.md)

