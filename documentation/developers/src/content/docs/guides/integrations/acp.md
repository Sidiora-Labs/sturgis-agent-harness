---
title: "Agent Client Protocol integration"
description: "Expose the persistent agent through a scoped ACP v1 text-session facade."
---

ACP means Agent Client Protocol. It is an external client integration boundary over the persistent core, not another agent loop and not a synonym for the private consumer IPC protocol.

The caller authenticates the connection, chooses the scope/workspace and supplies the emitter. CoreRuntime binds real submit/cancel callbacks:

```python
async def acp_integration_example(core, emit_json_rpc, connection_id):
    facade = core.acp(emit=emit_json_rpc, connection_id=connection_id)
    initialized = await facade.handle({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": 1, "clientCapabilities": {}},
    })
    if "error" in initialized:
        return facade, initialized, None
    session = await facade.handle({
        "jsonrpc": "2.0", "id": 2, "method": "session/new",
        "params": {"cwd": str(core.profiles.resources(core.scope)["workspace"]),
                   "mcpServers": []},
    })
    return facade, initialized, session
```

The embedding host invokes this function once for a facade, supplying its running CoreRuntime, authenticated logical connection identity and actual asynchronous `emit_json_rpc` output transport. It retains the returned facade for later messages and checks both JSON-RPC responses. Keep the emitter's lifetime aligned with the connection and close/rebind deliberately on disconnect; defining this function does not open a listener or authenticate a client.

## Supported behavior

The facade targets ACP v1 text sessions: initialization, new/load session, text prompts, cancellation and session updates. It advertises unsupported image, audio and embedded-context prompt capabilities as false. Nonempty client MCP server lists are rejected rather than silently accepted.

The session working directory must be an existing absolute directory within the authorized workspace. Loading a session checks its recorded workspace. Prompt deduplication binds the authorized scope, logical `connection_id` and JSON-RPC request ID; the saved prompt also checks its session and exact text. Preserve that identity tuple when recovering a lost prompt acknowledgement. Loading the same session under a new connection identity does not recover the old prompt receipt. Concurrent prompts for an already active session receive explicit in-progress status.

Output updates carry text chunks or complete tool-call records. Canonical target errors and indeterminate outcomes must not be rendered as successful tool execution. Precise internal result details remain in the update content.

## Completion and receipts

ACP prompt completion waits for preceding output writes before returning the stop reason. Stop reasons distinguish normal completion, token/turn limits, refusal and cancellation. This barrier does not prove the external client received a packet.

The baseline facade's updates remain unconfirmed after a successful transport write. Session reload is not a receipt for earlier output. Add future protocol capabilities only with a tested mapping to the canonical delivery states.

See [channels](channels-delivery.md), [runtime](../core/runtime.md) and [provider outcomes](providers.md).

## Implementation sources

- [agent/acp/__init__.py](/reference/files/agent/acp/__init__.py/)
- [agent/runtime/__init__.py](/reference/files/agent/runtime/__init__.py/)
- [agent/channel_core/__init__.py](/reference/files/agent/channel_core/__init__.py/)

[Developer manual](../index.md)
