---
title: "Retrieval, time and embedding spaces"
description: "Understand exact scanning, temporal validity, scoring and explicit partial results."
---

Native retrieval combines lexical token overlap, concept overlap and cosine similarity by addition. A missing component contributes zero. An empty query lists valid memories. Scores are deterministic for a given view; ties sort by record ID.

The embedding host invokes this function with its real `NativeMemoryClient`, trusted scope and compatible embedding object. The client owns the native subprocess and starts it on demand; this function does not replace its transport or execute merely by being defined.

```python
async def retrieval_integration_example(memory, scope, embedding):
    result = await memory.query(
        scope,
        text="compiler dependency",
        concepts=["build"],
        embedding=embedding,
        limit=10,
        timeout=0.2,
    )
    if result["status"] in {"ok", "partial"}:
        for match in (result.get("data") or {}).get("records", []):
            print(match["record"]["id"], match["score"])
    return result
```

The embedding must come from an explicitly selected compatible representation. The native worker does not call an embedding provider or learn model weights. A model ID binds to one dimension within a scope; vectors from different spaces are not compared as if their coordinates meant the same thing.

An unknown or incompatible space produces a partial response. Lexical and concept matches remain usable. A 10,000-record candidate scan bound reports truncation; it is not approximate-nearest-neighbor indexing. Query limits default to 20 and are bounded at 100. Large serialized responses can also become a `response_size` partial result.

## Time has two meanings

`at` selects validity within `[valid_from, valid_to)`, defaulting to the current time. It does not ask what the agent knew at an earlier moment. The latest canonical revision wins even if expired; query does not substitute an older revision because it happens to fit the requested time. Use `get_revision` explicitly for historical revision inspection.

Live retrieval combines current canonical revisions and the selected published derived generation. Fresh ingestion becomes eligible immediately. A lease instead selects a frozen generation snapshot. The response's source watermark and current top-level watermark expose the difference.

## Model context

`memory_blocks(scope, result)` checks scope and retraction, returns attributed user-role memory blocks, and records freshness/deferred status. Native unavailability must not become “the agent has no relevant memory.” Foreground context uses the last scoped observation cache without waiting for retrieval.

For stronger snapshot requirements, read [generations and leases](generations-leases.md). For source interpretation and correction, see [consumer content](../consumer/content-work.md).

## Implementation sources

- [native/memory/engine.cpp](/reference/files/native/memory/engine.cpp/)
- [agent/memory/client.py](/reference/files/agent/memory/client.py/)
- [agent/context/__init__.py](/reference/files/agent/context/__init__.py/)

[Developer manual](../index.md)
