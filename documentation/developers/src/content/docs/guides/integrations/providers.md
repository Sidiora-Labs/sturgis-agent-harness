---
title: "Provider catalog and streaming adapter"
description: "Observe capabilities separately from configuration and normalize complete stream outcomes."
---

ProviderCatalog separates configuration from an expiring capability observation. Registering an endpoint says where an integration is configured; observing a model records the context/output limits, tool support and freshness that admission can use.

```python
core.catalog.register(
    core.scope, "default", endpoint=model_endpoint,
    authentication="bearer")
core.catalog.observe(
    core.scope, "default", model=model_name,
    context_limit=32768, output_limit=4096, tools=True, ttl=300)
```

The trusted embedding program must obtain that observation and supply the bearer credential separately. A model-provided claim of its own capabilities is not the catalog's authority. Consumer settings add a real configuration-bound probe before selection; see [provider settings](../consumer/vault-provider-settings.md).

## Wire contract

HTTPProvider implements bounded chat/SSE HTTP streaming. It preserves message roles, serializes the canonical invoke schema when tools are available, correlates fragmented tool-call arguments, validates token usage and normalizes text, tool, usage and terminal events.

Configure the complete compatible endpoint. A vendor offering another protocol requires an adapter; “OpenAI-compatible” branding alone does not establish support for every response extension.

A refused or truncated stream preserves already emitted text and suppresses tool proposals. Partial tool fragments do not execute. Missing terminal information is a protocol failure. Cancellation can interrupt waiting for stream bytes without blocking the rest of runtime I/O.

## Tool-capability choice

The core default requires tool support when its registry is nonempty. The consumer deliberately allows text-only models: it removes both tool descriptions and the invoke schema for that request. An unsolicited tool proposal from a text-only descriptor fails before ToolDispatcher.

The adapter supports an optional client factory. Consumer provider settings use that seam to route HTTP through NetworkPolicy while reusing the canonical SSE parser. Do not duplicate the parser or bypass the network boundary with a private ad hoc client.

Token usage is factual input to scheduler accounting. If a provider omits authoritative counts, retain the outstanding token reservation; do not estimate zero. See [runtime](../core/runtime.md) and [usage budgets](../behavior/goals-budgets.md).

## Implementation sources

- [agent/provider_catalog/__init__.py](/reference/files/agent/provider_catalog/__init__.py/)
- [agent/provider_adapters/__init__.py](/reference/files/agent/provider_adapters/__init__.py/)
- [agent/runtime/__init__.py](/reference/files/agent/runtime/__init__.py/)
- [consumer/providers.py](/reference/files/consumer/providers.py/)

[Developer manual](../index.md)

