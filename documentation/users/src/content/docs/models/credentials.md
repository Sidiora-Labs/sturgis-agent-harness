---
title: "Store, rotate, and revoke credentials"
description: "Keep model and connection secrets in the private vault and reconnect their exact revisions when values change."
---

For normal model setup, enter a provider key directly in the [Model Gateway connection drawer](../guides/model-gateway.md), or use [provider account sign-in](../guides/gateway-accounts.md). Gateway provider credentials and app keys belong to the gateway; they do not require an agent vault reference.

This guide covers the existing agent vault used by legacy model and external connection configurations. Credentials are private values referenced by those configurations. Store the value once and select its returned reference. Inspection returns names, revisions, references, and state; it does not reveal stored secrets.

## Store a credential

In **Settings → Agent model → Advanced: legacy model connections, credentials & usage**, choose **Add credential**. Enter a short credential name and the secret in the password field, then choose **Store credential**. The successful form clears the secret. Select the saved reference when configuring a model or connection.

The CLI provides a private prompt:

```sh
sturgis credential AGENT_ID model_key
sturgis command AGENT_ID providers.credentials.list
```

Do not put the value into a command argument, source document, ordinary workspace file, or generic JSON example. The CLI's dedicated credential command exists so you can supply it without placing it in shell history.

## Worked example: rotate a model key

Suppose your `model_key` credential is revision 1 and the provider uses its old reference. In Settings, use the credential's rotation control, enter the replacement value, and store it. The CLI equivalent is:

```sh
sturgis credential AGENT_ID model_key --revision 1
```

Read the returned new reference. Edit the dependent provider or connection to use that reference, review access where applicable, and run its readiness check. Select the model again once it is ready. Merely replacing the vault value does not turn old readiness evidence into evidence for the new credential.

## Revoke access

Use the credential's revoke control and confirm **Revoke credential**. **Keep credential** dismisses the confirmation. For the CLI, save `revoke.json` with the actual current revision:

```json
{"name":"model_key","expected_revision":2}
```

```sh
sturgis command AGENT_ID providers.credentials.revoke --params-file revoke.json
```

Expect dependent configurations to require a new credential and readiness check. Revocation fences future use; it is not proof that an already submitted remote operation was undone.

## Recovery and limits

On a revision conflict, list credentials again before rotating or revoking. A lost acknowledgement for secret-bearing work may not have a recoverable secret response; inspect the credential's metadata instead of repeatedly submitting changed values under an old request ID.

Agent vaults are outside writable workspaces. Portable backups exclude vault values and IPC keys, so restored agents must reconnect credentials. Ordinary documents and conversation text may still contain secrets you deliberately wrote there; a backup cannot infer and remove those values reliably.

Login sessions are separate credentials. `sturgis logout` revokes the current session. CLI `--output-file` writes full command responses to a new private file; treat one-time pairing material and any full secret-bearing output as private data. See [device pairing](../connections/devices.md) for its distinct one-time proof.

## Command examples

These are command-specific examples, not a script to run every operation. Replace uppercase IDs, example endpoints, timestamps, and revision numbers with the actual intended values. Save one JSON object as `params.json`, then run `sturgis command AGENT_ID METHOD --params-file params.json`. Omit the file for `{}`. If acknowledgement is unknown, keep request and domain operation identities unchanged. For an acknowledged unavailable result, follow the feature-specific recovery instructions.

| Method | Example parameters or private-prompt command |
| --- | --- |
| `providers.credentials.list` | `{}` |
| `providers.credentials.put` | Use `sturgis credential AGENT_ID model_key`; the secret is prompted privately. |
| `providers.credentials.revoke` | `{"name":"model_key","expected_revision":2}` |
