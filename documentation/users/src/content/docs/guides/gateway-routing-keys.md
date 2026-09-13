---
title: "Model routes, app keys, and usage"
description: "Choose fallback order, connect external apps, limit API-key access, and interpret factual gateway usage."
---

Once providers are connected, the gateway's **Models**, **Model routes**, and **Access & usage** tabs control how apps use them. A model ID identifies one imported model. A route gives several models one stable name. An app key controls which names a client may request.

## Copy an exact model ID

Open **Models**. Search by model name, provider name, or ID, then choose **Copy ID** beside the intended model. The ID includes its connection, for example:

```text
conn_EXAMPLE/SERVICE_MODEL
```

Do not remove the connection prefix. Two connections may advertise the same upstream model name but use different accounts, keys, or endpoints. The prefix tells the gateway which connection to use. Named routes also appear in the model library and can be copied there.

If browser clipboard access is unavailable, select and copy the displayed ID. The same fallback applies to the endpoint and one-time API-key displays.

## Create a route

1. Import the intended models under **Providers**.
2. Open **Model routes → Create route**.
3. Enter a friendly **Route name**, such as `Everyday assistant`.
4. Review **Model alias**, which is the exact name an app will send, such as `everyday`. A route alias cannot contain a slash or start with `conn_`.
5. Choose **Priority fallback** or **Round robin**.
6. Check the models to include. Use the up and down buttons to change their order, or the remove button to remove one from the selected sequence.
7. Select **Save route**. Use the route alias as the model in your app.

A route contains 1–16 distinct imported models. Routes cannot contain other routes. **Edit route** changes the name, strategy, and target order; its alias remains fixed in the editor. If another editor changes the route first, reload it before retrying.

### Priority fallback

The first model is tried first. Eligible errors before any response has been emitted can advance to another model. Typical eligible failures include provider unavailability, rate limits, and rejected or unavailable provider credentials. Eligible failures temporarily cool down that destination.

Fallback stops as soon as a response chunk is emitted. If a provider fails after streaming begins, the client receives the incomplete/error outcome. The gateway does not silently combine the start of one model's answer with another model's continuation.

### Round robin

Round robin rotates the starting model among the selected targets. It still uses the same eligibility and no-fallback-after-output rules. Rotation and cooldown state are process-local: restarting a gateway resets them, and independent gateway processes do not share a global balancing counter.

Disconnecting a provider removes its targets from saved routes. A route with no remaining targets is removed. Check clients using the route after disconnecting a provider.

To remove a route explicitly, select its trash icon, labeled **Delete route ROUTE NAME**, then confirm **Delete route**. **Keep route** cancels. The providers and imported models remain connected; update apps and agents that used the deleted alias. A restricted key listing that route does not automatically gain access to another model. A revision conflict requires refreshing the current route before retrying deletion.

## Create and revoke app keys

1. Open **Access & usage → Create API key**.
2. Give the key a recognizable name, such as `Editor on laptop`.
3. Select **Generate key**.
4. Copy the complete key from **Your key is ready**, then choose **Done**.
5. Paste it into the intended app's API-key field.

The complete key is shown once. Later listings show only its name, prefix, and state. If you lose the value, create a replacement key and revoke the old one. Upstream provider keys and the standalone administrator key are different credentials; neither belongs in an ordinary inference client's key field.

To remove a client's access, choose **Revoke**, confirm **Revoke key**, and replace its configuration if it still needs access. **Keep key** cancels the confirmation. Revocation prevents later authorized requests; it does not undo a request already accepted by a remote provider.

The UI currently creates a key for all models available to its gateway tenant. The management API can restrict a key to explicit model IDs or route aliases using the `models` field. An empty list means all available model names. Restricting a key to a route allows that route's resolution; it does not expose every target as a directly permitted model name.

## Worked example: test from any Python environment

Use the endpoint copied from the gateway page. For a standalone service at its default address, it is `http://127.0.0.1:8790/v1`. The embedded Sturgis service normally displays an address ending in `/model-gateway/v1`.

This example uses Python's standard library and asks for the key privately:

```python
import getpass
import json
import urllib.request

endpoint = input("Gateway endpoint: ").rstrip("/")
key = getpass.getpass("Gateway app key: ")
model = input("Exact model ID or route alias: ")
body = json.dumps({
    "model": model,
    "messages": [{"role": "user", "content": "Reply with a short greeting."}],
    "max_tokens": 64,
    "stream": False,
}).encode()
request = urllib.request.Request(
    endpoint + "/chat/completions",
    data=body,
    headers={"Authorization": "Bearer " + key,
             "Content-Type": "application/json"},
)
with urllib.request.urlopen(request, timeout=60) as response:
    completion = json.load(response)
print(completion["choices"][0]["message"]["content"])
```

Expect a completed assistant response using the requested gateway model or route. An invalid/revoked key returns an authentication error. A model outside a restricted key's allowed names returns `model_denied`. A withdrawn model can return `model_unavailable`.

For streaming clients, set `stream` to `true` and parse Server-Sent Events until the completed stream's terminal marker. Do not treat an HTTP 200 alone as proof that the entire streamed answer succeeded; a later event can report an error.

## Read usage correctly

**Access & usage** shows provider requests, reported tokens, failures, and recent attempts. Fallback attempts and manual-model checks can each produce usage records, so these counts need not equal the number of user conversation messages.

A provider may omit authoritative token usage. Such a row shows **Usage not reported**, and the total includes only reported consumption. Missing usage is not zero consumption. Failed, incomplete, refused, and cancelled attempts can still consume tokens. A gateway usage record is not a settled invoice or a guarantee about your provider's billing.

Keep provider quotas and gateway routing separate: adding a fallback changes where a request can go; it does not expand an account's entitlement. Check each provider's own account dashboard for current quotas and billing.

## Limits and configuration transfer

The **Advanced: limits & configuration transfer** section on this tab controls request timeout, concurrency, and requests per minute. It also downloads a credential-free connection/route export and imports one with new IDs. The [step-by-step operating guide](../getting-started/standalone-gateway.md#adjust-limits-and-transfer-configuration) explains bounds, reconnecting imported providers, fresh app keys, and the distinction from preserving the private gateway storage.

[Connect a provider](model-gateway.md) · [Standalone operation](../getting-started/standalone-gateway.md)
