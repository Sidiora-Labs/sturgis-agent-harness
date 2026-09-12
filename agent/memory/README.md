# Memory core API

`NativeMemoryClient` asynchronously supervises the actual `sturgis-memory` executable.
See `native/memory/PROTOCOL.md` for native records and operation arguments.

```python
client = NativeMemoryClient("memory.sqlite", binary=".build/native/sturgis-memory")
await client.start()  # bool; request() also starts lazily
result = await client.query("scope", text="dependencies", timeout=0.2)
blocks, freshness = memory_blocks("scope", result)
await client.close()
```

Requests correlate by ID with a dedicated read pump and serialized frame writes.
Requests may time out or be cancelled while other callers continue. Caller task
cancellation raises `CancelledError`; no automatic mutation retry occurs. Responses
after a timeout/cancellation are drained. An unavailable result says whether dispatch
occurred, and marks possible dispatched effects unknown. Explicit idempotency keys
allow callers to reconcile a committed request after transport loss. `close()` is
terminal for that client; create another client to resume the same database.

`request(scope,operation,arguments=None,timeout=None)` returns the native envelope.
`ingest(scope,record,operation_id=...,expected_revision=0,timeout=None)` requires an
explicit idempotency key. `query(scope,text='',concepts=None,embedding=None,at=None,
limit=20,lease_id=None,timeout=None)` returns scores, scope, generations and watermarks.
`memory_blocks()` returns user-role retrieved blocks plus source/freshness metadata;
an unavailable or partial result never becomes an empty successful memory claim.

`MemoryIngestor(journal,client).process_event(event)` is a pure transformation.
`source_text(event)` is exactly `canonical_json(event.payload)`; native source ID is
`event.id`, revision is 1, and byte range `[0,len(source_text.encode('utf-8')))` points
to that immutable payload. Memory content is exactly:

```
Journal event {event.kind} from {event.source}:\n{canonical_json(event.payload)}
```

The native memory ID is `event:{event.id}`, with operation key `journal:{event.id}`.
Default allowed kinds are explicitly listed in `DEFAULT_EVENT_KINDS`; memory-generated
events/sources are excluded to prevent recursive ingestion. `replay(scope,limit=100)`
advances durable consumer `memory/native-v1` only after native acknowledgment or a
persisted explicit rejection. Missing transport acknowledgment leaves the cursor
unchanged. Oversized source events remain in the operational journal and get a
durable rejection record; they are never silently truncated or silently discarded.

`MemorySleep(journal,client,ingestor,timezone='UTC',consolidator=None,admission=None,
max_events=100,max_candidates=20,cycle_timeout=30)` runs through `await
run_cycle(scope,now=None)`. `now` is UTC epoch seconds used to select an IANA local
calendar day. One scoped durable cycle is recorded per timezone/day. Worker claims
expire after the cycle budget plus a margin; cancelled calls release their claim.
Repeat calls resume persisted phases, frozen source records, candidate lists and
operation keys. Parent conflicts start a fresh bounded attempt on a later invocation.
Publication is followed by native pruning at background admission priority; protected
leases remain in a durable prune queue for later cycles. Original evidence survives.

The optional admission callable receives `(scope,{kind:'memory.sleep',priority:
'background',cycle_id:...})` and must return `True`; runtime should bind its actual
scheduler authority. Without it, the explicitly invoked library cycle is authorized
by its caller. Sync callbacks run off-loop; async callback results are awaited.
The consolidator receives `(scope,source_records,cycle)` and returns at most
`max_candidates` native input records of kind observation/belief/procedure. Every
citation must resolve inside the frozen evidence ranges. Invalid candidates are
reported and excluded. The default consolidates exact repeated content without
inventing causal outcomes. Sleep never edits model weights or claims external task
completion. It is a manually/scheduler-invoked library mechanism, not an app daemon.
