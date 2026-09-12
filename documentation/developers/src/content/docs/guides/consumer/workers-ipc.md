---
title: "Private workers and authenticated IPC"
description: "Keep agent scope fixed and distinguish local process isolation from hosted namespace boundaries."
---

WorkerManager owns local placement for one installation using an exclusive lock. Each agent has server-assigned state, workspace and private roots. Launch configuration fixes its scope, mode, executable paths and network policy. Public requests cannot supply those values.

Self-hosted mode runs a separate trusted local process under the operating-system user. Hosted mode adds user, mount, PID, IPC and UTS namespaces, private writable agent roots, read-only code/dependency mounts, dropped capabilities and process/resource limits. Missing isolation support fails closed.

**Hosted workers share the host network namespace.** NetworkPolicy constrains supported adapter traffic; it is not a blanket network sandbox for arbitrary code. Nested managed-browser availability is a separately observed capability.

## Private wire identity

WorkerClient sends bounded framed JSON over a private UNIX socket. The authenticated envelope includes:

```text
version, request_id, method, params, deadline, nonce, signature
```

HMAC covers the canonical unsigned envelope. The worker checks version, deadline, syntax and nonce before dispatch. The method/params digest binds request identity independently of the nonce: a fresh transport frame can ask for the same command receipt without authorizing changed content.

A private key belongs to the selected agent root. Nonces are bounded, expire with the replay window, and cannot be reused. Responses also authenticate the matching request identity. Do not expose private signing keys or generic internal commands through HTTP.

## Lost acknowledgment walkthrough

The worker records a running command before calling the application. A crash leaves it indeterminate rather than silently reexecuting it. A later same-identity command uses receipt lookup. Exact completed results can be replayed when retained; one-time secret responses may instead be unavailable, and that is not permission to repeat their mutation.

The worker's transport journal is distinct from the agent's operational journal. It records whether the application command was dispatched; the application/core journals record its domain effects and outputs. Debug both joins when a client has an unknown acknowledgment.

Resource ceilings include virtual address-space limits, not a claim of hard aggregate RSS containment. See [lifecycle](lifecycle.md), [network connections](network-connections.md) and [packaging](../operations/packaging.md).

## Implementation sources

- [consumer/workers.py](/reference/files/consumer/workers.py/)
- [consumer/worker.py](/reference/files/consumer/worker.py/)
- [consumer/config.py](/reference/files/consumer/config.py/)
- [consumer/contracts.py](/reference/files/consumer/contracts.py/)
- [agent/framing/__init__.py](/reference/files/agent/framing/__init__.py/)

[Developer manual](../index.md)

