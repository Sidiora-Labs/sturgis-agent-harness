---
title: "Receive signed webhook observations"
description: "Configure authenticated external events and distinguish them from owner prompts or automatic execution authority."
---

An incoming webhook records an external observation in the selected agent. It is useful for a compatible service that needs to report an event. It is not an owner-message endpoint, a generic unsigned webhook, or a rule that automatically executes every received instruction.

## Configure the receiver

Store a signing credential in [Settings](../models/credentials.md). In Connections, choose **Incoming webhook**, set its ID and endpoint, and select that credential reference. Save, grant **Receive authenticated external events**, and test the resulting configuration. The actual inbound URL appears with the connection's access details.

The receiver is `POST /api/v1/external/AGENT_ID/webhook/CONNECTION_ID/receive`. It requires a known active owner-configured route and signed, bounded JSON. Public owner-command routes do not expose internal `ingress.webhook` directly.

## Worked example: report a source update

A compatible publisher sends a body such as:

```json
{"event_id":"maintenance_note_42","text":"A corrected maintenance note is available.","data":{"document":"maintenance-notes","revision":2}}
```

The publisher includes `X-Sturgis-Timestamp`, `X-Sturgis-Nonce`, and `X-Sturgis-Signature`. The timestamp is integer Unix seconds. The signature is HMAC-SHA256 hex using the configured current secret over canonical JSON containing:

```json
{"version":"webhook/1","connection_id":"CONNECTION_ID","revision":3,"timestamp":1790000000,"nonce":"UNIQUE_NONCE","sha256":"SHA256_OF_EXACT_RAW_BODY"}
```

Replace every identity, revision, timestamp, and digest with actual current values. This object is the signing input, not the HTTP body. The installed helper `consumer.webhooks.signing_bytes(connection_id, revision, timestamp, nonce, body)` produces the canonical bytes from the exact raw body. A publisher should obtain the secret from private storage, never from a URL or a logged command argument.

Expect a recorded external event with provenance and deduplication. If you want that observation to become suggested or bounded work, use an explicitly configured [initiative and attention workflow](../work/initiative-and-attention.md). The observation itself does not inherit the owner's authority to send prompts or approve tools.

## Inspect configuration through the CLI

Use `connections.get` with `{"id":"CONNECTION_ID"}` to inspect its current revision, grants, and readiness. Rotation of the referenced credential or editing the connection requires the publisher to use the new revision/secret and the owner to review readiness. `connections.revoke` disables future authorized receipt using ID and current revision.

## Failure and limits

Webhook bodies are bounded to 1 MiB; external routes allow at most 120 requests per connection per minute. Timestamp tolerance can be configured with `max_skew` from 1–900 seconds. Nonces and event identities support duplicate handling; reusing an identity with different content is rejected.

For signature failures, compare the exact raw bytes, active revision, timestamp, nonce, and secret. Do not bypass verification or grant wider origins to compensate for an HMAC mismatch. On a lost acknowledgement, keep the exact original event identity and content. If a credential is compromised, revoke and rotate it, then reconnect the publisher deliberately. External observations remain distinct from [verified action outcomes](../work/evidence.md).
