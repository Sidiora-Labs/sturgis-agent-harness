---
title: "Credential vault and provider settings"
description: "Bind readiness to exact configuration and credential revisions."
---

Normal model connections now belong to the independent [Model Gateway](../gateway/architecture.md). Its encrypted store holds upstream API keys and account tokens. `ConsumerGateway._bind` supplies each agent only a gateway app key restricted to its selected model; `AgentApplication.bind_gateway` installs that client credential, checks the model, and activates it. The mechanisms below remain the agent's local credential and readiness boundary and support existing direct Chat Completions integrations in the collapsed advanced settings.

CredentialVault stores authenticated ciphertext under a private agent root outside public workspace access. Its master key and database must be privately owned regular files, with symlink/link protections. AES-GCM additional data binds scope, name, revision and opaque reference.

Inspection returns only name, revision, reference and active state. Privileged executors resolve a pinned reference immediately before execution. `current(scope, reference)` cheaply rejects stale or revoked references during authorization; the vault never calls back into the operational Journal, avoiding inverted lock ownership.

## Rotation walkthrough

1. Read the credential's current metadata revision.
2. Call `providers.credentials.put` with the same name, new secret and that expected revision.
3. Receive a new opaque reference. The previous reference is no longer current.
4. Update the provider or connection configuration with the new reference and its expected configuration revision.
5. Probe the exact new configuration, then activate it where provider selection requires activation.

A new secret should not automatically reactivate a different endpoint or permission grant. An already authorized request may finish with its pinned secret; rotation fences new claims.

## Provider lifecycle

`providers.configure` requires an ID, full endpoint, model and context/output limits. Tool support defaults false. No credential reference means no bearer authentication. The settings record is not proof that the endpoint works.

A probe uses the actual network policy and canonical streaming adapter, then records readiness for the exact configuration and credential revision. Tool-enabled probes require a canonical invoke proposal; text-only probes do not. Observations expire after five minutes.

Tool probes reuse `agent.runtime.INVOKE_SCHEMA`, the same function schema supplied to normal model turns. Its object requires `tool` and `arguments`, exposes optional `tool_version`, `schema_version`, and `expectation`, and forbids additional properties. A generic `{type:"object"}` probe schema would allow a provider to return `{}` even though `ToolCall.from_dict` rejects it. The shared contract keeps schema-aware provider validation aligned with the actual parser. For example, `{"tool":"consumer.probe","arguments":{}}` supplies the required fields; normal ToolCall defaults fill the version and expectation fields before equality with the expected probe is checked. The probe does not dispatch that proposed action.

Text checks allow up to 128 output tokens; tool checks allow up to 1,024, both capped by the imported model's configured output limit. Tool JSON and any provider-side reasoning need enough room to complete the canonical envelope; a minimal text-answer budget is not evidence of a model's tool capability. The scheduler's 4,096-token allocation per probe remains unchanged, and factual input/output consumption is recorded even for failed checks. These allowances do not measure how a vendor divided its generation between reasoning and tool arguments, and truncation remains a failed capability check.

`ProviderSettings._probe_lease` renews the admitted work immediately and then at intervals of `min(1 second, lease_seconds / 3)`. `_probe_renew` checks the stop signal and monotonic deadline inside the journal transaction before heartbeating the original generation. The exchange and lease task race under the existing `min(120 seconds, network_policy.timeout)` deadline. This allows a slow response to outlast one scheduler lease without increasing the configured lease or granting more inference time.

Completion, cancellation, timeout, or authority failure stops renewal, signals stream cancellation, and joins both tasks before settlement. An in-flight heartbeat thread is joined rather than abandoned, so it cannot renew work after the probe returns. `_probe_finish` first records observed factual token consumption, then checks that the work is still running, nonrevoked, and in the original generation. Budget and authority failures take precedence over an earlier provider error. A separate configuration transaction rejects concurrent configuration or credential rotation without rolling back recorded usage. An externally cancelled probe records failure and re-raises cancellation.

Gateway binding may retry a failed tool check with text-only configuration. It does not start that fallback when the recorded code is `cancelled`, `authority_revoked`, `stale_authority`, `budget_exceeded`, or `profile_disabled`; these end the authorized operation. If a permitted separate text probe succeeds, the selected record honestly has `tools:false`. The UI presents **Chat only**, not verified tool readiness. An explicit retry of a completed same-model binding reuses its restricted client key but gets a fresh worker request identity; otherwise the old cached success could preserve a prior text-only fallback. A still-pending binding retains its identity until uncertain IPC is reconciled. This distinction avoids replaying an uncertain request while allowing a new capability check after a known outcome.

Activation requires a fresh matching observation. For an already selected unchanged provider, `ensure_selected_ready` may refresh readiness at the runtime's pre-admission hook. It must not silently activate edited configuration. The core's text-only consumer mode removes tool advertisement and rejects unsolicited proposals.

### Safe readiness failures

`consumer.providers.probe_failure` chooses static recovery text from an allowlisted provider category, the adapter's error code, or a recognized integer HTTP status. It can explain rejected authentication, quota/credit exhaustion, a missing model, rejected capabilities, a timeout, an incomplete stream, and a protocol mismatch. Arbitrary provider messages cannot select public text. Unknown categories receive a general model-check message.

The persisted probe error keeps the original adapter `code` with that static `message`. `AgentApplication.bind_gateway` forwards the message when readiness fails, retaining the outer `provider_unavailable` code and the underlying `probe_code` detail. Clients should display the safe message rather than replacing every failure with a generic gateway instruction. A failure does not activate the candidate configuration. The model row remains retryable, while an existing selection follows its own configuration and readiness state.

For example, a recognized `provider_auth` response directs the owner to reconnect and check model access. A recognized `insufficient_quota` response directs them to provider billing and limits. An unrecognized vendor diagnostic is neither journaled nor echoed to the browser. Catalog import alone is not inference evidence; the **Use model** path must reach this probe/activation boundary, then a conversation verifies ordinary model work.

## Secret-handling rule

Never persist input secrets in error messages, ordinary journal projections, browser recovery storage or docs examples. Browser credential forms clear transient values; CLI entry prompts privately. Secret-producing one-time responses cannot safely be reconstructed from ordinary receipts.

See [provider transport](../integrations/providers.md), [HTTP commands](http-commands.md) and [backup exclusions](backup-restore.md).

## Implementation sources

- [consumer/gateway.py](/reference/files/consumer/gateway.py/)
- [consumer/vault.py](/reference/files/consumer/vault.py/)
- [consumer/providers.py](/reference/files/consumer/providers.py/)
- [consumer/application.py](/reference/files/consumer/application.py/)
- [consumer/network.py](/reference/files/consumer/network.py/)
- [agent/runtime/__init__.py](/reference/files/agent/runtime/__init__.py/)

[Developer manual](../index.md)
