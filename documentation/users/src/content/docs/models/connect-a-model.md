---
title: "Connect and select a model"
description: "Configure the supported streaming protocol, probe actual readiness, and choose text-only or tool-capable operation."
---

A model configuration tells Sturgis where to send inference requests and what limits to reserve. Configuration does not prove the endpoint works. A probe records actual readiness for the exact configuration and credential revision, and activation selects that model for new work.

## Before you start

Have the complete chat-completions streaming endpoint, exact model identifier, documented context/output limits, and any required credential. Store the credential first using [private credentials](credentials.md). A local or private endpoint also needs an operator-approved exact origin.

The supported transport sends `model`, `messages`, `stream:true`, `max_tokens`, and optional function tools to the configured endpoint. It parses a bounded single-choice SSE response ending in `[DONE]`. This is not a universal vendor contract, a Responses API adapter, or arbitrary OAuth support.

## Configure in the web interface

1. Open **Settings → Models & credentials**.
2. Choose **Add model**, or **Connect your first model** in an empty space.
3. Enter the provider ID, complete endpoint, model name, context limit, output limit, and credential reference if needed.
4. Leave **Model supports canonical tool calls** unchecked for a text-only model. Select it only if the endpoint supports Sturgis's canonical tool-call transport.
5. Save, choose **Probe model**, and inspect the result. When readiness succeeds, choose **Use model**.

## Worked example: a text-only research model

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

## Command examples

These are command-specific examples, not a script to run every operation. Replace uppercase IDs, example endpoints, timestamps, and revision numbers with the actual intended values. Save one JSON object as `params.json`, then run `sturgis command AGENT_ID METHOD --params-file params.json`. Omit the file for `{}`. If acknowledgement is unknown, keep request and domain operation identities unchanged. For an acknowledged unavailable result, follow the feature-specific recovery instructions.

| Method | Example parameters or private-prompt command |
| --- | --- |
| `providers.activate` | `{"provider_id":"research","expected_revision":1}` |
| `providers.configure` | `{"provider_id":"research","endpoint":"https://model.example.com/v1/chat/completions","model":"SERVICE_MODEL","context_limit":32768,"output_limit":4096,"tools":false}` |
| `providers.inspect` | `{"provider_id":"research"}` |
| `providers.list` | `{}` |
| `providers.probe` | `{"provider_id":"research","expected_revision":1}` |
