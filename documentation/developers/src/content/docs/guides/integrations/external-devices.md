---
title: "Signed webhooks and paired device clients"
description: "Authenticate external evidence without impersonating the human owner."
---

External ingress uses the fixed route `/api/v1/external/AGENT_ID/KIND/CONNECTION_ID/ACTION`. The owner first configures a known active connection; the route index maps it to that private agent. Public owner commands cannot invoke internal ingress methods by name.

Signed operations use `X-Sturgis-Timestamp`, `X-Sturgis-Nonce` and `X-Sturgis-Signature`. Sign canonical bytes from the implementation helper with HMAC-SHA256. Do not invent an equivalent-looking concatenation: exact body hashes, revision and action binding are part of the contract.

## Device handshake

1. Configure a device ID and 1–32 exact operation names with bounded input schemas.
2. The owner requests pairing for the current connection revision.
3. A compatible device client claims the one-time token with its device ID.
4. The claim returns a newly minted signing secret and updated revision. Store it in the device's private storage.
5. Grant the intended operations, then use signed sync to learn current revision.
6. Poll for bounded pending requests and respond with the exact request ID.

Pairing defaults to 300 seconds and is bounded to 1–900. Sync uses signed revision zero to discover changes. Sync/poll sign an empty body while their HTTP body is `{}`. Responses sign the actual raw response body.

```json
{"request_id":"request_42","status":"success","data":{"position":"closed"}}
```

The response must correspond to an actually observed device operation. Status is success, target_error or cancelled. Identical results are idempotent; changed reuse conflicts. Late results remain labeled late and cannot settle a previously uncertain effect by themselves. Pairing readiness is not actuator verification.

## Webhook evidence

Webhooks accept bounded strict JSON with an event ID and optional text/data. The current connection revision, allowed receive operation, timestamp window, signature and nonce/content identity are checked before recording `external.webhook`.

Its provenance is external evidence, not owner instruction. The model must not gain owner authority because a signed remote service sent imperative text.

The release provides protocol boundaries, not bundled native Windows/macOS/Linux device actuators or universally configured messaging accounts. See [connections](../consumer/network-connections.md) and [effect reconciliation](tool-effects.md).

## Implementation sources

- [consumer/webhooks.py](/reference/files/consumer/webhooks.py/)
- [consumer/device.py](/reference/files/consumer/device.py/)
- [consumer/api.py](/reference/files/consumer/api.py/)
- [consumer/connections.py](/reference/files/consumer/connections.py/)

[Developer manual](../index.md)

