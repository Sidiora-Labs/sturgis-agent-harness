---
title: "Architecture and process boundaries"
description: "Understand the Python control layer, persistent core and C++ memory worker."
---

The product has a shared consumer control service, private agent workers, and a native memory subprocess per agent runtime. The browser and CLI use one HTTP contract. They do not create a separate agent loop.

The control service authenticates people, resolves owned agents, records lifecycle intent and routes bounded commands to a fixed private worker. `AgentApplication` composes product services over a `CoreRuntime`. The core supervises admission, model work, delivery, heartbeats and memory. The C++ worker stores scoped memory records and retrieval projections; it does not execute tools or decide whether a goal was achieved.

## Three kinds of durable truth

The operational journal holds causal events, revisioned records, work grants and output receipts. The artifact store holds typed immutable bytes and verifies their hashes. Native memory holds evidence-linked recall and derived generations. These stores answer different questions:

- Journal: what did this execution authorize, observe and commit?
- Artifact: which exact bytes did this reference identify?
- Memory: which relevant, temporally valid records can help the next request?

A retrieved belief cannot overwrite an operational receipt. A memory generation cannot activate source code. A model's fluent explanation cannot convert an unknown tool effect into an observed one.

## Follow one failure

If a browser mutation fails before ownership authorization, no worker should be started. If a private IPC acknowledgment is lost, consult the command receipt rather than resending the effect. If the model call fails, inspect its terminal state and persisted partial text. If the memory subprocess is unavailable, foreground input and output remain independent and memory freshness becomes explicit.

Hosted workers enforce filesystem and process namespace boundaries but share the host network. Network restrictions for adapters are a separate policy in the consumer layer. See [workers and IPC](../consumer/workers-ipc.md) and [network connections](../consumer/network-connections.md).

Read [module ownership](module-ownership.md) before adding another controller, and [the causality loop](causality.md) before adding a new effect path.

## Implementation sources

- [agent/runtime/__init__.py](/reference/files/agent/runtime/__init__.py/)
- [consumer/api.py](/reference/files/consumer/api.py/)
- [consumer/application.py](/reference/files/consumer/application.py/)
- [consumer/workers.py](/reference/files/consumer/workers.py/)
- [native/memory/main.cpp](/reference/files/native/memory/main.cpp/)

[Developer manual](../index.md)

