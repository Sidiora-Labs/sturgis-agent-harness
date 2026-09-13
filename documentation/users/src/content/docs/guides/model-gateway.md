---
title: "Connect providers with Model Gateway"
description: "Pick a preset, load its live models, and connect your agents and other apps from one place."
---

Model Gateway is the installation's model connection center. Connect a provider once, import its models, and use those models from Sturgis agents or other compatible apps. You can open it before creating an agent, and a suspended agent does not prevent gateway administration.

The gateway is also available as a [standalone service](../getting-started/standalone-gateway.md). Its provider connections, accounts, app keys, routes, and usage belong to the gateway. An agent receives a restricted gateway key when you select its model; it does not receive the upstream provider's API key.

## Choose a connection

Open **Model Gateway** in the installation section of the sidebar. Use **Search providers** or the category chips to find a preset.

| Choice | What you need | What the gateway does |
| --- | --- | --- |
| API provider | A provider account and API key | Fills the endpoint, fetches the live model catalog, and stores the key securely when you connect. |
| Free option | Whatever account or key that provider requires | Offers the provider's supported free catalog or free tier; the provider still decides eligibility, quota, and availability. |
| Account sign-in | An eligible provider account | Opens the supported device-code or browser sign-in flow. See [account connections](gateway-accounts.md). |
| Local | A running model server with models installed | Connects to the server on your gateway machine. A default local server may need no API key. |
| Gateway | Another gateway, such as 9router | Imports the models that gateway advertises. Supply its client key if it requires one. |
| Custom | A compatible API base URL and its authentication | Uses the selected OpenAI Chat Completions, OpenAI Responses, Anthropic, or Gemini protocol preset. |

A free tier is not a promise of unlimited inference. Paid and free models may coexist in a provider's catalog. Local inference consumes your hardware resources. Account sign-in does not create a subscription or bypass account restrictions.

## Connect an API provider in one drawer

1. Select the provider's tile. Its **Provider name** and **Endpoint** are filled in.
2. Keep the suggested name or give the connection a recognizable name, such as `Personal Groq`.
3. Paste the provider key into **API key**. The field is masked. **Get an API key** opens the provider's key-management page when the preset provides one.
4. Select **Load models**. The gateway fetches the catalog using this connection; it does not rely on a hard-coded list of model names.
5. Search the returned models. Check the models you want to import, or use **Select visible** to add all models matching the current search. Changing the search does not clear previous selections.
6. Select **Connect provider**. The drawer closes after the connection is saved. Find it under **Your connections**.

The drawer keeps your entries when a request fails so you can fix them and retry. Closing the drawer or successfully connecting clears the entered API key. It is not placed in browser storage or the browser's query cache.

The **Models imported** badge means the catalog was saved. Importing confirms that the provider advertised those models at discovery time; it does not test a completed answer from each model. Selecting a model for an agent performs its real readiness check, and normal requests still enforce the provider's limits.

## Worked example: a local model

Start your model server on the same machine as the gateway and make sure it has a model installed. Then:

1. Choose **Local → Ollama**.
2. Keep `http://127.0.0.1:11434/v1` if that is your server's actual address.
3. Leave **API key** empty for an unsecured local server, or enter the key if you configured authentication.
4. Choose **Load models**, select a model actually returned by your server, and connect.
5. Open the **Models** tab and copy its complete gateway model ID.

`127.0.0.1` means the machine running the gateway, not necessarily the computer displaying your browser. In a container, it means that container. A model server elsewhere on a private network needs the operator to permit its exact origin. See [standalone operation](../getting-started/standalone-gateway.md#local-and-private-model-servers).

If the catalog is empty, install or enable a chat model in the local server before retrying. The gateway filters known non-chat models, such as embedding-only or speech-only models, from this chat catalog.

## A provider without a model-list endpoint

Some custom servers support inference but do not implement model discovery.

1. Choose the custom preset matching the server's actual protocol.
2. Fill in its base endpoint and key, if needed.
3. Open **Advanced: add a model ID**.
4. Enter an exact model ID from that server's configuration or documentation and select the add button.
5. Add any other intended models, then select **Connect provider**.

Manual import makes a short inference request for each selected model before saving. It may consume provider usage. Import at most 16 manual models at once. A failed check keeps the drawer open and does not save a supposedly working connection. Manual entries use preset fallback limits rather than claiming those limits were discovered from the provider. Refreshing a manual connection repeats its checks.

If you combine a manually entered ID with discovered selections, the connection is saved in manual mode and checks every selected model. Use **Load models** again to return to a fully discovered catalog.

## Use models in Sturgis and other apps

For a Sturgis agent:

1. Open **Settings → Agent model**, find the imported model, and select **Use model**.
2. Wait while the button shows **Checking model…**. This makes a real model request and may consume provider usage.
3. On success, confirm the model has the **Selected** badge and appears under **Currently using**. Its capability badge says **Chat and tools** or **Chat only**.
4. Open **Conversation**, send `Reply with a short greeting.`, and confirm a completed answer appears.

If the check fails, its explanation stays beside that model under **Could not use MODEL NAME**. Follow the message, then select **Retry**. The failed check does not select that model. To inspect its connection or add another provider, choose **Open Model Gateway**.

**Chat only** means the model can reply but tool use has not been verified. If a tool check fails but a separate text check succeeds, the agent may select the model for chat only. Choose **Retry tool check** to check the selected model again, or select a model that supports tools. A successful retry changes the badge to **Chat and tools**. The check itself does not execute an action, and normal actions still require the agent's permissions.

For another app, you need three values:

1. The endpoint shown at the top of **Model Gateway**.
2. A gateway API key created under **Access & usage**.
3. An exact ID copied from **Models**, such as `conn_EXAMPLE/SERVICE_MODEL`, or a named model route.

Use the gateway key in the external app, not an upstream provider key or the standalone administrator key. See [routes and app keys](gateway-routing-keys.md) for an executable client example.

## Update, rotate, refresh, and disconnect

- **Edit** changes the connection name, endpoint, imported selections, or key. An empty key field keeps the existing stored key. A changed endpoint requires entering the key again before it can be reused with that address.
- **Load models** inside Edit fetches the current catalog and lets you change the import set. Saving applies the current connection revision; another editor's change can produce a conflict.
- **Refresh models** checks the existing import set against the live catalog. It removes withdrawn models and does not automatically import newly advertised models. Edit and load the catalog to add new ones.
- **Disconnect** asks for confirmation. It removes the connection and its stored API-key secret. Model routes lose targets from that connection; routes with no remaining targets are removed. Apps and agents using removed models need another model.

A signed-in account is distinct from an imported connection. Disconnecting that connection does not sign out the provider account. Account lifecycle is described in [account connections](gateway-accounts.md).

## Fix common problems

| What you see | What to do |
| --- | --- |
| API key rejected | Verify the key belongs to this provider and has model access. Replace it in the drawer and retry. |
| Endpoint or model not found | Use the base API URL and exact model ID. Load the catalog again. Choose the correct custom protocol. |
| No models returned | Enable chat models in the provider account or local server. Use manual import only for a server that lacks discovery. |
| Network address outside policy | Check where the gateway runs. Ask its operator to permit the exact private model-server origin. |
| Rate or quota limit | Wait for the provider's quota to reset, choose another model, or configure a fallback route. |
| No available quota or credits | Check the provider account's billing and limits. Importing its catalog does not establish that inference credits are available. |
| Model check timed out | Retry the check or choose another model. A saved catalog can still contain a model that is slow or temporarily unavailable. |
| Provider stopped before finishing, or returned an unreadable stream | Retry **Use model** and check the gateway connection if it persists. A partial answer is not a completed response. |
| Connection changed | Refresh the connection list and reopen Edit before saving again. |
| Models withdrawn | Edit the connection, load its current catalog, and replace unavailable models. |
| Agent model selection fails | Keep the reported error. Check the gateway connection and model access, then retry **Use model**. |
| Selected model says Chat only | Select **Retry tool check**, or choose a model that supports tools. Verify the **Chat and tools** badge before expecting model-proposed actions; required permissions still apply. |

OpenRouter can send its final token counts in a separate chunk that repeats an empty completed choice. The gateway accepts that accounting chunk without duplicating the answer or marking a successful check as failed. Token counts still come from the provider; missing counts remain unreported.

[Select an agent model](../models/connect-a-model.md) · [Provider account sign-in](gateway-accounts.md) · [Routes, keys, and usage](gateway-routing-keys.md)
