---
title: "Canonical values and framed boundaries"
description: "Use strict JSON, typed calls and results, and predictable binary envelopes."
---

The semantic protocol is independent of transport framing. `ToolCall` and `ToolResult` describe invocation meaning; `FrameCodec` bounds how JSON bytes cross a stream. ACP, provider SSE, private worker IPC and native memory preserve their own external envelopes and normalize at the appropriate boundary.

```python
from agent.protocol import ToolCall, canonical_json, strict_loads

call = ToolCall("workspace.read", {"path": "notes.txt"})
wire_value = call.as_dict()
restored = ToolCall.from_dict(strict_loads(canonical_json(wire_value)))
assert restored == call
```

The current call schema version is `1.0`; the default tool version is `1`. Unknown top-level call fields are rejected. Arguments and expectations must be finite JSON objects. `expectation` records an expectation; it is not an observed result or a grant.

## Result dimensions

| State | Permitted outcome |
| --- | --- |
| `accepted`, `running` | null |
| `completed` | `success`, `target_error`, `execution_error` |
| `rejected` | `rejected` |
| `cancelled` | `cancelled` |
| `indeterminate` | `unknown` |

Effect certainty is separately `none`, `observed` or `unknown`. Completeness records whether the inline observation was complete, whether it was truncated, and which ranges are missing. A completed invocation can therefore have a target error or uncertain world effects.

`CoreError(code, message, **details)` is the shared typed failure. Use stable machine-readable codes and bounded details. Avoid embedding secrets, raw credentials or sensitive transport bodies into errors that can reach journal projections or clients.

## Framing rules

The core and native transport use a four-byte unsigned big-endian payload length followed by UTF-8 JSON. Reject oversized or truncated frames before unbounded allocation. Strict JSON parsing rejects duplicate keys and nonfinite values; canonical serialization sorts keys so identities and fingerprints do not depend on property order.

A stream reader must preserve subsequent complete frames after a recoverable malformed message where its protocol allows that recovery. Fatal framing loss is different: the stream can no longer safely guess its next boundary. See [native wire examples](../memory/records-wire.md) and [private IPC](../consumer/workers-ipc.md).

## Implementation sources

- [agent/protocol/__init__.py](/reference/files/agent/protocol/__init__.py/)
- [agent/framing/__init__.py](/reference/files/agent/framing/__init__.py/)
- [consumer/contracts.py](/reference/files/consumer/contracts.py/)
- [native/memory/PROTOCOL.md](/reference/files/native/memory/PROTOCOL.md/)

[Developer manual](../index.md)

