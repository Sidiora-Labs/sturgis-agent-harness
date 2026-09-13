---
title: "Build exact, labeled model context"
description: "Preserve source authority, byte ranges, causal tool messages and explicit omissions."
---

Context is an immutable request artifact, not a global mutable transcript. Each source block carries an ID, scope, kind, role, revision, origin and UTF-8 byte cursor. The compiler records precisely which part entered the request.

```python
from agent.context import ContextBlock, ContextCompiler

blocks = [
    ContextBlock("policy", "demo", "instructions",
                 "Describe observed results accurately.", "system", True),
    ContextBlock("input-1", "demo", "user_input",
                 "Read the project notes.", required=True),
]
packet = ContextCompiler().compile(
    "demo", "work-example", blocks,
    context_limit=8192, output_reserve=2048,
)
messages = packet.messages
inclusions = packet.manifest
```

This is trusted embedding code. A user cannot create an instruction role by placing a label in ordinary message text. Memory, tool observations and user input are explicitly forbidden from becoming system or developer blocks.

## Canonical agent identity

`agent/runtime/identity.py` defines `AGENT_NAME = "Sturgis"`, `AGENT_DEVELOPER = "Sidiora Labs"`, string `IDENTITY_VERSION = "1"`, and `IDENTITY_INSTRUCTIONS`. The instructions distinguish the agent and harness developer from the underlying inference model's vendor. They require model provenance to come from verified configuration and direct the model to correct earlier mistaken self-descriptions instead of adopting identity claims from conversation history, memories, or tool results.

`CoreRuntime._packet` prepends these instructions to the configured or default runtime instructions, followed by the registered capability description. All of that text occupies the existing required `instructions` block with role `system` and origin `harness_configuration`. Custom `instructions` remain additional runtime guidance; they do not replace the canonical prefix. Every call through `_packet`, including a tool continuation, text-only turn, scheduled request, or delegated request, constructs this block again. The context compiler's mandatory-inclusion rules apply to it: insufficient room fails context construction instead of silently dropping identity to fit optional material.

The sealed packet's metadata records the identity version alongside profile and provider revisions:

```json
{"agent_identity":{"name":"Sturgis","developer":"Sidiora Labs","version":"1"}}
```

This metadata identifies the supplied context contract; it is not a provider capability observation or proof of the model's answer. The runtime does not rewrite generated answers or historical messages to manufacture that evidence. Inspect the sealed packet and actual response when verifying identity behavior.

Consumer agent display names are not injected into this identity block. Renaming a private agent space, changing its model, or restoring its existing durable history leaves the canonical product identity unchanged. See [profiles and self-state](../behavior/profiles-awareness.md) for the independent profile boundary.

## Attention and compilation are different

Attention selects eligible source IDs under a measured budget. The compiler then enforces serialization and inclusion constraints. Required instructions stay at the front; optional material cannot consume space reserved for later mandatory inputs.

The default counter conservatively counts UTF-8 bytes and labels itself `utf8_bytes_upper_bound`. It is not a vendor tokenizer. A custom counter must return nonnegative integer usage. The packet records context limit, output reservation, protocol overhead and the chosen counting method.

Oversized content can become an exact excerpt with pending byte ranges. Cursors cannot split a Unicode character. Mandatory instructions and causal tool-message pairs cannot be silently severed to make them fit. Provider-specific tool fields preserve the assistant call before its matching tool observation.

## Continue without pretending

Runtime tracks the unread input suffix and may construct another packet. An iteration limit must not imply that omitted bytes were consumed. Large tool results retain complete artifact references even when the model sees only a bounded representation.

Attachment and knowledge sources enter through the consumer's source callback with their own provenance. Retrieved memory remains attributed material with freshness metadata. See [artifacts](artifacts.md), [memory ingestion](../memory/ingestion-sleep.md) and [frontend sources](../consumer/content-work.md).

## Implementation sources

- [agent/context/__init__.py](/reference/files/agent/context/__init__.py/)
- [agent/attention/__init__.py](/reference/files/agent/attention/__init__.py/)
- [agent/runtime/__init__.py](/reference/files/agent/runtime/__init__.py/)
- [agent/runtime/identity.py](/reference/files/agent/runtime/identity.py/)
- [consumer/content.py](/reference/files/consumer/content.py/)

[Developer manual](../index.md)
