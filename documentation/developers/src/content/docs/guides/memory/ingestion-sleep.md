---
title: "Journal ingestion and daily memory sleep"
description: "Advance only acknowledged input and consolidate with bounded evidence."
---

MemoryIngestor transforms selected immutable journal events into native records. The default allowlist includes user prompts/steering, awareness observations, tool results, reconciliations, goal and commitment changes, and persisted output. Memory-generated events and sources are excluded to avoid recursive ingestion.

The source body is exactly canonical JSON of the event payload. Its source ID is the journal event ID, revision one, with a byte range spanning that immutable payload. The native record ID is `event:<event-id>`; the operation ID is `journal:<event-id>`.

```python
from agent.memory import MemoryIngestor, MemorySleep


async def memory_sleep_integration_example(journal, memory, scope):
    ingestor = MemoryIngestor(journal, memory)
    replay = await ingestor.replay(scope, limit=100)
    if replay["status"] not in {"ok", "partial"}:
        return {"replay": replay, "cycle": None}
    sleep = MemorySleep(journal, memory, ingestor, timezone="Europe/Berlin")
    cycle = await sleep.run_cycle(scope)
    return {"replay": replay, "cycle": cycle}
```

The embedding host invokes this library function with its real Journal, NativeMemoryClient and trusted scope, and owns their lifetimes. It must obtain actual background admission before invoking the cycle; defining the function does not schedule it. A runtime should bind that admission rather than allowing unbounded independent sleep jobs.

## Durable progress

The ingestion cursor advances after native acknowledgment, a durable explicit rejection, or an intentional skip of an excluded event. It is a journal-processing cursor, not the native-ingested watermark. A missing transport acknowledgment for an eligible event leaves the cursor unchanged so the exact operation ID can reconcile a committed native mutation. Oversized events remain in the journal and receive rejection records; they are not silently truncated.

Sleep records one scoped cycle per local calendar day and timezone. Its phases, source snapshot, candidate list, stage index, operation IDs and publication progress survive interruption. A claimed cycle has a bounded lease. Cancellation releases its owner; parent conflict starts a fresh bounded attempt on a later invocation.

## What consolidation can change

The default consolidator merges exact repeated content. An injected consolidator receives frozen source records and the cycle, then returns at most the configured candidate bound. Derived records must be observation, belief or procedure kinds with citations contained in the frozen evidence ranges. Invalid candidates are reported and excluded.

After publication, bounded pruning runs at background priority. Protected leases remain in a durable prune queue for later cycles. The runtime can preempt background sleep for foreground work while preserving recorded phases.

Sleep optimizes recall; it does not change model weights, grant authority, publish source code or fulfill operational goals. The publication contract is detailed in [generations](generations-leases.md).

## Implementation sources

- [agent/memory/ingestion.py](/reference/files/agent/memory/ingestion.py/)
- [agent/memory/sleep.py](/reference/files/agent/memory/sleep.py/)
- [agent/runtime/__init__.py](/reference/files/agent/runtime/__init__.py/)

[Developer manual](../index.md)
