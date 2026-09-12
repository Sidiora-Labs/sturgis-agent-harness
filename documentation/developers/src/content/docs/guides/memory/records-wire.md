---
title: "Native memory records and wire protocol"
description: "Construct evidence-linked records and communicate with the actual C++ subprocess."
---

The native process runs as `sturgis-memory --db PATH`. Each request is one four-byte big-endian length plus UTF-8 JSON, bounded to 1,048,576 payload bytes. Nesting is bounded to 32 levels; duplicate keys, unknown fields, wrong types and nonfinite numbers fail.

A complete request has exactly these envelope fields:

```json
{
  "protocol_version": "memory/1",
  "request_id": "request-1",
  "operation": "ingest",
  "scope": "demo",
  "arguments": {
    "operation_id": "ingest-build-42",
    "expected_revision": 0,
    "record": {
      "id": "build-42",
      "kind": "experience",
      "content": "Build 42 exited successfully.",
      "sources": [{"id": "observed-build-42", "revision": 1, "start": 0, "end": 15}],
      "source_time": 100,
      "observed_at": 101,
      "valid_from": 100,
      "valid_to": null,
      "concepts": ["build", "project"]
    }
  }
}
```

This illustrates wire shape. An integration must substitute an actual retained source and its exact byte interval; the example's narrative is not execution evidence.

Record kinds are `fact`, `experience`, `observation`, `belief` and `procedure`. Content is nonempty and at most 32,768 bytes. Records require 1–64 source references, each with a positive revision and nonempty byte interval. Concepts are bounded unique IDs. An optional embedding supplies a model ID and 1–2048 finite coordinates with nonzero norm.

## Results and retries

Responses carry protocol version, request ID, status, data, error, generation and current source watermark. Status is `ok`, `partial`, `unavailable`, `conflict` or `invalid_request`. Keep a partial result partial even if some records are useful.

Mutations other than lease release require an operation ID. An exact retry returns its stored mutation result; reuse with changed arguments conflicts. Failed operations do not reserve the identity. Transport request IDs correlate messages; operation IDs deduplicate effects across transport loss.

`retract` accepts `operation_id`, `id`, `reason` and optional `revision`. Omitting the revision suppresses every existing and future revision of that identity, including records in leased views. Supplying a revision limits the retraction to that revision. No public undo operation exists; the consumer also rejects corrections to an already retracted record. Retraction preserves historical records and provenance rather than deleting the evidence.

Use `NativeMemoryClient` for ordinary Python integration. It serializes frame writes, correlates replies with a dedicated reader, drains late replies and reports whether an unavailable request was dispatched. Closing a client is terminal; instantiate another to reopen the database. See [retrieval](retrieval.md) and [publication](generations-leases.md).

## Implementation sources

- [native/memory/PROTOCOL.md](/reference/files/native/memory/PROTOCOL.md/)
- [native/memory/main.cpp](/reference/files/native/memory/main.cpp/)
- [native/memory/engine.cpp](/reference/files/native/memory/engine.cpp/)
- [agent/memory/client.py](/reference/files/agent/memory/client.py/)

[Developer manual](../index.md)
