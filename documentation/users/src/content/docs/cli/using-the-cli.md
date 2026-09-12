---
title: "Use the same agent from the CLI"
description: "Authenticate, send prompts, inspect events, run domain commands, preserve request identities, and move backups through the same API."
---

The CLI talks to the same authenticated consumer service as the web interface. It does not bypass ownership or open a private worker database directly. Use it for repeatable requests and advanced features that have no dedicated UI editor.

## Configure and authenticate

Global options go before the command:

```sh
sturgis --url http://127.0.0.1:8787 login --email you@example.com
sturgis --json agents list
sturgis agents create "Research companion"
```

Login privately prompts for the password. First-owner setup adds `--bootstrap` and prompts for the installation token. `logout` revokes the stored session. Client state is private and bound to the server URL; logging into one origin does not authenticate another.

Useful global options include `--config-dir`, `--url`, `--json`, `--timeout`, and `--output-file`. The timeout is positive and at most 300 seconds; default 30. `--output-file` exclusively creates a mode-0600 file containing the full response. Normal printed output redacts recognized secret fields.

## Worked example: run and inspect a research request

```sh
sturgis chat AGENT_ID --text-file prompt.txt
sturgis command AGENT_ID conversations.list
sturgis events AGENT_ID --conversation CONVERSATION_ID --after 0 --follow
sturgis stop AGENT_ID CONVERSATION_ID
```

Save your prompt as UTF-8 in `prompt.txt`; use `--text-file -` for stdin. `chat` without a text file prompts interactively. Reuse `--conversation` to continue a thread. `--message-id` supplies a stable identity, `--no-wait` returns after admission, and `--wait-timeout` controls how long chat waits for a terminal turn.

`events --follow` repeatedly reads bounded durable pages. Keep the latest cursor for reconnecting. A non-success terminal event is not printed as a successful completed turn merely because output arrived.

## Domain commands and exact retries

Save this as `query.json`:

```json
{"query":"maintenance","limit":20}
```

```sh
sturgis command AGENT_ID knowledge.list --params-file query.json
```

The generic shape is `command AGENT_ID METHOD --params-file FILE --request-id ID`. Parameters must be a JSON object; `--params-file -` reads stdin. The client supplies a new request ID if omitted and attaches the identity to reported errors so you can inspect or retry it.

For an ambiguous mutation, keep the exact method, parameters, and request ID. If that command also requires `operation_id` or `message_id`, preserve that domain identity too; the generic CLI does not invent required domain fields inside your JSON. Changed parameters under the same request ID conflict. Completed non-secret receipts can be recovered without re-executing the original action; missing or secret receipts may remain unavailable.

A received response is different from a lost acknowledgement. For knowledge indexing or memory mutation, a successful HTTP envelope can contain a native unavailable result. After native recovery, use a **new outer `--request-id`** with the same `operation_id` and parameters to resume that domain operation. Reusing its completed outer ID only replays the saved unavailable result. Follow the [knowledge](../knowledge/source-library.md#failure-and-recovery) or [memory](../knowledge/memory.md#freshness-failure-and-limits) recovery sequence; do not apply this rule blindly to an unknown external effect.

## More tasks

`credential AGENT_ID NAME` privately prompts for a vault value. `doctor --agent AGENT_ID` inspects local availability, service health, authentication, and owned runtime diagnostics. `backup AGENT_ID FILE` and `restore FILE --name NAME` use the portable HTTP archive flow. `agents rename` and `agents archive` require `--revision`.

`setup` and `serve` operate the local installation; the other commands normally connect remotely. See [self-hosted setup](../getting-started/self-hosted.md), [all domain commands](../reference/commands.md), and [troubleshooting](../reference/troubleshooting.md). Use `sturgis --help` or `sturgis COMMAND --help` for the installed parser, rather than assuming a shell, direct tool-execution, or lifecycle subcommand exists.
