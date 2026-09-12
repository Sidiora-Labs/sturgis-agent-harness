---
title: "Attach sources and find conversation history"
description: "Include bounded source material, replay durable events, search conversations, and recover archived threads."
---

An attachment is immutable source material belonging to one agent. A conversation can refer to it without making its contents instructions or granting it tool permissions. The original bytes remain available even when Sturgis has no decoder for that format.

## Attach a source

In the composer, choose **Attach file** and select a file of at most 4 MiB. Confirm the attachment chip appears before sending your prompt. Its remove control removes the attachment from this draft; it does not delete the original upload from the source library.

Plain text, Markdown, CSV, JSON, and HTML can supply bounded UTF-8 text. HTML extraction excludes scripts and styles. Extraction over 256 KiB is deferred with no extracted text. Context inclusion has its own smaller bound and labels any omitted suffix; an upload does not mean every byte reached the model.

## Worked example: compare a source with a response

Attach `observatory-notes.md` and ask:

> Summarize the maintenance dates in this source. Quote only the short details needed to support each date, and say when the available source is incomplete.

Expect the response and original input in the same history. Open **Knowledge → Source library** to inspect the upload's extraction state and download the original if needed. If a PDF is unsupported, convert the relevant material to UTF-8 text before asking for a text-grounded answer; no OCR or office/PDF decoder is bundled.

## Search and restore archived conversations

The sidebar lists recent conversations. The command palette searches navigation views; full conversation search is available through `conversations.list` using the CLI/API. Save `search.json`:

```json
{"query":"maintenance","include_archived":true}
```

```sh
sturgis command AGENT_ID conversations.list --params-file search.json
```

Inspect a result using `conversations.get` with `{"id":"CONVERSATION_ID"}`. To return an archived conversation to the active list, call `conversations.update` with its current revision:

```json
{"id":"CONVERSATION_ID","expected_revision":3,"archived":false}
```

The web interface has rename/archive controls; it does not expose a complete archived-conversation management screen. Agent archive is a separate permanent fence in this release.

## Replay and recover

```sh
sturgis events AGENT_ID --conversation CONVERSATION_ID --after 0 --follow
```

Save the latest cursor and use it after reconnecting. `messages.events` exposes the bounded command form with `conversation_id`, `after`, and `limit`. Read replay as recorded history, not as a request to rerun a tool. Imported backup history carries `portable.import` provenance and cannot become fresh input.

If history appears unavailable while the runtime is suspended, start the runtime from Agent & data. If a download belongs to another agent, its authorization failure is expected; select the agent that owns the source rather than altering the download URL. [Knowledge](../knowledge/source-library.md) and [memory](../knowledge/memory.md) explain how source revisions differ from recalled facts.

## Command examples

These are command-specific examples, not a script to run every operation. Replace uppercase IDs, example endpoints, timestamps, and revision numbers with the actual intended values. Save one JSON object as `params.json`, then run `sturgis command AGENT_ID METHOD --params-file params.json`. Omit the file for `{}`. If acknowledgement is unknown, keep request and domain operation identities unchanged. For an acknowledged unavailable result, follow the feature-specific recovery instructions.

| Method | Example parameters or private-prompt command |
| --- | --- |
| `conversations.get` | `{"id":"CONVERSATION_ID"}` |
| `conversations.list` | `{"query":"maintenance","include_archived":true}` |
| `conversations.update` | `{"id":"CONVERSATION_ID","expected_revision":2,"title":"Corrected maintenance review"}` |
| `messages.events` | `{"conversation_id":"CONVERSATION_ID","after":0,"limit":100}` |
