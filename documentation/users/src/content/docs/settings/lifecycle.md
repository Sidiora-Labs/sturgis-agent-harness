---
title: "Start, suspend, recover, and archive an agent"
description: "Control runtime admission while preserving accepted work, scheduled wake, and unresolved outcomes."
---

An agent's saved identity and content outlive its current worker process. Runtime controls manage that process and whether new work may enter it. Agent archive is a stronger durable execution fence; it is not a synonym for stopping or suspending a worker.

## Use runtime controls

Open **Settings → Agent & data**. **Start runtime** starts or resumes private runtime access. **Suspend runtime** requires settled work and preserves scheduled wake. **Recover runtime** replaces the current process and records interruption rather than pretending in-flight work completed.

The web client consults control-plane status before background private reads. While suspended, these queries pause. Starting the runtime re-observes status before re-enabling them. CLI commands that need private runtime data can wake a suspended agent, so avoid running diagnostic/content commands if your goal is to keep it dormant.

## Worked example: suspend between sessions

Finish or cancel the active conversation and inspect **Work → Overview**. Choose **Suspend runtime**. Expect a suspended runtime state and, if applicable, a visible **Next scheduled wake**. A future schedule can still wake the agent; suspend does not cancel that schedule.

To inspect state without requesting a worker domain read, use the lifecycle status route or agent metadata:

```sh
sturgis agents get AGENT_ID
```

The HTTP lifecycle actions are POST `/api/v1/agents/AGENT_ID/lifecycle/start`, `/status`, `/suspend`, and `/recover`, each with `{}` and normal owner authentication. The CLI does not provide a separate generic lifecycle subcommand; use the web controls or the authenticated API. `overview` and `diagnostics.get` are worker commands and may wake a suspended agent.

## Archive deliberately

Use the archive control in Agent & data and confirm only when you intend to end runtime execution for that agent. Archive checks for quiescent/settled work, updates owned metadata and lifecycle state together, clears scheduled wake, and rejects subsequent worker commands. Metadata and supported archival inspection can remain available.

CLI archive requires the current metadata revision:

```sh
sturgis agents get AGENT_ID
sturgis agents archive AGENT_ID --revision 3
```

Replace `3` with the observed revision. There is no unarchive action in this release. Archive does not delete history. A fresh restore from a valid [backup](backup-restore.md) creates another owned agent with live execution authority disconnected.

## Recovery and limits

`runtime_busy` means accepted work, pending command admission, or undelivered output prevents a settled transition. Inspect and settle the work instead of killing the process to claim a successful suspend. An unknown external effect survives recovery as unknown and is not automatically replayed.

One installation root has one placement owner. Operators should stop the existing service before upgrades or a second server launch. A control restart recovers durable placement and interrupted work; it is not distributed multi-host orchestration. Hosted namespace failures remain explicit rather than falling back to an ordinary process. See [troubleshooting](../reference/troubleshooting.md) for diagnosis without losing the original request identities.
