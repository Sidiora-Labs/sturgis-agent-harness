---
title: "Network policy and revisioned connections"
description: "Pin DNS decisions to actual sockets and separate configuration, grants and readiness."
---

Connections describe exact adapter kinds, endpoints, options and credential references. Granting operations, probing readiness and authorizing execution are separate revisioned decisions. A saved connection does not make every capability ready.

For a search integration, configure the endpoint and bounded `max_results`, grant only `query`, and probe the current revision. The endpoint must accept GET with `q` and `limit` and return:

```json
{"results":[{"title":"Build documentation",
             "url":"https://example.org/build",
             "snippet":"The documented build procedure."}]}
```

The adapter validates exact result fields and bounds. This is a protocol example, not a claim that the example domain supplies that service.

## NetworkPolicy

NetworkPolicy validates an exact granted origin, resolves DNS, validates every destination address, and connects to a chosen literal IP while preserving the original Host and TLS server name. This closes the gap between checking a hostname and later connecting to a different DNS answer. It disables ambient proxy configuration.

Private, loopback and otherwise non-public addresses require an explicit operator origin exception. That exception does not grant all connections access to the origin. Connection policy still limits destinations.

Default limits are 8 MiB, 30 seconds, three redirects and four concurrent requests. The deadline includes waiting and DNS/body work. Redirects are limited to GET/HEAD, must stay within granted origins, and lose credentials across origins. Identity encoding is requested; compressed bodies are rejected. Streams are bounded and consumed once.

## Adapter compatibility

| Adapter | Implemented subset |
| --- | --- |
| `http` | Granted bounded GET returning status, URL, media type and base64 bytes; fetch body is limited to 64 KiB. |
| `search` | The exact GET/result-list contract above; 1–20 configured results. |
| `mcp` | HTTP JSON-RPC tools protocol `2025-11-25`; initialize, initialized, tools/list and tools/call. Exact allowed tool names and catalog hash are pinned. |
| `stt`, `tts`, `image` | Explicit model catalog and media endpoints; see [media and browser](../integrations/media-browser.md). |
| `browser` | Managed local Chromium with mediated HTTP; see [media and browser](../integrations/media-browser.md). |
| `webhook`, `device` | Signed ingress and paired remote operations; see [external devices](../integrations/external-devices.md). |

The MCP adapter accepts JSON or one bounded SSE response. It does not implement arbitrary resources/prompts, stdio transport, paginated catalogs or persistent server-initiated streams. Remote schemas use a deliberately bounded profile that rejects executable, recursive or combinatorial constructs.

Execution rechecks connection revision, grants and credential currency in the canonical authorization transaction. An endpoint edit or key rotation invalidates prior readiness. This policy is not host-wide network isolation; hosted workers share the host network namespace.

## Implementation sources

- [consumer/network.py](/reference/files/consumer/network.py/)
- [consumer/connections.py](/reference/files/consumer/connections.py/)
- [consumer/connectors.py](/reference/files/consumer/connectors.py/)
- [consumer/browser.py](/reference/files/consumer/browser.py/)

[Developer manual](../index.md)

