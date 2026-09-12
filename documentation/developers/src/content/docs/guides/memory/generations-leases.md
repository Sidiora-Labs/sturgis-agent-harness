---
title: "Generation publication, leases and pruning"
description: "Build a complete memory view before atomically selecting it."
---

Canonical ingestion is immediately visible to live queries. Derived consolidation follows a separate publication path so readers never observe a half-built generation.

`begin_generation` checks the expected current parent, snapshots canonical records at a source watermark, and copies retained derived records. `stage` adds bounded derived records. `publish` changes the current pointer only after its stored parent and caller's expected parent still match the current generation.

For a new database whose current generation is zero, the embedding host can invoke this function with its real NativeMemoryClient, trusted scope, stable cycle identity and reader lease identity. Keep both identities short enough that the generated operation IDs fit the 256-byte native limit.

```python
async def generation_integration_example(memory, scope, cycle_id, lease_id):
    started = await memory.request(scope, "begin_generation", {
        "operation_id": cycle_id + ":begin", "expected_parent": 0,
    })
    if started["status"] != "ok":
        return started
    generation = started["data"]["generation"]
    published = await memory.request(scope, "publish", {
        "operation_id": cycle_id + ":publish",
        "generation": generation, "expected_parent": 0,
    })
    if published["status"] != "ok":
        return published
    leased = await memory.request(scope, "lease", {
        "operation_id": lease_id + ":lease", "lease_id": lease_id,
        "generation": generation, "ttl_seconds": 300,
    })
    if leased["status"] != "ok":
        return leased
    try:
        view = await memory.query(scope, lease_id=lease_id)
    finally:
        released = await memory.request(scope, "release", {"lease_id": lease_id})
    return {"view": view, "release": released}
```

The function checks mutation status before using returned data and exposes query/release results to its caller. A normal integration reads the current parent first; zero is valid only for initial publication. The example publishes a canonical snapshot without adding derived summaries. It does not run until the host awaits it; the host retains ownership of the client and its database.

## Conflict and lease rules

A conflicting generation stays in staging for diagnosis. It is not silently rebased. Stage batches contain 1–100 records and are atomic with their operation receipt. Canonical IDs in the snapshot cannot be overwritten by staged derived records.

A reader lease pins a published generation for 1–3600 seconds, default 300. Generation zero has no leasable snapshot. Lease identity is scoped; an existing lease ID cannot be reused. Released or expired leases cannot read.

Pruning is explicit and bounded. Current publication and unexpired leases protect generations. Other requested snapshots retire; unreferenced derived objects and projections can be removed. Canonical revisions, source evidence, metadata and idempotency receipts remain durable. Retraction overrides old leased views immediately, so a lease does not resurrect withdrawn material.

See [sleep](ingestion-sleep.md) for durable cycle recovery and [backup](../consumer/backup-restore.md) for the separate portable-data boundary.

## Implementation sources

- [agent/memory/client.py](/reference/files/agent/memory/client.py/)
- [native/memory/engine.cpp](/reference/files/native/memory/engine.cpp/)
- [native/memory/PROTOCOL.md](/reference/files/native/memory/PROTOCOL.md/)
- [agent/memory/sleep.py](/reference/files/agent/memory/sleep.py/)

[Developer manual](../index.md)
