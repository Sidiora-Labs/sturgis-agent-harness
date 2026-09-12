---
title: "The complete request and effect loop"
description: "Trace input, context, authorization, observation and output through durable records."
---

A message is not a single mutable conversation buffer. It crosses durable boundaries whose identities remain available after a process or transport fails.

## Worked path: inspect a workspace file

1. The HTTP boundary authenticates the owner and authorizes the agent. The caller sends a stable request ID and message ID, never a scope or host path.
2. Lifecycle records the command's identity and content digest before waking a private worker. Message parameters are retained for undispatched input recovery.
3. The application resolves a conversation binding, validates any attached sources, and calls the core admission path.
4. ChannelCore appends an attributed input event. Runtime joins that event to work; scheduler admits it within current capacity and resource constraints.
5. ContextCompiler freezes labeled sources, history, registry information and memory freshness into an immutable packet with exact byte ranges.
6. The provider streams text or a canonical tool proposal. Text is persisted as it arrives. A proposal is still only a proposal.
7. ToolDispatcher validates the registered schema, records intent, and obtains a current claim in one journal transaction. The executor then reads the granted workspace.
8. The result records invocation state, target outcome, effect certainty, completeness and exact execution identities. Actual usage is charged.
9. Runtime may continue with the observed result in a new context packet. A final inference state does not fulfill a separate operational goal.
10. Delivery writes ordered durable output to the transport. A successful write remains unconfirmed unless the transport negotiates and supplies acknowledgment.

If the browser disconnects after step 8, the output and tool record survive. Event replay restores the visible history. It does not repeat the file operation or claim the browser saw the earlier response.

## Why the joins matter

Request IDs deduplicate commands. Input-event IDs identify original content. Work generations fence stale execution. Context IDs identify exactly what the model received. Action and attempt IDs identify the effect path. Stream sequence numbers identify delivery order. These are not interchangeable identifiers.

When debugging, walk those joins rather than inferring success from the last sentence. See [state](../core/state.md), [tool uncertainty](../integrations/tool-effects.md) and [frontend recovery](../frontend/mutation-recovery.md).

## Implementation sources

- [consumer/api.py](/reference/files/consumer/api.py/)
- [consumer/lifecycle.py](/reference/files/consumer/lifecycle.py/)
- [consumer/application.py](/reference/files/consumer/application.py/)
- [agent/runtime/__init__.py](/reference/files/agent/runtime/__init__.py/)
- [agent/tool_runner/__init__.py](/reference/files/agent/tool_runner/__init__.py/)
- [agent/delivery/__init__.py](/reference/files/agent/delivery/__init__.py/)

[Developer manual](../index.md)

