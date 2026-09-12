---
title: "Credential vault and provider settings"
description: "Bind readiness to exact configuration and credential revisions."
---

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

Activation requires a fresh matching observation. For an already selected unchanged provider, `ensure_selected_ready` may refresh readiness at the runtime's pre-admission hook. It must not silently activate edited configuration. The core's text-only consumer mode removes tool advertisement and rejects unsolicited proposals.

## Secret-handling rule

Never persist input secrets in error messages, ordinary journal projections, browser recovery storage or docs examples. Browser credential forms clear transient values; CLI entry prompts privately. Secret-producing one-time responses cannot safely be reconstructed from ordinary receipts.

See [provider transport](../integrations/providers.md), [HTTP commands](http-commands.md) and [backup exclusions](backup-restore.md).

## Implementation sources

- [consumer/vault.py](/reference/files/consumer/vault.py/)
- [consumer/providers.py](/reference/files/consumer/providers.py/)
- [consumer/application.py](/reference/files/consumer/application.py/)
- [consumer/network.py](/reference/files/consumer/network.py/)

[Developer manual](../index.md)

