---
title: "Connect and select a model"
description: "Choose a gateway model for your agent, verify readiness, and retain an existing legacy connection during migration."
---

The normal setup is **Model Gateway → connect a provider**, then **Settings → Agent model → Use model**. You do not need to create a credential reference, enter token limits, or configure an agent-specific provider ID.

## Connect and select through the gateway

1. Open **Model Gateway** in the installation section of the sidebar. This works before you create an agent.
2. Pick a preset. Paste its API key or complete its supported account sign-in.
3. Choose **Load models**, select the models you want to import, and connect.
4. Open your agent's **Settings → Agent model**.
5. Select **Use model** beside the intended model or route.
6. Wait while **Checking model…** is shown. When it succeeds, the page shows **Selected** and **Currently using**. Open **Conversation**, send `Reply with a short greeting.`, and confirm a completed response.

**Models imported** in Model Gateway confirms that the model catalog was saved. **Use model** checks an actual response before selecting that model for the agent. If this fails, the model row keeps an explanation and a **Retry** button. Fix the reported connection, access, quota, or response problem before retrying; a failed check does not replace the selection with the failed model.

Check the capability badge beside **Currently using**. **Chat and tools** means the canonical tool-response check succeeded; **Chat only** means the model can answer but tool use is not verified. A failed tool check can fall back to a successful text check, so selection alone does not establish action capability. Choose **Retry tool check** to retry the selected model, or choose one that supports tools. Successful tool checks do not grant permissions or execute a test action.

The gateway owns upstream credentials. Sturgis automatically gives the agent a gateway key restricted to its selected model. The agent's configuration and probe still enforce its context, output, and tool-capability boundaries.

The selected model is Sturgis's inference component. Sturgis remains the agent developed by Sidiora Labs when you switch providers; the upstream model's developer is a separate attribution. See [agent identity in conversations](../conversations/continuing-conversations.md#agent-identity-and-the-selected-model).

See the complete [provider connection manual](../guides/model-gateway.md), [native account sign-in guide](../guides/gateway-accounts.md), and [model routes and keys](../guides/gateway-routing-keys.md).

## Keep an existing connection during migration

Existing direct model configurations remain available under **Settings → Agent model → Advanced: legacy model connections, credentials & usage**. They are not silently deleted or copied into the new gateway.

Connect the intended provider in Model Gateway, import its model, and choose it under **Your agent’s model**. Confirm a real conversation works before changing old credentials or configurations. Gateway connections belong to the installation account and use their own credential storage; an old agent vault reference cannot be pasted into the new provider API-key field.

## Legacy direct-provider configuration

Use the collapsed advanced controls only when maintaining a pre-gateway integration. Have the complete chat-completions streaming endpoint, exact model identifier, documented context/output limits, and any required credential. Store that credential using [private credentials](credentials.md). A local or private direct endpoint also needs an operator-approved exact origin.

The legacy transport sends `model`, `messages`, `stream:true`, `max_tokens`, and optional function tools. It parses a bounded single-choice SSE response ending in `[DONE]`. Native Responses, Anthropic, Gemini, and supported account protocols are supplied by the gateway; the old direct form remains a Chat Completions adapter.

In the advanced section, choose **Add model**, enter its ID, endpoint, model, limits, and credential reference, then save. Leave **Model supports canonical tool calls** unchecked for text-only operation. Choose **Probe model**, inspect its result, and choose **Use model** after readiness succeeds.

## Legacy CLI example: a text-only research model

Ask your service operator for the real endpoint and model name. Save this as `model.json`, replacing the two example strings:

```json
{"provider_id":"research","endpoint":"https://model.example.com/v1/chat/completions","model":"SERVICE_MODEL","context_limit":32768,"output_limit":4096,"tools":false}
```

```sh
sturgis command AGENT_ID providers.configure --params-file model.json
sturgis command AGENT_ID providers.list
```

Use the returned revision in `probe.json`:

```json
{"provider_id":"research","expected_revision":1}
```

```sh
sturgis command AGENT_ID providers.probe --params-file probe.json
```

Reread the configuration and use its current revision for `providers.activate`. Then send a short conversation prompt. Expect a persisted text response. A text-only configuration suppresses tool descriptions and the invoke schema; a returned tool proposal is rejected before tool admission.

## Readiness, failure, and limits

Readiness lasts five minutes for the current configuration/credential revision. An unchanged selected configuration can be re-probed before new work when its observation expires. Editing an endpoint or rotating a key invalidates the old evidence.

A probe makes a real request and may consume tokens. Declared limits do not discover the vendor's actual context window. If the probe fails, inspect the typed error, verify the exact endpoint and stream format, and fix the configuration before probing again. Increasing an allowance does not repair unsupported tool transport. Keep [usage evidence](../settings/usage-diagnostics.md) separate from a provider invoice.

A slow model check can continue while Sturgis renews its work lease, within the configured network timeout and a maximum of 120 seconds per probe. Cancelling the check or ending its authority stops that operation; it does not silently start a new text-only check. A disabled profile or exhausted work budget also prevents that fallback. Retry after resolving the reported condition.

Gateway models retain the output allowance advertised at import, including large-output models. The short readiness check and an ordinary conversation use different allowances, which is why the setup walkthrough also asks you to send a real message. An allowance is a maximum; usage records show the tokens actually reported by the provider.

Readiness errors distinguish a rejected key/account, exhausted quota or credits, a withdrawn model, unsupported request capabilities, a timeout, and an incomplete or unreadable response when the provider supplies a recognized error category. Unknown failures use a general connection-check message. Provider diagnostic text and secrets are not displayed. See the [gateway troubleshooting table](../guides/model-gateway.md#fix-common-problems) for recovery steps.

## Command examples

These are command-specific examples, not a script to run every operation. Replace uppercase IDs, example endpoints, timestamps, and revision numbers with the actual intended values. Save one JSON object as `params.json`, then run `sturgis command AGENT_ID METHOD --params-file params.json`. Omit the file for `{}`. If acknowledgement is unknown, keep request and domain operation identities unchanged. For an acknowledged unavailable result, follow the feature-specific recovery instructions.

| Method | Example parameters or private-prompt command |
| --- | --- |
| `providers.activate` | `{"provider_id":"research","expected_revision":1}` |
| `providers.configure` | `{"provider_id":"research","endpoint":"https://model.example.com/v1/chat/completions","model":"SERVICE_MODEL","context_limit":32768,"output_limit":4096,"tools":false}` |
| `providers.inspect` | `{"provider_id":"research"}` |
| `providers.list` | `{}` |
| `providers.probe` | `{"provider_id":"research","expected_revision":1}` |
