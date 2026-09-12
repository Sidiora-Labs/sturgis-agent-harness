---
title: "Create and safely edit workspace files"
description: "Browse, create, read, edit, download, and delete files using workspace-relative paths and exact content hashes."
---

Files is the editable workspace of the selected agent. It differs from the immutable source library and generated artifact store. Use it for notes and deliverables that you want to edit; upload a source when preserving original bytes matters.

## Use the file browser

Open **Files**, browse folders, and filter entries. Choose a file to read it. Valid readable text opens in **File content**; binary or invalid UTF-8 data remains downloadable without pretending it has a text preview.

Choose **New file** or **Create your first file**. Enter **Workspace path**, supply **Initial content**, and choose **Create file**. Use a relative path such as `notes/maintenance.md`; the server rejects escapes and protected internal paths.

## Worked example: create, edit, and verify a note

Create `maintenance.md` with “Review dates against the original source.” Open it, change the wording, and choose **Save file**. The viewer displays the SHA-256 of the current bytes. Reopen it and choose **Download** to retrieve those bytes.

For CLI creation, save `write.json`:

```json
{"path":"hello.txt","data_base64":"SGVsbG8sIFN0dXJnaXMhCg=="}
```

```sh
sturgis command AGENT_ID files.write --params-file write.json
```

The base64 decodes to `Hello, Sturgis!` followed by a newline. Without `expected_sha256`, writing is create-only. Read it with `files.read` and `{"path":"hello.txt"}`; keep the returned SHA-256 before a later edit. For a replacement, add that exact value as `expected_sha256` and provide the new base64 bytes.

`files.list` accepts an optional path, defaulting to `.`. The UI opens folders; it does not provide a general filesystem administration shell, rename operation, or arbitrary host path picker.

## Delete deliberately

Choose **Delete file**, verify the selected file, and confirm. **Keep file** cancels the dialog. The command `files.delete` requires both `path` and its exact current `expected_sha256`. There is no consumer trash or undelete workflow; retain a copy or backup when you need one.

## Conflicts, bounds, and recovery

A save or delete checks the version you read. If another writer changed the file, reread it and reconcile your intended edit rather than dropping the hash check. An uncertain response should be inspected by reading the path and checking bytes/hash before you submit changed content.

The browser's file encoding flow rejects content above 4 MiB before sending it. Protected `.sturgis` internals, symlink escapes, vaults, other agent roots, and arbitrary server directories are unavailable through this interface. Directory creation can occur as part of a valid new nested file path; there is no general directory-management API.

A source download uses its attachment route and a generated result uses its artifact route. Both remain scoped to the owner and agent. Do not copy their IDs into a workspace path and expect an editable file. See [source library](../knowledge/source-library.md) and [backup/restore](../settings/backup-restore.md) for preserving those separate stores.

## Command examples

These are command-specific examples, not a script to run every operation. Replace uppercase IDs, example endpoints, timestamps, and revision numbers with the actual intended values. Save one JSON object as `params.json`, then run `sturgis command AGENT_ID METHOD --params-file params.json`. Omit the file for `{}`. If acknowledgement is unknown, keep request and domain operation identities unchanged. For an acknowledged unavailable result, follow the feature-specific recovery instructions.

| Method | Example parameters or private-prompt command |
| --- | --- |
| `files.delete` | `{"path":"hello.txt","expected_sha256":"CURRENT_SHA256"}` |
| `files.list` | `{"path":"."}` |
| `files.read` | `{"path":"hello.txt"}` |
| `files.write` | `{"path":"hello.txt","data_base64":"SGVsbG8K"}` |
