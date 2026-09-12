---
title: "Build and revise your source library"
description: "Upload original files, inspect extraction, index knowledge, revise documents, and retract indexed content without deleting sources."
---

The source library preserves original uploads. Knowledge documents add extracted text and indexed memory references on top of those sources. Keeping the two layers separate lets you remove an interpretation or index while retaining the original material.

## Upload and index in the interface

Open **Knowledge → Source library → Add a source**, or **Upload your first source**. Select or drop a file, then choose **Upload source**. Inspect its media type, byte size, and extraction status. **Download** retrieves the original through an authenticated route scoped to the selected agent.

Choose **Add to knowledge** on a source whose extraction is ready. The resulting document shows its revision and memory count. **Read source** opens the indexed text. Removing a document requires confirmation; **Keep knowledge** closes the dialog without retracting it.

## Worked example: correct an uploaded maintenance note

Upload `maintenance-v1.md` containing “The first maintenance visit was in April.” Add it to knowledge and read the text back. If the original author corrects the date, upload `maintenance-v2.md` as another source.

The web flow can remove the old document and add the corrected source as a new document. To preserve a single document identity across revisions, use `knowledge.add` through the CLI/API with the existing document ID and current revision. Save `revise-knowledge.json`:

```json
{"attachment_id":"CORRECTED_ATTACHMENT_ID","title":"Maintenance notes","document_id":"DOCUMENT_ID","expected_revision":1,"operation_id":"maintenance_revision_2"}
```

```sh
sturgis command AGENT_ID knowledge.add --params-file revise-knowledge.json --request-id maintenance_revision_2
sturgis command AGENT_ID knowledge.list
```

Inspect the current document with `knowledge.get` and `{"id":"DOCUMENT_ID"}`. Add `"revision":1` to retrieve its older knowledge revision. `knowledge.list` accepts a text query and bounded limit.

## Original bytes and extraction limits

Uploads are bounded to 4 MiB. Supported UTF-8 extraction covers plain text, Markdown, CSV, JSON, and HTML. HTML excludes scripts and styles. The extraction bound is 256 KiB; larger text is `deferred` with no extracted body. Unsupported formats or invalid UTF-8 remain downloadable but are not silently decoded. OCR, PDF, and office-document readers are not bundled.

`attachments.upload` requires a plain filename, canonical media type, base64 bytes, and stable operation ID. `attachments.list`, `attachments.get`, and `attachments.read` inspect metadata or bounded byte ranges. Source attachment IDs differ from artifact IDs; use the type requested by the command.

## Failure and recovery

Indexing reports native memory availability separately from saving a document. Do not assume a saved title means indexing succeeded. If the HTTP acknowledgement is unknown, retry with the same outer `request_id`, domain `operation_id`, and parameters so you can recover the original receipt.

If you received a successful HTTP envelope whose indexing result says native memory is unavailable, that outer request is already complete. After memory recovers, send the same `operation_id` and parameters with a **new outer `request_id`** to resume indexing. Reusing the old outer request ID only returns its saved unavailable result. For example, keep `maintenance_revision_2` inside `revise-knowledge.json` and rerun the command with `--request-id maintenance_revision_2_resume_1`.

`knowledge.delete` requires ID, current revision, and operation ID; it retracts indexed memory references while the original attachment remains. A stale revision requires rereading the document. If a single fact needs correction rather than replacing a source, use [memory correction](memory.md). Keep uncertain retries unchanged and use a new operation identity for a genuinely new revision.

## Command examples

These are command-specific examples, not a script to run every operation. Replace uppercase IDs, example endpoints, timestamps, and revision numbers with the actual intended values. Save one JSON object as `params.json`, then run `sturgis command AGENT_ID METHOD --params-file params.json`. Omit the file for `{}`. If acknowledgement is unknown, keep request and domain operation identities unchanged. For an acknowledged unavailable result, follow the feature-specific recovery instructions.

| Method | Example parameters or private-prompt command |
| --- | --- |
| `attachments.get` | `{"id":"ATTACHMENT_ID"}` |
| `attachments.list` | `{"limit":20}` |
| `attachments.read` | `{"id":"ATTACHMENT_ID","offset":0,"limit":4096}` |
| `attachments.upload` | `{"name":"hello.txt","media_type":"text/plain","data_base64":"SGVsbG8K","operation_id":"upload_hello_1"}` |
| `knowledge.add` | `{"attachment_id":"ATTACHMENT_ID","title":"Maintenance notes","operation_id":"index_maintenance_1"}` |
| `knowledge.delete` | `{"id":"DOCUMENT_ID","expected_revision":2,"operation_id":"retract_document_1"}` |
| `knowledge.get` | `{"id":"DOCUMENT_ID","revision":1}` |
| `knowledge.list` | `{"query":"maintenance","limit":20}` |
