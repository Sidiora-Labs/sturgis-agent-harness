---
title: "Connect an MCP app server"
description: "Allow exact remote tool names through the supported HTTP MCP subset and recover when catalogs or schemas change."
---

An MCP connection lets the agent call selected tools on a compatible app server. You choose exact tool names and the service endpoint; Sturgis pins the observed catalog so that later changes cannot silently broaden what was approved.

## Prerequisites

The service must implement the supported HTTP JSON-RPC tools protocol version `2025-11-25`. Have its endpoint, any required credential, and the exact remote tool names. This release does not provide stdio MCP transport, a general resources/prompts client, paginated tool catalogs, or a persistent server-initiated stream.

The adapter initializes a session, sends the initialized notification, lists tools, and calls selected tools. It supports an optional session header and best-effort session deletion, with JSON or one bounded SSE response. A familiar vendor name does not certify compatibility.

## Configure the connection

Choose **App server (MCP)** in Connections. Enter the endpoint and credential reference. In **Allowed app tools**, enter exact names separated by commas, such as `search_notes, read_document`, only if your server actually offers them. Save, grant **Call the allowed app tools**, and test readiness.

The grant exposes the connection operation `call`. The remote name and arguments remain validated against the pinned selected catalog. Model-action permission is still required separately.

## Worked example: a notes service

Save `notes-mcp.json` with a real compatible endpoint:

```json
{"id":"notes","kind":"mcp","endpoint":"https://notes.example.com/mcp","options":{"allowed_tools":["search_notes","read_document"]}}
```

```sh
sturgis command AGENT_ID connections.configure --params-file notes-mcp.json
```

Grant `operations:["call"]` with the returned revision, then test the resulting revision. Inspect `connections.get` for the readiness catalog hash and selected tools. Ask the model:

> Search the notes app for maintenance procedures. Read the matching document only if the search gives a specific document identifier, and identify the source in your summary.

Expected results come from the actual tools and preserve external-evidence labels. This prompt does not authorize unrelated remote tools or arbitrary app mutations.

## Limits and recovery

Allowed tool names are bounded to 1–32 entries, each 1–128 characters. An individual MCP call's serialized arguments are bounded to 64 KiB. Remote schemas must fit the supported bounded validation profile; executable, recursive-reference, pattern, and combinatorial schema features are rejected rather than evaluated without limit.

If the remote catalog changes, the call can fail with `stale_remote_schema` before effects. Review the new selected catalog and reprobe deliberately. `mcp_tool_denied` means the requested remote tool is outside the connection grant. A remote result with an invalid shape or an ambiguous network failure may leave effects uncertain; inspect the original request and response before another attempt.

Readiness proves the selected catalog was observed. It does not prove each tool is harmless, always available, or semantically correct. Keep [permissions](../settings/permissions.md) and [evidence](../work/evidence.md) in the workflow, especially for app tools that modify external state.
