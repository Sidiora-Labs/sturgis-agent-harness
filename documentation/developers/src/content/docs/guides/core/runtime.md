---
title: "Compose and supervise a persistent runtime"
description: "Use one core loop for admission, inference, memory, heartbeats and delivery."
---

`CoreRuntime` owns one canonical operational root and takes an exclusive process lock. Its durable profile binds the workspace and separate resource roots. Constructing it grants no file execution, plugin installation or source activation authority to model work.

A trusted embedding program supplies transports and provider configuration:

```python
from agent.runtime import CoreRuntime
from agent.channel_adapters import StreamChannelAdapter


async def runtime_integration_example(
    root, workspace, memory_binary, scope, reader, writer,
    provider_endpoint, model_name, embedding_program_lifetime,
):
    core = CoreRuntime(
        root, scope=scope, workspace=workspace, memory_binary=memory_binary,
    )
    core.adapters.register("client", StreamChannelAdapter(reader, writer))
    binding = core.channels.bind(
        scope, "client", "account", "conversation", "participant")
    core.catalog.register(scope, "default", endpoint=provider_endpoint)
    core.catalog.observe(
        scope, "default", model=model_name, context_limit=32768,
        output_limit=4096, tools=True, ttl=300)
    core.attach_ingress(binding.id)
    async with core:
        await embedding_program_lifetime(core)
```

The embedding host invokes this function with private root/workspace paths, the compiled native binary, its trusted scope, authenticated caller-owned streams and provider values. The catalog declaration must match a capability observation the host actually obtained; the example's limits are not a probe. Its asynchronous `embedding_program_lifetime(core)` callback receives the running core and returns when the host wants it closed. Defining the function does not start a process or listener. This is not a second server entry point; the consumer product already composes these services.

## Independent paths

Admission, inference, delivery, heartbeat and memory run as supervised asynchronous tasks. Same-binding prompts queue behind their current interaction. Steering fences old work and signals cancellation, then creates a fresh request. New input is never represented as having been inserted into an already submitted provider request.

The optional `prepare_provider` callback runs before model admission. The consumer uses it to refresh readiness for an already selected unchanged provider. `context_sources` contributes labeled application source blocks.

By default, a core with registered tools requires a tool-capable provider. The consumer opts into `allow_text_only_provider=True`: it suppresses the invoke schema and registry for a text-only model and rejects unsolicited tool proposals before execution.

`pause_admission` fences new model and background-sleep admission for lifecycle snapshots. It does not claim active work has stopped. The consumer first pauses its workflow monitor and then checks actual idle/output/reservation state. See [lifecycle](../consumer/lifecycle.md).

## Implementation sources

- [agent/runtime/__init__.py](/reference/files/agent/runtime/__init__.py/)
- [agent/channel_adapters/__init__.py](/reference/files/agent/channel_adapters/__init__.py/)
- [agent/provider_catalog/__init__.py](/reference/files/agent/provider_catalog/__init__.py/)
- [consumer/application.py](/reference/files/consumer/application.py/)

[Developer manual](../index.md)
