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

### Categorized errors without remote diagnostic text

`_provider_error_details` accepts only fixed public codes from an error object's `code` or `type`; recognized numeric codes map through `_HTTP_ERROR_CODES`. It returns a `provider_code` detail rather than accepting the provider's free-form message, request metadata, or credentials. Unknown codes yield no additional details.

For a non-200 response, `_http_error_details` reads an optional body within the configured event-byte and time limits. A malformed, oversized, or unreadable body cannot replace the authoritative HTTP failure with its diagnostics. Streaming error events use the same category filter. `_next_chunk` races each read against cancellation, then cancels and joins pending tasks so this error-body path preserves stream cleanup.

The consumer's `probe_failure` maps recognized categories to authored recovery messages. The public probe retains its original adapter error code; it does not expose raw upstream response bodies. Tests in `tests/core/test_provider_error_details.py` and `tests/consumer/test_gateway_readiness.py` cover category admission, unsafe values, cancellation, and readiness propagation.

Normal gateway traffic reaches this adapter after provider normalization. In particular, the gateway consumes OpenRouter's empty repeated completion in a final usage envelope and emits a single terminal plus final usage. The direct legacy adapter remains strict about choices after a completion reason; put provider-specific wire compatibility in the [gateway transport boundary](../gateway/architecture.md#openrouter-final-usage-envelopes).

## Tool-capability choice

The core default requires tool support when its registry is nonempty. The consumer deliberately allows text-only models: it removes both tool descriptions and the invoke schema for that request. An unsolicited tool proposal from a text-only descriptor fails before ToolDispatcher.

`agent.runtime.INVOKE_SCHEMA` is shared by normal turns and consumer readiness probes. It declares the required `tool` and `arguments` fields instead of accepting an unspecified JSON object. A schema-aware model can therefore produce the same envelope that `ToolCall.from_dict` validates. Tool readiness requires the expected normalized `consumer.probe` proposal; the proposal is observed without executing a capability. A successful text-only fallback remains `tools:false` and must not be presented as successful tool verification.

The adapter supports an optional client factory. Consumer provider settings use that seam to route HTTP through NetworkPolicy while reusing the canonical SSE parser. Do not duplicate the parser or bypass the network boundary with a private ad hoc client.

Token usage is factual input to scheduler accounting. If a provider omits authoritative counts, retain the outstanding token reservation; do not estimate zero. See [runtime](../core/runtime.md) and [usage budgets](../behavior/goals-budgets.md).

## Implementation sources

- [agent/provider_catalog/__init__.py](/reference/files/agent/provider_catalog/__init__.py/)
- [agent/provider_adapters/__init__.py](/reference/files/agent/provider_adapters/__init__.py/)
- [agent/runtime/__init__.py](/reference/files/agent/runtime/__init__.py/)
- [consumer/providers.py](/reference/files/consumer/providers.py/)

[Developer manual](../index.md)
