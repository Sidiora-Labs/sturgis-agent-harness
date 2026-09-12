---
title: "Configure connection access and readiness"
description: "Use the shared connection workflow and understand why configuration, grants, probes, and action permissions are separate."
---

Connections give an agent a bounded interface to a service. A connection has an ID, adapter kind, endpoint, optional credential reference, options, grants, and readiness evidence. Naming an endpoint does not make it compatible or authorize all of its operations.

## The four steps

1. Open **Connections** and create a connection of the correct kind.
2. Enter its ID, HTTP endpoint, credential reference if needed, and supported options. Choose **Configure connection**.
3. Choose **Manage grants**, select the operation this connection may provide, and choose **Save grants**.
4. Choose **Test connection** and inspect its readiness evidence. Review model-action permissions separately in **Settings → Permissions**.

Connection editing resets the applicable authorization/readiness state. After editing, review grants and test the new configuration. **Choose access**, **Needs test**, **Ready**, **Unavailable**, **Credential changed**, and **Revoked** describe different conditions.

## Worked example: a read-only service

Create a Web fetch connection called `reference` to an HTTP endpoint you control or are authorized to use. Grant **Fetch web content**, test it, then configure the tool permission as appropriate. Ask the model to use that connection for a narrow read and inspect its actual result.

The CLI configuration shape is `connection.json`:

```json
{"id":"reference","kind":"http","endpoint":"https://reference.example.com/notes","options":{"allowed_origins":["https://reference.example.com"]}}
```

```sh
sturgis command AGENT_ID connections.configure --params-file connection.json
sturgis command AGENT_ID connections.list
```

Use `connections.get` with `{"id":"reference"}` to inspect one record. Grant via `connections.grant` with `id`, `operations:["fetch"]`, and current `expected_revision`; probe via `connections.probe` with the new current revision. Revoking uses `connections.revoke` with ID and revision. Update revision numbers after every change.

## Choose a supported kind

- [Web fetch and search](web-and-search.md): bounded page bytes or a specific search JSON protocol.
- [MCP app server](mcp.md): selected tools through a bounded HTTP MCP subset.
- [Managed browser](browser.md): rendered page inspection when an operator backend is available.
- [Speech and images](media.md): particular media-service request/response contracts.
- [Paired device](devices.md): a separate compatible device client with declared operations.
- [Incoming webhook](webhooks.md): signed external observations.

## Failure and precise limits

IDs are bounded to 64 characters. Endpoints require HTTP(S) without query-string credentials. Allowed origins are exact and bounded to 32; the configured endpoint's origin must be included. Private destinations also need the operator's exact-origin exception. A broader account-side list cannot bypass that operator policy.

There is no public `connections.execute` bypass. Model actions travel through the normal dispatcher with capabilities, permissions, budgets, and evidence. A readiness probe may call a remote service; catalog or pairing readiness does not guarantee every later operation succeeds. On stale configuration, changed credentials, or schema drift, inspect, reconfigure if necessary, regrant, and reprobe. Use [troubleshooting](../reference/troubleshooting.md) for network and authorization errors.

## Command examples

These are command-specific examples, not a script to run every operation. Replace uppercase IDs, example endpoints, timestamps, and revision numbers with the actual intended values. Save one JSON object as `params.json`, then run `sturgis command AGENT_ID METHOD --params-file params.json`. Omit the file for `{}`. If acknowledgement is unknown, keep request and domain operation identities unchanged. For an acknowledged unavailable result, follow the feature-specific recovery instructions.

| Method | Example parameters or private-prompt command |
| --- | --- |
| `connections.configure` | `{"id":"reference","kind":"http","endpoint":"https://reference.example.com/notes"}` |
| `connections.get` | `{"id":"reference"}` |
| `connections.grant` | `{"id":"reference","operations":["fetch"],"expected_revision":1}` |
| `connections.list` | `{}` |
| `connections.probe` | `{"id":"reference","expected_revision":2}` |
| `connections.revoke` | `{"id":"reference","expected_revision":3}` |
