---
title: "Troubleshoot without losing the evidence"
description: "Diagnose login, runtime, provider, content, connector, permission, and backup failures while preserving accepted requests."
---

Start with the narrowest failed boundary: browser/API access, owned runtime, provider, connection, or individual work. Record the error code, request identity, relevant agent/conversation/work ID, and current revision. Avoid copying secrets into a support transcript.

## Worked example: the page opens but the agent does not answer

1. Confirm the correct agent is selected.
2. Open **Settings → Agent & data** and inspect runtime state. If deliberately suspended, choose **Start runtime**.
3. Inspect the selected provider under Models & credentials. Probe its current configuration and credential revision if readiness is absent or expired.
4. Check the conversation's accepted input and **Work → Overview**. A request can be accepted while waiting for model admission.
5. If the acknowledgement is uncertain, use the retained retry identity; do not send a different effectful request under a fresh ID to make progress appear successful.

```sh
sturgis doctor --agent AGENT_ID
sturgis command AGENT_ID providers.list
sturgis command AGENT_ID work.list
sturgis command AGENT_ID diagnostics.get
```

Doctor's control health and local native result are distinct from the agent's live memory/provider observations.

## Common failures

| Symptom or code | What to inspect | Recovery |
| --- | --- | --- |
| `unauthenticated` | Session and exact server URL | Sign in again to that origin; do not copy another account's token. |
| `csrf_failed`, `origin_denied`, `host_denied` | Browser origin and reverse-proxy Host/Origin | Operator fixes the exact public origin/proxy; do not disable the check. |
| Bootstrap closed or signup absent | Whether an owner exists; operator signup setting | Use normal login or ask the operator about account registration. |
| `conflict`, `revision_conflict` | Current record and your expected revision | Reread and reconcile; use a new revision only for the intended current change. |
| `runtime_busy` during suspend/backup/archive | Active work, pending admission, undelivered output | Let it settle or request cancellation, then retry the settled operation. |
| `isolation_unavailable` | Hosted OS namespace and launcher requirements | Operator fixes the host; hosted workers do not silently downgrade. |
| `provider_unavailable` | Endpoint, model, current credential, tools setting, readiness | Correct configuration, probe, then select the model. |
| Memory unavailable | Native worker and ingestion/freshness status | Recover the runtime as needed; do not interpret failure as zero results. |
| Unsupported/deferred source | Media type, UTF-8 validity, extraction size | Keep original bytes; supply a supported smaller text source. |
| File precondition failure | Current SHA-256 and your edited copy | Reopen and reconcile; do not drop the hash protection. |
| `approval_required` | Exact rejected invocation | Inspect and approve once or deny in Permissions. |
| Stale tool/schema/credential | Current connection/tool binding | Review, regrant/reprobe, and obtain a new matching action request. |
| `browser_unavailable` | Typed namespace or virtual-address reason | Use a supported operator backend or another connector; retain confinement. |
| `model_unavailable` on media probe | Explicit media model in `/models` | Fill the current model field even though the UI labels it optional. |
| Webhook/device signature failure | Raw bytes, current revision, timestamp, nonce, secret | Fix signing; preserve identical retry content. |
| `restore_interrupted` | Unpublished failed restore identity | Start a deliberate new attempt with a new request identity. |

## When to recover or contact the operator

Use **Recover runtime** for a stuck worker after inspecting its state. Recovery interrupts execution and preserves unknown effects; it does not undo remote work. If a bound is exceeded, reduce the request or use supported ranges rather than raising unrelated limits.

A model output, child summary, or imported event cannot reconcile a missing operational observation. For ambiguous effects, preserve the original action/attempt identities and ask the integration operator for the supported evidence path. Consult [evidence](../work/evidence.md), [limits](limits.md), and [backup recovery](../settings/backup-restore.md) before deleting files or starting duplicate work.
