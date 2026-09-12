---
title: "Developer manual"
description: "Build, extend and operate the Python agent, native memory worker and consumer application."
---

Sturgis combines a Python agent core, a C++ memory process, and a consumer application with a React client. The model participates through bounded, labeled requests. Persistent operational records establish what was requested, which execution was authorized, what a backend observed, and what reached a transport.

Start with [local setup](getting-started/setup.md), then follow [the complete causality loop](architecture/causality.md). A tool author should read [tool registration](integrations/tool-authoring.md) and [uncertain effects](integrations/tool-effects.md) together: returning useful data and establishing that an external effect happened are separate responsibilities.

For example, adding a read-only search capability starts with the existing connection adapter contract, then its exact network-origin grant, tool schema and owner policy. A successful HTTP response becomes a typed observation; a browser result uses the same command and recovery path as other capabilities. The relevant sequence is [network connections](consumer/network-connections.md), [tool authoring](integrations/tool-authoring.md), [human permissions](consumer/ownership-auth.md), and [qualification](getting-started/qualification.md).

## Choose a development path

| Your change | Read first |
| --- | --- |
| New capability or backend | [Tools](integrations/tool-authoring.md), [network boundaries](consumer/network-connections.md), [permissions](consumer/ownership-auth.md) |
| Memory behavior | [Wire records](memory/records-wire.md), [retrieval](memory/retrieval.md), [publication](memory/generations-leases.md) |
| Agent reasoning or work coordination | [Goals](behavior/goals-budgets.md), [plans](behavior/commitments-plans.md), [attention](behavior/initiative-attention.md), [scheduler](core/scheduler.md) |
| Web or CLI behavior | [HTTP commands](consumer/http-commands.md), [frontend data flow](frontend/data-events.md), [mutation recovery](frontend/mutation-recovery.md) |
| Installation or incident response | [Packaging](operations/packaging.md), [debugging](operations/debugging.md), [release boundaries](operations/release-migration.md) |

These guides explain current mechanisms and the reasons behind their boundaries. The linked source reference supplies exact definitions and complete signatures. Examples labeled as embedding code run inside a trusted application; they are not additional public HTTP endpoints or capabilities granted to a model.

The Linux implementation has local and hosted worker modes. Hosted workers share the host network namespace; the boundary is described precisely in [private workers](consumer/workers-ipc.md). Local HTTP fixtures and actual process tests establish implementation behavior, not universal compatibility with remote vendors.

## Implementation sources

- [agent/runtime/__init__.py](/reference/files/agent/runtime/__init__.py/)
- [consumer/application.py](/reference/files/consumer/application.py/)
- [spec/sturgis-02/contracts.md](/reference/files/spec/sturgis-02/contracts.md/)
