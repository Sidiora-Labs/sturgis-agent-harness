---
title: "Mutation identities and unknown acknowledgments"
description: "Retain only request hashes and identities across browser retries."
---

A browser error may arrive after the server accepted a mutation. Generating another request ID on every click would turn retry into a second effect.

`useAction` snapshots JSON parameters, canonicalizes object-key order and hashes the semantic body with SHA-256. It stores only version, digest and request ID in sessionStorage, keyed by account, agent and method. It never stores credential values, archive bytes or raw request bodies.

```tsx
const action = useAction();
await action.execute(
  "goals.edit",
  { id: goal.id, expected_revision: goal.revision, objective: nextObjective },
  "Goal updated.",
);
```

The page supplies current values. The hook owns transport identity and prevents concurrent same-method mutation races.

## Lost acknowledgment walkthrough

The first call persists its identity before dispatch. If fetch fails or the worker reports an uncertain outcome, the receipt remains. A later call with identical semantic parameters reuses that identity, including after a page remount. Changed values are blocked until the previous outcome is reconciled.

A denied retry does not necessarily settle the original attempt. Authentication, CSRF, origin, scope, archived-agent and related failures may happen before saved receipt lookup; a retained unknown request survives those errors. Definite success or a definite post-lookup failure can release it. There is no automatic mutation retry.

If recovery storage or WebCrypto is unavailable, the hook refuses to send an untrackable mutation. It does not silently fall back to a fresh ID.

## Domain identities

For attachments upload, knowledge add/delete, and memory correct/retract, the hook assigns `operation_id` from the retained request identity. Pages must not regenerate it independently. Conversation sending has a dedicated pending-message identity and room-creation flow, preserving the original room after an uncertain acknowledgment.

An acknowledged native-unavailable result is distinct from an unknown HTTP acknowledgement. The current hook clears its transport recovery entry after a successful envelope, even when knowledge indexing or memory returns an unavailable native status. The completed outer request cannot resume native work: its receipt only replays that result. A backend or CLI recovery path must retain the original domain `operation_id` and parameters, then use a new outer `request_id` after native recovery. The hook does not expose separate operation-ID control for this path; do not describe another generic UI click as resuming the same domain operation. See [content recovery](../consumer/content-work.md#failure-interpretation).

Restore has its own account-scoped recovery identity bound to the chosen name and archive hash. A completed restore clears it; `restore_interrupted` permits a new explicit identity. Unknown outcomes retain it across remounts and denied retries.

Read [private receipts](../consumer/workers-ipc.md) and [backup publication](../consumer/backup-restore.md) before changing these semantics.

## Implementation sources

- [frontend/src/hooks/useAgent.tsx](/reference/files/frontend/src/hooks/useAgent.tsx/)
- [frontend/src/pages/ConversationPage.tsx](/reference/files/frontend/src/pages/ConversationPage.tsx/)
- [frontend/src/pages/SettingsPage.tsx](/reference/files/frontend/src/pages/SettingsPage.tsx/)
- [frontend/src/lib/api.ts](/reference/files/frontend/src/lib/api.ts/)
- [consumer/lifecycle.py](/reference/files/consumer/lifecycle.py/)
- [consumer/portability.py](/reference/files/consumer/portability.py/)

[Developer manual](../index.md)
