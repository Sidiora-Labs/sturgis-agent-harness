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

## Indexed state projections

`Journal.list_states(namespace, scope, states)` returns the same `Record` objects and identifier ordering as `list`, but SQLite selects matching `body.state` values before Python decodes them. The caller supplies a list, tuple, set, or frozenset containing 1–64 nonblank strings, each at most 128 characters and without NUL. Duplicate values are collapsed. Invalid collections raise `invalid_states`; the domain owner still defines which state names have meaning.

```python
def active_work_records(journal, scope):
    return journal.list_states("work", scope, ("queued", "running"))
```

The `records_states` expression index covers namespace, scope, `json_extract(body,'$.state')`, and ID. The query explicitly selects that index so a multi-state, ID-ordered lookup does not fall back to scanning historical rows for sort order. This requires SQLite's JSON functions. Opening an existing journal creates the index if missing; ordinary transactional writes maintain it. The query is parameterized and cannot cross the supplied scope or namespace. It excludes missing/nonmatching state values without rewriting their records.

This projection avoids decoding completed work bodies on active-work paths. It does not delete or compact history: `get`, ordinary `list`, immutable events, revisions, and cursors retain their existing contracts. Large historical records remain available for explicit inspection without becoming the scheduler's active candidate list.

## Indexed routed event pages

`Journal.routed_events(scope, kinds, *, after=0, limit=1000, binding_id=None)` returns `events`, `cursor`, and `has_more`. It selects the requested scope, kinds, and optional conversation binding in SQL before applying the page limit or decoding event bodies. Maintenance events and other conversations therefore do not consume a visible history page. The domain owner supplies the authorized scope, explicit public-kind allowlist, and binding; this helper does not authorize arbitrary caller-selected kinds.

`after` must be a nonnegative integer and `limit` an integer from 1 through 1,000, excluding booleans; invalid ranges raise `invalid_cursor`. `kinds` must be a list, tuple, set, or frozenset with 1–64 nonempty strings of at most 128 characters and no NUL; duplicates are collapsed and invalid collections raise `invalid_event`. An optional binding must be a nonempty string of at most 256 characters without NUL, otherwise `invalid_binding` is raised.

For binding-filtered reads, an explicit event `binding_id` wins. Events with an absent or null binding can instead resolve `work_id` through a `runtime_turns` record in the same scope. This includes terminal work events that carry a work ID rather than a conversation binding. The two disjoint query branches cannot duplicate a row, and a conflicting explicit binding is not overridden by the work reference.

`events_kinds` indexes `(scope, kind, seq)`. `events_binding` and `events_work` add indexed JSON binding/work paths; `records_bindings` indexes the scoped runtime-turn binding projection. Queries explicitly select these indexes. Opening an existing journal adds missing indexes without deleting events or rebuilding the conversation as synthetic messages.

The scoped sequence watermark and page are read within one transaction. The query returns at most `limit + 1` matching rows: an extra row establishes `has_more`, and the returned cursor stays at the last included event so the next page cannot skip a visible match. Once no matching rows remain in that snapshot, the cursor advances to `max(after, watermark)`, even for an empty page. Hidden maintenance activity can thus be passed in one exhausted page. Events appended after the snapshot remain eligible on the next request. The cursor is a journal position, not a count of rendered messages.

```python
def conversation_event_page(journal, scope, binding_id, after=0):
    # A trusted owner has already resolved scope and binding for this caller.
    return journal.routed_events(
        scope, ("user.prompt", "output.persisted", "runtime.turn_completed"),
        binding_id=binding_id, after=after, limit=100,
    )
```

This query returns durable evidence only. Reading or reconnecting does not re-admit messages, execute tools, or change a delivery acknowledgment.

## Async rule

Transactions use a synchronous reentrant lock and nested savepoints. Never await while holding one. Move the complete short database boundary into `asyncio.to_thread`; then perform asynchronous I/O after it returns. Catching only ordinary exceptions inside a transaction is insufficient because cancellation must roll back too.

For external effects, a transaction records intent and authority before dispatch. It cannot make a remote service participate in SQLite atomicity. [Tool reconciliation](../integrations/tool-effects.md) covers the missing-acknowledgment case.

## Implementation sources

- [agent/state/**init**.py](/reference/files/agent/state/__init__.py/)
- [agent/protocol/**init**.py](/reference/files/agent/protocol/__init__.py/)

[Developer manual](../index.md)
