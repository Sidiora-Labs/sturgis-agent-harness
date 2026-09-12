---
title: "Queries, lifecycle fencing and event replay"
description: "Load private data only after control state permits it and resume streams by cursor."
---

The client first queries control-plane agent metadata with key `["runtime", accountId, agentId]`. This read observes desired/observed lifecycle state without starting a private worker. Private reads are enabled only when a known control state permits waking.

```tsx
const runtime = useRuntimeState(accountId, agentId);
const files = useAgentQuery<{ path: string; entries: FileEntry[] }>(
  "files.list", { path: "." });
```

The second hook requires AgentContext. It checks the control guard both in React Query enablement and inside the query function, so manual invalidation cannot bypass it. The file listing retains the current path and its entries rather than pretending the response is a bare array.

## Suspend walkthrough

Before an explicit suspend action, `pauseRuntimeQueries` adds an account/agent pause marker and fences the cache. The application cancels private reads and stops event polling. A release removes the local marker but keeps cached reads fenced until a fresh control observation arrives.

If suspend fails, a fresh running observation can re-enable reads. If the control request fails, the old running observation cannot authorize background work. Initial control loading is a loading state, not a false private-query error.

The shell conversation list, page queries and Conversation's direct composer/event paths share this guard. Draft text may remain editable while sending, uploading or polling is blocked. Resume/start is explicit.

## Event replay

`useEvents` polls scoped public events after a cursor and optional conversation filter. It deduplicates by event ID, advances the server-provided cursor and immediately continues when more pages exist. Errors change connection state to reconnecting with a bounded retry delay.

Changing the agent/conversation aborts the old request and timer. Passing no conversation disables polling. The UI rebuilds display from actual persisted output and terminal events, not guessed streaming completions.

The API helper preserves the consumer envelope version, sends same-origin credentials and CSRF for bodies, and returns typed errors. Event receipt by the browser does not establish a tool's world effect. See [delivery](../integrations/channels-delivery.md) and [mutation identities](mutation-recovery.md).

## Implementation sources

- [frontend/src/hooks/useAgent.tsx](/reference/files/frontend/src/hooks/useAgent.tsx/)
- [frontend/src/hooks/useEvents.ts](/reference/files/frontend/src/hooks/useEvents.ts/)
- [frontend/src/lib/api.ts](/reference/files/frontend/src/lib/api.ts/)
- [frontend/src/App.tsx](/reference/files/frontend/src/App.tsx/)
- [frontend/src/pages/ConversationPage.tsx](/reference/files/frontend/src/pages/ConversationPage.tsx/)
- [consumer/api.py](/reference/files/consumer/api.py/)

[Developer manual](../index.md)
