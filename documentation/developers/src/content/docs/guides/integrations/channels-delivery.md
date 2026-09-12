---
title: "Channels, bindings and durable delivery"
description: "Bind transport identity once and distinguish writes from acknowledged receipt."
---

ChannelCore binds a scope, adapter, account, conversation and participant. Transport identity comes from trusted connection setup, not arbitrary fields in a model message. InboundMessage carries the binding, participant, stable delivery ID, text, kind and artifact references.

```python
from agent.channel_core import InboundMessage


async def channel_integration_example(
    core, account, conversation, participant, delivery_id,
):
    binding = core.channels.bind(
        core.scope, "client", account, conversation, participant)
    return await core.accept(InboundMessage(
        core.scope, binding.id, participant,
        delivery_id, "Describe the current status.",
    ))
```

The embedding host awaits this function with its running CoreRuntime, authenticated account/conversation/participant values and stable delivery ID. The `client` adapter must already be registered. Defining the function does not admit input. Reusing a delivery identity with changed content conflicts. Prompt, steering and cancellation are separate input kinds; they remain attributed events.

## Adapter boundary

An adapter implements asynchronous `send(item)`; a receive-capable adapter can also attach to runtime ingress. StreamChannelAdapter reads/writes bounded framed JSON over caller-owned asyncio streams. The library does not create a public listener or authenticate a network socket for the embedding program.

Runtime's ingress accepts only its canonical frame fields, then uses the registered binding's authority. A dead stream produces a recorded failure; a caller may explicitly attach a new connection.

## Delivery semantics

Delivery enqueues scoped stream items with monotonically ordered sequences and optional terminal state. Claims have leases and attempt limits. A failed transport write leaves durable retry/failure state; recovery can report that delivery might have been duplicated.

A successful write is `sent_unconfirmed`. Setting a capability flag does not manufacture an acknowledgment. Only a negotiated receipt supplied through the acknowledgment path establishes confirmed receipt. Replay also requires the binding's advertised capability.

The consumer channel persists output for browser event replay, but this is not a browser acknowledgment. The browser reads scoped public events after a cursor and reconstructs history.

Use `wait_output` when a caller needs previous output writes to finish before returning a protocol response. It is distinct from waiting for inference. See [ACP](acp.md) and [frontend events](../frontend/data-events.md).

## Implementation sources

- [agent/channel_core/__init__.py](/reference/files/agent/channel_core/__init__.py/)
- [agent/channel_adapters/__init__.py](/reference/files/agent/channel_adapters/__init__.py/)
- [agent/delivery/__init__.py](/reference/files/agent/delivery/__init__.py/)
- [consumer/application.py](/reference/files/consumer/application.py/)

[Developer manual](../index.md)
