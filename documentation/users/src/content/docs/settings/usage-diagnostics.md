---
title: "Inspect usage and runtime diagnostics"
description: "Read actual consumption, outstanding reservations, overrun evidence, provider readiness, and runtime failures without inventing cost certainty."
---

Usage answers what resources have recorded consumption and what remains reserved or unresolved. Diagnostics answers what the runtime currently observes about itself. Neither is a provider invoice, a complete remote-service audit, or proof that every tool works.

## Read usage in Settings

Open **Settings → Models & credentials** and inspect usage evidence alongside provider configuration. Distinguish actual resource receipts from active allocations, unresolved claims, and overruns. Parent/child resource relationships are not extra billable charges to sum again.

The `usage.summary` projection includes `actual`, `receipt_count`, `direct_work_usage`, `unresolved_resources`, `unresolved_claims`, `overruns`, and `active_allocations`. Current monetary estimation is `cost_estimate:null`, with settlement `not_configured`. Missing or unknown usage must not be presented as zero cost.

## Worked example: investigate a timeout reservation

After a model or tool operation times out, run:

```sh
sturgis command AGENT_ID usage.summary
sturgis command AGENT_ID work.list
sturgis command AGENT_ID diagnostics.get
```

Identify the work ID attached to an unresolved claim and inspect it with `work.get`. Compare recorded actual consumption with the held allocation. The expected result may remain unresolved if no trustworthy terminal usage observation exists. Repeating the same request with a new ID just to clear the reservation can create another operation.

If a budget overrun is recorded, do not rewrite the allocation to make previous consumption disappear. Review the intended allowance for future work and the source of the overrun.

## Inspect runtime state

Open **Settings → Agent & data → Runtime diagnostics**. It reports selected scope, paused admission, native memory availability, recent runtime failures, capabilities, and mode. It is a bounded recent view, not a complete historical log export.

```sh
sturgis doctor --agent AGENT_ID
```

Doctor combines local native-binary availability, HTTP health, authentication, and an owned agent diagnostic command. A remote CLI machine's local-native result is about that CLI installation; it is not a substitute for the server runtime's memory observation. `GET /api/v1/health` establishes control-process health only.

## What to do next

If memory is unavailable, inspect the runtime/native process before assuming a query returned no records. If provider readiness is stale, refresh the current model configuration. If a tool is absent from capabilities, review connection grants, readiness, and [permissions](permissions.md). If the runtime is deliberately suspended, start it rather than treating paused private queries as a network outage.

**Recover runtime** can replace a stuck process, but it interrupts current work and preserves unresolved effects. Use [lifecycle recovery](lifecycle.md) only after inspecting the problem. Export a [backup](backup-restore.md) through the settled snapshot path for portable content/history; diagnostics does not replace that archive or a provider's own usage records.

## Command examples

These are command-specific examples, not a script to run every operation. Replace uppercase IDs, example endpoints, timestamps, and revision numbers with the actual intended values. Save one JSON object as `params.json`, then run `sturgis command AGENT_ID METHOD --params-file params.json`. Omit the file for `{}`. If acknowledgement is unknown, keep request and domain operation identities unchanged. For an acknowledged unavailable result, follow the feature-specific recovery instructions.

| Method | Example parameters or private-prompt command |
| --- | --- |
| `diagnostics.get` | `{}` |
| `overview` | `{}` |
| `usage.summary` | `{}` |
