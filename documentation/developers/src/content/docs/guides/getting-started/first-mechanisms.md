---
title: "Tutorial: durable state before agent behavior"
description: "Run the real journal, goal-budget and framing mechanisms in a disposable workspace."
---

Run the repository's executable tutorial from its root:

```sh
.venv/bin/python documentation/examples/first_mechanisms.py
```

It uses the real implementation and a disposable temporary directory. It does not require a provider account, browser service or external effect. A successful run prints:

```text
Tutorial passed: durable revision, conflict, owner scope, goal transition, idempotent usage, restart, fragmented frame and strict JSON.
```

## Follow the state changes

The tutorial creates a scoped journal record at revision one, updates it with the current preimage revision, and confirms revision two. A second writer using the stale revision receives `CoreError("conflict", ...)`. The value is not silently overwritten. Reading the same record ID through another scope returns no record.

This is the storage foundation of consumer compare-and-swap commands: the caller must observe the state it intends to change, and another concurrent decision can invalidate that observation.

## Follow the budget

A proposed goal has a token limit of 1,000. The script explicitly activates it, reserves 100 tokens, and records an actual 40-token usage receipt. Charging the same receipt identity again leaves total usage at 40.

The receipt identity prevents duplicate accounting after acknowledgment loss. The example does not imply the goal was achieved: no outcome criterion or operational evidence has been supplied. Budget consumption and task success remain separate facts.

## Restart and framing

Closing and reopening the SQLite journal confirms that revision two survives a real connection restart. The temporary directory is then removed by its context manager.

The framing section serializes finite canonical JSON, splits its length-prefixed bytes after only three header bytes, and feeds the fragments separately. No premature frame is emitted; the complete body appears only after the second fragment. Finally, duplicate JSON fields are rejected as `invalid_json`.

If an assertion fails, inspect that boundary rather than replacing the expected value with whatever happened. The script is an executable explanation of invariants used throughout the system. Continue with [the causality loop](../architecture/causality.md), [state transactions](../core/state.md) and [tool authoring](../integrations/tool-authoring.md).

## Implementation sources

- [documentation/examples/first_mechanisms.py](/reference/files/documentation/examples/first_mechanisms.py/)
- [agent/state/__init__.py](/reference/files/agent/state/__init__.py/)
- [agent/goals/__init__.py](/reference/files/agent/goals/__init__.py/)
- [agent/framing/__init__.py](/reference/files/agent/framing/__init__.py/)
- [agent/protocol/__init__.py](/reference/files/agent/protocol/__init__.py/)

[Developer manual](../index.md)

