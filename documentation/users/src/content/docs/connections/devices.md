---
title: "Pair and manage a compatible device"
description: "Use one-time pairing, declared operation schemas, signed polling, and truthful device-result states."
---

A paired device is a separate client that implements Sturgis's `device/1` protocol. Pairing establishes an identity and signing secret. It does not install a device application or prove the device can carry out an operation. Native Windows, macOS, and Linux desktop actuators are not included in this release.

## Configure and pair

Choose **Paired device** in Connections. Enter its endpoint, **Device identity**, and **Allowed device operations**. Use exact names implemented by your client, such as `read_status`. Define each operation's input schema under Advanced options; an operation without a supplied schema defaults in the UI to an object with no additional properties.

Save the connection and use the device pairing control. **Pair your device** shows a one-time token and its claim endpoint. Copy it into your compatible device client before expiry. After the client claims it, review grants and choose **Test connection**. Pairing changes revisions, so always refresh the current record before granting or revoking.

## Worked example: a read-status device

Use device identity `desk_device` and operation `read_status`. For a no-argument operation, Advanced options may include:

```json
{"operations":{"read_status":{"type":"object","additionalProperties":false}}}
```

After the compatible client claims its token, grant **Request the declared device operations**. Inspect it with:

```sh
sturgis command AGENT_ID devices.list
```

Save `device.json` as `{"connection_id":"DEVICE_CONNECTION_ID"}` and run `devices.get`. Expect `paired`, current connection revision/status, device identity, and possibly `last_seen`. Readiness has `actuator_verified:false`; perform an actual bounded operation through normal model/tool admission to establish whether the client responds.

## Client protocol boundaries

The claim endpoint is `POST /api/v1/external/AGENT_ID/device/CONNECTION_ID/claim` with `{pairing_token,device_id}`. It returns a once-minted signing secret and updated revision. Store that secret privately in the device client; it is not retrievable from ordinary inspection.

Subsequent `sync`, `poll`, and `respond` operations use timestamp, nonce, and HMAC-SHA256 signature headers. Sync signs revision zero to discover the current configuration. Sync/poll sign an empty body while posting `{}`. Respond signs its exact raw JSON body and the current revision. Implementers must follow the canonical `device/1` signing contract, not sign an approximate reserialization.

Polling returns at most 16 pending requests. A response contains `request_id`, `status` (`success`, `target_error`, or `cancelled`), and `data`. Identical duplicate results are idempotent; changed results conflict. Late results remain labeled late rather than manufacturing a settled effect.

## Revoke and recover

`devices.pair` takes `connection_id`, `expected_revision`, and optional `ttl` from 1–900 seconds; default 300. `devices.revoke` uses the connection ID and current revision. Device options permit 1–32 declared operations within a 32 KiB schema bound.

If the token expires or acknowledgement of the one-time secret is lost, perform a new deliberate pairing attempt. Do not assume inspection can recover the secret. If requests time out, inspect the pending/result identities and `last_seen`; a paired badge does not prove an actuator is online. A restored backup requires fresh device connection and authority. Review [permissions](../settings/permissions.md) before allowing device effects.

## Command examples

These are command-specific examples, not a script to run every operation. Replace uppercase IDs, example endpoints, timestamps, and revision numbers with the actual intended values. Save one JSON object as `params.json`, then run `sturgis command AGENT_ID METHOD --params-file params.json`. Omit the file for `{}`. If acknowledgement is unknown, keep request and domain operation identities unchanged. For an acknowledged unavailable result, follow the feature-specific recovery instructions.

| Method | Example parameters or private-prompt command |
| --- | --- |
| `devices.get` | `{"connection_id":"DEVICE_CONNECTION_ID"}` |
| `devices.list` | `{}` |
| `devices.pair` | `{"connection_id":"DEVICE_CONNECTION_ID","expected_revision":1,"ttl":300}` |
| `devices.revoke` | `{"connection_id":"DEVICE_CONNECTION_ID","expected_revision":3}` |
