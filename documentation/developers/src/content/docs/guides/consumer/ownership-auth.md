---
title: "Accounts, ownership and human permissions"
description: "Authenticate a person before resolving an agent, then constrain every effect."
---

Accounts and agent ownership live in the control plane. A private worker's scope comes from its launch configuration. Public requests never choose a scope, runtime root or host path. Even a caller-constructed Principal object is revalidated against the current session and user records before it confers authority.

Passwords use salted scrypt. Opaque login and CSRF tokens are minted only at authentication; durable records hold their digests. Login attempts have a bounded rate window. Bootstrap closes once the first account exists, signup requires operator enablement, and logout revokes the current session.

## Browser and CLI requests

The server checks the exact configured Host and Origin, rejects duplicate authentication/CSRF headers and duplicate session-cookie names, and requires strict bounded JSON. Browser sessions are HttpOnly with SameSite strict. Cookie-authenticated mutations require the matching `X-CSRF-Token` and installation origin. Bearer authentication takes the explicit CLI/API path and still performs owner authorization.

For every agent command, ownership is checked before the lifecycle layer can wake or resolve a worker. An administrator flag does not automatically make another owner's private agent accessible through AgentDirectory.

## Tool policy is a second boundary

Permissions wraps the existing connection and scheduler authorization callback. Baseline content/workspace reads are allowed; other registered capabilities default to ask. Policies are revisioned and pinned to the current descriptor, tool version and connection/credential binding. A stale policy becomes denying until deliberately refreshed.

Suppose a model requests `workspace.write` under ask policy. Authorization rejects the attempt with structured approval evidence. The surrounding transaction rolls back its claim, but ToolDispatcher persists the rejection result outside it. Permissions projects that durable rejection, including artifact-backed call details when necessary.

An authenticated owner can approve that exact request for a bounded lifetime. The one-use grant binds the call digest and current policy/configuration versions, and is consumed atomically with a future exact invocation. Approval does not automatically retry an old failed turn or mark a goal successful.

Revocation fences affected active/queued work and schedules and refreshes future capabilities. Already uncertain external effects remain uncertain. See [tool reconciliation](../integrations/tool-effects.md), [network grants](network-connections.md) and [client retry identities](../frontend/mutation-recovery.md).

## Implementation sources

- [consumer/identity.py](/reference/files/consumer/identity.py/)
- [consumer/api.py](/reference/files/consumer/api.py/)
- [consumer/permissions.py](/reference/files/consumer/permissions.py/)
- [consumer/application.py](/reference/files/consumer/application.py/)

[Developer manual](../index.md)

