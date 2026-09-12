---
title: "Journal records, events and cursors"
description: "Commit causal facts and revisioned projections in one scoped transaction."
---

`Journal` owns the operational SQLite connection, scoped events, revisioned records and durable consumer cursors. WAL mode and FULL synchronous writes support durability. Domain owners share this journal so an authorization claim and its intent can commit in the same transaction.

```python
from agent.state import Journal

journal = Journal(":memory:")
with journal.transaction():
    record = journal.put("example.settings", "theme", "demo", {"value": "dark"})
    event = journal.append(
        "demo", "example.setting_changed",
        {"id": record.id, "revision": record.revision},
        source="example_owner", dedupe_key="set-theme-1",
    )

updated = journal.put(
    "example.settings", "theme", "demo",
    {"value": "light"}, expected_revision=record.revision,
)
journal.close()
```

The example demonstrates storage mechanics, not permission for a model to append arbitrary events. Production namespaces and event sources belong to explicit service owners.

## Revisions and identities

`put` defaults to expected revision zero, meaning create only. A successful write returns the next revision. Updating with an old revision fails rather than overwriting someone else's change. Callers must reread and decide whether the new state still permits their operation.

Events are immutable and globally sequenced within the database, then queried by scope. A dedupe key is unique within its scope. Reusing it with exactly the same kind, source and payload returns the existing event; changing that content conflicts.

Consumer cursors record the last processed sequence. Advance a cursor only after the relevant acknowledgment or explicit rejection has been durably handled. Otherwise a crash can convert an unprocessed event into a falsely completed delivery.

## Async rule

Transactions use a synchronous reentrant lock and nested savepoints. Never await while holding one. Move the complete short database boundary into `asyncio.to_thread`; then perform asynchronous I/O after it returns. Catching only ordinary exceptions inside a transaction is insufficient because cancellation must roll back too.

For external effects, a transaction records intent and authority before dispatch. It cannot make a remote service participate in SQLite atomicity. [Tool reconciliation](../integrations/tool-effects.md) covers the missing-acknowledgment case.

## Implementation sources

- [agent/state/__init__.py](/reference/files/agent/state/__init__.py/)
- [agent/protocol/__init__.py](/reference/files/agent/protocol/__init__.py/)

[Developer manual](../index.md)

