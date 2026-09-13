---
title: "Start and continue a conversation"
description: "Create conversations, send prompts, follow durable output, and return to previous work."
---

A conversation is a continuing thread inside the selected agent. The agent's knowledge and workspace remain available across conversations, while each conversation has its own identity and message history. Start a new conversation when you want a distinct thread; create another agent when you need a separate private space.

## Before you start

Select the right agent and a [ready model](../models/connect-a-model.md). You can open history without assuming a provider is ready for new inference. If the runtime is suspended, use **Settings → Agent & data → Start runtime** to resume private web queries.

Choose **New conversation** in the sidebar. Type in **Message** and use **Send message**. The app records the input before waking a dormant worker. It then follows durable output events; connection state, admission, running work, and a terminal model turn are different observations.

## Agent identity and the selected model

You are using **Sturgis**, an agent developed by **Sidiora Labs**. The model selected in Settings supplies inference; its name and vendor are separate from the agent's identity. Changing providers does not turn the agent into that provider's product. Sidiora Labs develops the agent and its runtime, not necessarily the underlying model.

Ask `What is your agent name, who developed it, and how is that different from the model you use?` The expected answer identifies Sturgis and Sidiora Labs, and distinguishes model provenance using only verified configuration. A previous incorrect self-description should be corrected in the next reply; the original conversation record remains preserved.

Agent names such as **Research companion** are display labels for your private spaces. Renaming that label does not replace the runtime's Sturgis identity. See [accounts and agents](../getting-started/account-and-agents.md) for the resource boundaries.

## Worked example: keep a research thread

Create a conversation and send:

> Help me structure a short research note about the observatory. First list the questions we should answer; do not claim you have checked external sources.

Read the response, then send a follow-up in the same conversation:

> Use those questions to organize the notes I am attaching. Separate supported details from unanswered questions.

The expected result is a continuing exchange with the previous conversation context and explicit source material. You should not need to recreate the agent or configuration for the second message.

For CLI use:

```sh
sturgis chat AGENT_ID
sturgis command AGENT_ID conversations.list
sturgis chat AGENT_ID --conversation CONVERSATION_ID --text-file follow-up.txt
```

The first command prompts for text. Save the returned conversation ID to continue the same thread. `--text-file -` reads stdin; `--no-wait` returns after admission rather than waiting for terminal inference.

## Return, rename, or archive

Recent conversations appear in the sidebar. Open one to replay its persisted history. **Conversation options → Rename conversation** changes its title. **Archive conversation** asks for confirmation and removes it from the active list while preserving history.

An existing thread shows **Opening your conversation…** until its initial saved history is loaded. It should not look like a new, empty conversation while that replay is pending. Loaded messages stay visible if updates reconnect or the runtime pauses. See [history loading and recovery](sources-history-search.md#replay-and-recover) for the notices and cursor behavior.

The CLI/API can include archived conversations in `conversations.list` and revise the archived flag with `conversations.update`. This differs from archiving an entire agent, which has no unarchive action. See [history and search](sources-history-search.md) for examples.

## Failure and recovery

If acknowledgement is uncertain, the composer presents **Retry message** and preserves the pending input identity. Keep its text and attachments unchanged during that retry. A new intended message needs a new identity. Reconnecting to event history does not re-execute old tool effects.

The composer stores drafts in the browser's local state, so use a trusted browser profile. If the provider fails, inspect the recorded turn error and provider readiness before sending new work. To change running work or stop it, use [steering and cancellation](steering-and-cancellation.md), rather than assuming a second normal prompt cancels the first.

## Command examples

These are command-specific examples, not a script to run every operation. Replace uppercase IDs, example endpoints, timestamps, and revision numbers with the actual intended values. Save one JSON object as `params.json`, then run `sturgis command AGENT_ID METHOD --params-file params.json`. Omit the file for `{}`. If acknowledgement is unknown, keep request and domain operation identities unchanged. For an acknowledged unavailable result, follow the feature-specific recovery instructions.

| Method | Example parameters or private-prompt command |
| --- | --- |
| `conversations.create` | `{"title":"Maintenance review"}` |
