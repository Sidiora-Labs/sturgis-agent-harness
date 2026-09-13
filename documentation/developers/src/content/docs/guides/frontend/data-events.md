---
title: "Queries, lifecycle fencing and event replay"
description: "Load private data only after control state permits it and resume streams by cursor."
---

The client first queries control-plane agent metadata with key `["runtime", accountId, agentId]`. This read observes desired/observed lifecycle state without starting a private worker. Private reads are enabled only when a known control state permits waking.

```tsx
const runtime = useRuntimeState(accountId, agentId);
const files = useAgentQuery<{ path: string; entries: FileEntry[] }>(
  "files.list",
  { path: "." },
);
```

The second hook requires AgentContext. It checks the control guard both in React Query enablement and inside the query function, so manual invalidation cannot bypass it. The file listing retains the current path and its entries rather than pretending the response is a bare array.

## Suspend walkthrough

Before an explicit suspend action, `pauseRuntimeQueries` adds an account/agent pause marker and fences the cache. The application cancels private reads and stops event polling. A release removes the local marker but keeps cached reads fenced until a fresh control observation arrives.

If suspend fails, a fresh running observation can re-enable reads. If the control request fails, the old running observation cannot authorize background work. Initial control loading is a loading state, not a false private-query error.

The shell conversation list, page queries and Conversation's direct composer/event paths share this guard. Draft text may remain editable while sending, uploading or polling is blocked. Resume/start is explicit.

## Event replay

`useEvents(agent, conversation, {enabled=true})` polls scoped public events after the server-provided cursor. It returns `events`, `connection`, `loading`, and `error`. Its replay state holds the agent/conversation identity, accumulated events, cursor, seen IDs, and a `caughtUp` flag. Event IDs deduplicate pages, and `has_more` schedules the next page immediately. The first page with `has_more:false` marks initial catch-up complete; only then can the connection become `live`. A successful first HTTP response containing an incomplete page is not proof that history is empty or current.

`caughtUp` stays latched for that replay identity. Later pages with `has_more:true` are fetched immediately but do not reset `loading` or change a successful connection back to idle. Thus `live` records successful polling after initial catch-up; it does not guarantee that every subsequent backlog has already been drained.

The server applies its public-kind and authorized binding filters before the page limit. An exhausted page can advance its cursor past hidden maintenance events without returning them. The hook uses that exact cursor even when no items are visible; it does not derive progress from event counts. See [routed snapshot pages](../core/state.md#indexed-routed-event-pages).

Changing the agent or conversation aborts the previous request and timer and resets the cursor, deduplication set, and readiness. A render-time identity check suppresses the previous room's messages before the resetting effect runs. Passing no conversation disables polling. Setting `enabled:false` instead pauses the same room: it aborts reads while retaining loaded events and cursor, and reports an idle connection. Resuming continues from that cursor. This in-memory preservation is for the current replay identity, not a persistent browser cache of every visited room.

Polling failures retain accumulated messages, set `connection:"reconnecting"`, expose the API error, and retry after 1,200 ms. A successful page clears the error. The lifecycle guard supplies `enabled`; a paused runtime does not keep polling or show an active reconnect banner.

`ConversationPage` renders **Opening your conversation…** while the selected room's initial replay is pending, or **Waiting to load conversation history…** when private reads are unavailable. The welcome view appears for a loaded, genuinely empty conversation rather than substituting for an unfinished replay. Already loaded messages remain visible through catch-up, reconnection, and lifecycle pauses. During an enabled replay failure the alert explains whether it is showing loaded messages or reconnecting to load saved messages. Pausing states that updates are paused and the loaded messages and draft remain. The UI rebuilds display from persisted output and terminal events; replay does not infer missing completion or re-execute effects.

The API helper preserves the consumer envelope version, sends same-origin credentials and CSRF for bodies, and returns typed errors. Event receipt by the browser does not establish a tool's world effect. See [delivery](../integrations/channels-delivery.md) and [mutation identities](mutation-recovery.md).

## Implementation sources

- [frontend/src/hooks/useAgent.tsx](/reference/files/frontend/src/hooks/useAgent.tsx/)
- [frontend/src/hooks/useEvents.ts](/reference/files/frontend/src/hooks/useEvents.ts/)
- [frontend/src/lib/api.ts](/reference/files/frontend/src/lib/api.ts/)
- [frontend/src/App.tsx](/reference/files/frontend/src/App.tsx/)
- [frontend/src/pages/ConversationPage.tsx](/reference/files/frontend/src/pages/ConversationPage.tsx/)
- [consumer/api.py](/reference/files/consumer/api.py/)
- [consumer/application.py](/reference/files/consumer/application.py/)
- [agent/state/**init**.py](/reference/files/agent/state/__init__.py/)

[Developer manual](../index.md)
