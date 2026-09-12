---
title: "Command cookbook: all 95 consumer commands"
description: "Execute every public domain command with prerequisites, concrete CLI input, expected observations, and command-specific recovery."
---

Use this cookbook when you need a particular operation rather than a tour of the interface. Each recipe is independent. Running every mutation in order would change, cancel, revoke, or remove useful records. Choose the operation you intend, read its prerequisites, and inspect the returned observation before continuing.

## Invocation and retry conventions

Sign in first using the [CLI guide](../cli/using-the-cli.md), then replace `AGENT_ID` with an agent owned by that account. Uppercase identities, example URLs, hashes, and revision numbers are placeholders. Lowercase names such as `research` and `review_sources` are concrete examples, not preinstalled services. Use current records from your own installation; never fabricate an evidence event or use another owner's identifier.

Each shell block writes a small local parameters file and calls the actual installed CLI. These files contain ordinary example input, never vault secrets. Read commands omit `--request-id`, so repeating a read requests a fresh observation. Mutation examples supply an explicit request ID. Choose a new unique ID for a new intended mutation and retain it with the exact parameters if the acknowledgement is lost. Keep required domain identities such as `operation_id` and `message_id` unchanged during that investigation. A revision conflict requires rereading and making a new deliberate decision, not silently substituting a larger number under the previous request ID.

A received HTTP success containing a native-memory `unavailable` status is different from a lost acknowledgement. That outer response is already a completed receipt. After native recovery, submit a **new outer request ID with the same domain operation ID and unchanged domain parameters** to resume indexing or memory mutation. Reusing the completed outer ID would only replay the old unavailable response. If the HTTP acknowledgement itself is unknown, first retain both identities and resolve that original receipt.

The CLI prints the `data` from the `consumer/1` envelope or a typed error. Native-memory results have their own `status`, `data`, errors, and freshness; an outer success does not flatten those into successful recall. Inspect status fields before treating a result as complete. All domain commands can request a private worker and wake a suspended agent. Use `sturgis agents get AGENT_ID` for non-waking control status. Archive remains a durable worker-command fence.

The [command reference](commands.md) lists optional fields and non-domain HTTP routes, including account setup, lifecycle, events, downloads, backup, and restore. The recipes below cover the exact 95 public domain commands; they do not expose internal ingress or arbitrary tool execution.

## Runtime and usage

Feature guide: [Runtime and usage](../settings/usage-diagnostics.md).


### overview

Select the agent whose configuration you want to understand. This is the application overview, useful before deciding whether a missing response is caused by configuration or by current work. It requires no ready model.

```sh
sturgis command AGENT_ID overview
```

**Expected result.** Expect the agent ID, deployment mode, provider settings, usage, conversation count, memory availability, accepting flag, and registered tool descriptors. It is a compact application snapshot, not the multi-domain Work overview.

**Recovery.** If providers are absent, configure one; if memory is unavailable, inspect diagnostics separately. Do not infer worker health from the control HTTP health endpoint, or treat registered tools as already authorized successful operations.


### diagnostics.get

Use this after recording the failed conversation or work ID. You must own the agent, and the private runtime must be reachable; the command can start a suspended worker, so it is unsuitable for passive dormancy monitoring.

```sh
sturgis command AGENT_ID diagnostics.get
```

**Expected result.** Expect bounded diagnostics containing scope, admission pause state, native-memory availability, capabilities, recent runtime failures, and deployment information. Use these observations to locate the failed boundary, then inspect the relevant work record.

**Recovery.** If worker startup itself fails, this command cannot supply private diagnostics. Use agent metadata and operator launcher evidence instead. Recovering the worker is a separate lifecycle decision and does not reconcile an uncertain remote effect.


### usage.summary

Run this after actual work, or while investigating a timeout. Keep the work ID available so you can compare its direct consumption with held allocations. A provider subscription or billing connection is not required.

```sh
sturgis command AGENT_ID usage.summary
```

**Expected result.** Expect actual resource totals, receipt count, direct work usage, unresolved claims and resources, overruns, and active allocations. Tool usage receipts are reconciled into the projection. Monetary estimation remains null with settlement not configured.

**Recovery.** Do not add ancestor allocations to direct receipts as extra charges. An unresolved claim can remain held after a timeout. Inspect its work and actual terminal evidence; changing a budget or repeating an effect cannot establish missing consumption.


## Conversations

Feature guide: [Conversations](../conversations/sources-history-search.md).


### conversations.create

Create a new thread inside the already selected agent. A model need not be ready merely to create the conversation. Use a meaningful title; creating another conversation does not create another agent or private workspace.

```sh
cat > conversations-create.json <<'JSON'
{
  "title": "Maintenance review"
}
JSON
sturgis command AGENT_ID conversations.create --params-file conversations-create.json --request-id recipe_conversations_create_1
```

**Expected result.** Expect a new conversation record with its ID and revision. Save that ID for messages.send and later inspection. The new record has no accepted prompt until you submit a message explicitly.

**Recovery.** If the create acknowledgement is uncertain, retry the identical request ID before making another conversation. Duplicate titles are not reliable evidence of identity. Reconcile the returned ID rather than deleting a similarly named thread.


### conversations.list

Choose a search term that occurs in the intended conversation title or recorded user-message text. Assistant-output text is not searched by this command. Set include_archived true when you want a thread removed from the active sidebar.

```sh
cat > conversations-list.json <<'JSON'
{
  "query": "maintenance",
  "include_archived": true
}
JSON
sturgis command AGENT_ID conversations.list --params-file conversations-list.json
```

**Expected result.** Expect an items collection of matching conversation records. The example searches maintenance and includes archived records; an empty query lists conversations without that text filter. IDs and revisions are suitable inputs for get or update.

**Recovery.** An empty result is scoped to this agent and filter. Clear the query or include archived records before assuming deletion. Searching does not unarchive a thread or restore an archived agent's execution authority.


### conversations.get

Take the conversation ID from conversations.list or a successful creation response. Use this to inspect one thread before renaming it, checking accepted messages, or connecting a workflow to its history.

```sh
cat > conversations-get.json <<'JSON'
{
  "id": "CONVERSATION_ID"
}
JSON
sturgis command AGENT_ID conversations.get --params-file conversations-get.json
```

**Expected result.** Expect the conversation record together with its messages and turns. Separate an accepted input, persisted output, and terminal turn state; the presence of some text does not establish that the entire operation completed.

**Recovery.** For an unavailable ID, confirm the selected agent and list its conversations. Do not substitute a title for the ID. For continued event replay, use messages.events with a cursor rather than repeatedly interpreting the whole history as new input.


### conversations.update

Read the current conversation and revision first. This example renames an existing thread. Use the same command with archived true to archive it or archived false to restore a conversation to the active list.

```sh
cat > conversations-update.json <<'JSON'
{
  "id": "CONVERSATION_ID",
  "expected_revision": 2,
  "title": "Corrected maintenance review"
}
JSON
sturgis command AGENT_ID conversations.update --params-file conversations-update.json --request-id recipe_conversations_update_1
```

**Expected result.** Expect the updated conversation and a newer revision. Renaming changes presentation, while the conversation ID and recorded history remain. Conversation unarchive is supported and differs from the irreversible execution fence of agent archive.

**Recovery.** If the expected revision conflicts, reread the record and preserve any intervening rename or archive decision. Submit the reconciled change as a new request. Do not overwrite a concurrent update merely by guessing the next revision.


## Messages and replay

Feature guide: [Messages and replay](../conversations/steering-and-cancellation.md).


### messages.send

Use an existing active conversation. For this source-summary example, first upload and attach the intended source, or include actual attachment IDs in the optional attachments array. Choose a fresh message ID for this exact input.

```sh
cat > messages-send.json <<'JSON'
{
  "conversation_id": "CONVERSATION_ID",
  "message_id": "maintenance_prompt_1",
  "text": "Summarize the maintenance source.",
  "kind": "prompt"
}
JSON
sturgis command AGENT_ID messages.send --params-file messages-send.json --request-id recipe_messages_send_1
```

**Expected result.** Expect durable input admission and a work identity, followed separately by output and terminal events. The shown kind prompt creates normal work; kind steer records direction for active work without erasing previously emitted output.

**Recovery.** If acknowledgement is lost, retain message ID, request ID, text, kind, and attachments. A new message ID can create another task. Provider failure is a later execution observation: inspect events and readiness before deliberately requesting new work.


### messages.cancel

Identify the conversation whose active work you want stopped. The message_id here identifies the cancellation request itself; it is not the ID of the earlier prompt. Keep it stable if that cancellation acknowledgement is lost.

```sh
cat > messages-cancel.json <<'JSON'
{
  "conversation_id": "CONVERSATION_ID",
  "message_id": "maintenance_cancel_1"
}
JSON
sturgis command AGENT_ID messages.cancel --params-file messages-cancel.json --request-id recipe_messages_cancel_1
```

**Expected result.** Expect a persisted cancellation input. The runtime fences further execution and requests cancellation at supported boundaries. Observe subsequent work and turn events to establish how execution ended; receiving this admission is not terminal cleanup proof.

**Recovery.** If a remote operation was already submitted, its effect may remain unknown even when local work stops. Do not send a new effectful prompt to hide that state. Inspect the original attempt and integration evidence before deciding what to do next.


### messages.events

Use a conversation ID to limit replay, or omit it for the agent's public conversation stream. Start after zero for available history, then retain the returned cursor for subsequent pages instead of repeatedly processing old events.

```sh
cat > messages-events.json <<'JSON'
{
  "conversation_id": "CONVERSATION_ID",
  "after": 0,
  "limit": 100
}
JSON
sturgis command AGENT_ID messages.events --params-file messages-events.json
```

**Expected result.** Expect items, a cursor, and has_more. Read another page after the returned cursor while has_more is true. Limit is bounded from one to 1,000; event IDs identify records, while the cursor identifies a stream position.

**Recovery.** The public stream is intentionally bounded and is not every private journal event. Reconnecting only reads history; it does not rerun tools. For continuous polling use the CLI events command, preserving the conversation and latest cursor.


## Attachments and immutable artifacts

Feature guide: [Attachments and immutable artifacts](../knowledge/source-library.md).


### attachments.upload

Prepare a plain filename, canonical media type, and bytes within the 4 MiB upload bound. The supplied base64 is Hello followed by a newline. Choose one operation ID for these exact original bytes and metadata.

```sh
cat > attachments-upload.json <<'JSON'
{
  "name": "hello.txt",
  "media_type": "text/plain",
  "data_base64": "SGVsbG8K",
  "operation_id": "upload_hello_1"
}
JSON
sturgis command AGENT_ID attachments.upload --params-file attachments-upload.json --request-id recipe_attachments_upload_1
```

**Expected result.** Expect immutable source metadata including the attachment ID, original artifact reference, byte size, hash, and extraction state. Supported small UTF-8 text becomes available for source inclusion; uploading alone does not create a knowledge document.

**Recovery.** An unsupported or deferred extraction still preserves the original download. Do not change bytes under the same operation identity. For a corrected original, upload a new attachment, then deliberately revise the knowledge document that should refer to it.


### attachments.list

Use this to find source IDs after an upload or when rebuilding a conversation's attachment selection. The example requests twenty records; the command does not search arbitrary host directories or other agents' libraries.

```sh
cat > attachments-list.json <<'JSON'
{
  "limit": 20
}
JSON
sturgis command AGENT_ID attachments.list --params-file attachments-list.json
```

**Expected result.** Expect a bounded items collection of source metadata. Inspect each filename, media type, size, and extraction state before choosing it. Attachment IDs address source records; artifact IDs address immutable byte storage underneath those records.

**Recovery.** If the desired source is absent, verify the agent and use a larger supported limit where appropriate. Do not assume an uploaded binary was interpreted merely because its metadata appears. Use attachments.get for the extraction observation.


### attachments.get

Copy an attachment ID from upload or list. This command is appropriate when you need the original artifact reference, media type, size, or extraction status before deciding whether to index or transcribe the source.

```sh
cat > attachments-get.json <<'JSON'
{
  "id": "ATTACHMENT_ID"
}
JSON
sturgis command AGENT_ID attachments.get --params-file attachments-get.json
```

**Expected result.** Expect metadata for the owned original. The returned artifact ID can be needed by a media tool, while knowledge.add requires the attachment ID. Neither field is a workspace filename or a freely selectable filesystem path.

**Recovery.** If the source is unavailable, verify its owning agent and exact ID. If extraction is unsupported, use its authenticated download or a compatible separate media adapter; repeatedly requesting metadata does not install a decoder.


### attachments.read

Obtain the attachment ID from metadata and choose a nonnegative byte offset. The example reads at most 4,096 original bytes, useful for checking a source prefix without claiming that the entire file was included.

```sh
cat > attachments-read.json <<'JSON'
{
  "id": "ATTACHMENT_ID",
  "offset": 0,
  "limit": 4096
}
JSON
sturgis command AGENT_ID attachments.read --params-file attachments-read.json
```

**Expected result.** Expect base64 bytes and explicit start, end, and total-byte information. Decode according to the original media type. Advance using the returned end to request the next range; byte offsets are not Unicode character positions.

**Recovery.** If a requested offset exceeds the source, reread metadata rather than silently wrapping the range. Invalid UTF-8 remains binary evidence. For a full ordinary upload, use the authenticated attachment download instead of treating a partial range as complete.


### artifacts.read

Take a real artifact ID from attachment metadata or a tool result, such as generated audio or a screenshot. Use this recipe for immutable bytes; it does not edit the artifact or require a workspace file with that name.

```sh
cat > artifacts-read.json <<'JSON'
{
  "id": "ARTIFACT_ID",
  "offset": 0,
  "limit": 4096
}
JSON
sturgis command AGENT_ID artifacts.read --params-file artifacts-read.json
```

**Expected result.** Expect bounded base64 bytes with start, end, and total_bytes plus artifact identification. Read successive ranges when the artifact is larger than the requested limit. A successful first range is evidence only for the bytes actually returned.

**Recovery.** If the full authenticated download rejects an artifact larger than its 4 MiB bound, use these bounded ranges. Confirm ownership and type before decoding. Do not manufacture a path or treat an arbitrary byte sequence as a valid image or audio file.


## Workspace files

Feature guide: [Workspace files](../files/workspace.md).


### files.list

Choose a workspace-relative directory. The example lists the public workspace root. Internal .sturgis entries and path escapes are excluded; this interface does not expose the installation root, vault directory, or another agent's files.

```sh
cat > files-list.json <<'JSON'
{
  "path": "."
}
JSON
sturgis command AGENT_ID files.list --params-file files-list.json
```

**Expected result.** Expect an object with path and entries, not a bare array or items field. Entries distinguish directories and files. Use the returned names within the selected directory, and call files.read only for the intended regular file.

**Recovery.** If a directory is unavailable, verify its relative path and parent entries. Do not replace the path with an absolute server directory to bypass scope. There is no consumer directory-management or arbitrary shell command hidden behind listing.


### files.read

Choose a regular public workspace file found through files.list. Read it before editing or deleting so that the subsequent mutation can protect the exact bytes you reviewed, even if another writer is active.

```sh
cat > files-read.json <<'JSON'
{
  "path": "hello.txt"
}
JSON
sturgis command AGENT_ID files.read --params-file files-read.json
```

**Expected result.** Expect path, data_base64, bytes, and sha256. Decode the bytes for a text preview only when valid for the file. Save the exact SHA-256 as the precondition for a later replacement or deletion.

**Recovery.** If another writer changes the file after this read, the protected mutation should fail. Read again and reconcile your intended change. A read error or invalid text encoding does not authorize replacing the file with an empty document.


### files.write

Use a new public workspace path for this create example. The base64 writes Hello and a newline. To replace an existing file, first read it and add its current exact hash as expected_sha256.

```sh
cat > files-write.json <<'JSON'
{
  "path": "hello.txt",
  "data_base64": "SGVsbG8K"
}
JSON
sturgis command AGENT_ID files.write --params-file files-write.json --request-id recipe_files_write_1
```

**Expected result.** Expect a file-operation receipt identifying the written bytes and before/after observations. Omitting expected_sha256 is create-only, so this recipe cannot silently overwrite an existing hello.txt. Valid nested paths can create their needed parent directories.

**Recovery.** On an existing-file or preimage conflict, inspect current bytes and choose a reconciled replacement with its observed hash. On an unknown acknowledgement, recover the original request and verify the path before starting another mutation with changed content.


### files.delete

Read the file and preserve any copy you need before deleting. Replace CURRENT_SHA256 with the exact returned 64-character SHA-256, not the hash of an older version or the string shown in this example.

```sh
cat > files-delete.json <<'JSON'
{
  "path": "hello.txt",
  "expected_sha256": "CURRENT_SHA256"
}
JSON
sturgis command AGENT_ID files.delete --params-file files-delete.json --request-id recipe_files_delete_1
```

**Expected result.** Expect a deletion receipt only when the current bytes match the precondition. The consumer has no trash or undelete command. Deleting a workspace file does not remove a separate source attachment or immutable artifact with similar content.

**Recovery.** If the preimage fails, reread and review the new version before deciding again. If the acknowledgement is lost, resolve the original request and inspect the path; an already absent file is not evidence that a second deletion should execute.

## Knowledge documents and memory

Feature guide: [Knowledge documents and memory](../knowledge/source-library.md).


### knowledge.add

Upload a supported source and confirm extraction is ready. The example creates a new knowledge document from that attachment. For a new revision of an existing document, also provide document_id and its current expected_revision.

```sh
cat > knowledge-add.json <<'JSON'
{
  "attachment_id": "ATTACHMENT_ID",
  "title": "Maintenance notes",
  "operation_id": "index_maintenance_1"
}
JSON
sturgis command AGENT_ID knowledge.add --params-file knowledge-add.json --request-id recipe_knowledge_add_1
```

**Expected result.** Expect document metadata and a separate indexing result. Saving the document and acknowledging every native record are distinct observations. A successful replacement retains knowledge history and retracts the superseded document's indexed memory references.

**Recovery.** If indexing returns unavailable in an acknowledged response, restore native availability and use a new outer request ID with the same operation ID and identical parameters. This resumes the prepared operation instead of creating a second document or revision.


### knowledge.list

Choose a term appearing in document titles or extracted text. This lists current nondeleted knowledge documents; it does not search all conversation history or enumerate the immutable source library underneath those documents.

```sh
cat > knowledge-list.json <<'JSON'
{
  "query": "maintenance",
  "limit": 20
}
JSON
sturgis command AGENT_ID knowledge.list --params-file knowledge-list.json
```

**Expected result.** Expect items without full document text and a truncated flag. The example returns up to twenty matches for maintenance. Take an ID and current revision from the result before requesting the document or preparing a versioned change.

**Recovery.** If a result is missing, clear the filter, inspect its original attachment, and check whether the knowledge document was deleted. Native recall availability and this saved-document list are separate; a listed title does not establish successful indexing.


### knowledge.get

Use a document ID from knowledge.list. The example explicitly requests revision one to inspect historical source text. Omit revision when you need the latest record and concurrency revision for a new edit or deletion.

```sh
cat > knowledge-get.json <<'JSON'
{
  "id": "DOCUMENT_ID",
  "revision": 1
}
JSON
sturgis command AGENT_ID knowledge.get --params-file knowledge-get.json
```

**Expected result.** Expect the selected document text, source attachment relationship, memory references, and revision. Historical retrieval also indicates whether the current document is deleted. Reading an older version does not reactivate its old memory index.

**Recovery.** If the requested revision is absent, inspect the current document and recorded history instead of assuming revisions can be invented. When checking what the agent currently recalls, query memory separately; historical text is not current retrieval authority.


### knowledge.delete

Inspect the current document and confirm that removing its knowledge index is intended. Supply the current expected_revision and a stable operation ID. This is not an attachment-delete command or a way to erase all historical evidence.

```sh
cat > knowledge-delete.json <<'JSON'
{
  "id": "DOCUMENT_ID",
  "expected_revision": 2,
  "operation_id": "retract_document_1"
}
JSON
sturgis command AGENT_ID knowledge.delete --params-file knowledge-delete.json --request-id recipe_knowledge_delete_1
```

**Expected result.** Expect the document marked deleted and a separate native indexing/retraction result for its current and retired memory references. Original attachments and historical document material remain. The same deleted identity cannot simply be revised back into a live document.

**Recovery.** Inspect partial or unavailable native results before concluding recall has been withdrawn. After native recovery, resume the exact domain operation with a new outer request ID. To index a source again deliberately, create a new knowledge document identity.


### memory.query

Choose a term or concept you want to recall. A model provider need not be ready, but the native memory process must be available. The example uses lexical text only; embeddings require a compatible model and vector dimension.

```sh
cat > memory-query.json <<'JSON'
{
  "text": "maintenance",
  "limit": 20
}
JSON
sturgis command AGENT_ID memory.query --params-file memory-query.json
```

**Expected result.** Expect a native status envelope with scored records, provenance, revision, view, watermark, and any partial/truncated indication. At most one hundred results are supported. The optional at timestamp filters validity; it does not reconstruct knowledge as known at that past time.

**Recovery.** An unavailable status is not an empty successful search. Incompatible embedding spaces can yield partial results while lexical/concept matches remain usable. Inspect errors and freshness; use memory.get for an explicit older canonical revision instead of expecting query to substitute it.


### memory.get

Copy an actual memory ID from query results. The example requests canonical revision one, which is useful when understanding a correction. Omit revision to retrieve the latest canonical version rather than relying on an old search result.

```sh
cat > memory-get.json <<'JSON'
{
  "id": "MEMORY_ID",
  "revision": 1
}
JSON
sturgis command AGENT_ID memory.get --params-file memory-get.json
```

**Expected result.** Expect a native envelope containing the record with content, sources, kind, timestamps, revision, and current retraction information. Historical inspection preserves evidence of what was recorded, but current retraction still applies and does not become undone by reading history.

**Recovery.** If the record is absent, verify both identity and revision and check the native status. A derived search result is not automatically an editable canonical record. Inspect its sources and use the supported original record or knowledge-document workflow where appropriate.


### memory.correct

Verify the original evidence, then retrieve the current unretracted canonical record. Supply its revision and the complete intended correction. The example changes a date; it does not claim the server independently checked the corrected external source.

```sh
cat > memory-correct.json <<'JSON'
{
  "id": "MEMORY_ID",
  "content": "The corrected source identifies May.",
  "expected_revision": 1,
  "operation_id": "correct_date_1"
}
JSON
sturgis command AGENT_ID memory.correct --params-file memory-correct.json --request-id recipe_memory_correct_1
```

**Expected result.** Expect a new canonical revision recorded as a belief with User correction content and provenance to the user-correction event. Original revision history remains. The correction establishes what the owner supplied, not fresh independent verification of every statement it contains.

**Recovery.** A retracted record cannot be corrected in place through this command. For conflicts, reread before forming a new correction. For a received native-unavailable result, resume the same domain operation with a new outer request after recovery, preserving its original expected revision and content.


### memory.retract

Inspect the memory and decide whether you mean one revision or its entire identity. The example explicitly withdraws revision two. Removing revision from the parameters suppresses all revisions of this identity, including future revisions.

```sh
cat > memory-retract.json <<'JSON'
{
  "id": "MEMORY_ID",
  "reason": "Source withdrawn",
  "revision": 2,
  "operation_id": "retract_date_1"
}
JSON
sturgis command AGENT_ID memory.retract --params-file memory-retract.json --request-id recipe_memory_retract_1
```

**Expected result.** Expect a native retraction result plus the recorded source event for your reason. Current recall respects that withdrawal while historical evidence remains. There is no public undo-retraction command; retraction is stronger than merely hiding a search result in the interface.

**Recovery.** Preserve the reason and operation identity on ambiguous acknowledgement. Do not omit revision as a convenience if you only intended to withdraw one version. If new information is needed after an all-identity retraction, use a deliberately new supported source/record identity.


## Goals

Feature guide: [Goals](../work/goals.md).


### goals.create

State a desired outcome and choose explicit nonnegative resource limits. The example reserves no execution by itself; it creates the intention that later scheduled or planned work can reference through goal_id.

```sh
cat > goals-create.json <<'JSON'
{
  "objective": "Prepare a source-based brief",
  "limits": {
    "tokens": 16000,
    "iterations": 2,
    "elapsed_ms": 60000,
    "tool_calls": 2
  }
}
JSON
sturgis command AGENT_ID goals.create --params-file goals-create.json --request-id recipe_goals_create_1
```

**Expected result.** Expect a proposed goal with an ID, revision, limits, usage, and reservation state. Optional criteria define what later counts as achievement. No criterion means the goal cannot be certified achieved solely from a narrative or a state-change request.

**Recovery.** If the goal already exists after an uncertain acknowledgement, recover the original request instead of creating another intention. For execution, activate it and attach actual work. Increasing limits does not install missing tools, readiness, or outcome checks.


### goals.list

Use this before editing, activating, pausing, or archiving goals. It reads goal records for the selected agent and is independent of whether a provider can currently run the next model turn.

```sh
sturgis command AGENT_ID goals.list
```

**Expected result.** Expect goals with current states, revisions, budgets, and evidence. Distinguish proposed intentions, live states, and terminal outcomes. Inspect usage plus reservations when deciding whether another associated task fits the remaining limits.

**Recovery.** Archived goals remain in this API list with their archived flag, even when a web view hides them. Do not reuse their IDs or add child and ancestor budget totals as independent consumption; inspect direct work usage for factual accounting.


### goals.edit

Retrieve a nonterminal goal and its current revision. The example changes only the objective. To change limits or criteria, supply those fields explicitly after reviewing existing usage, outstanding reservations, and what the revised outcome is meant to establish.

```sh
cat > goals-edit.json <<'JSON'
{
  "id": "GOAL_ID",
  "expected_revision": 2,
  "objective": "Prepare a corrected source-based brief"
}
JSON
sturgis command AGENT_ID goals.edit --params-file goals-edit.json --request-id recipe_goals_edit_1
```

**Expected result.** Expect the same goal identity with a new revision and the selected fields updated. Previously observed usage is preserved. New limits must not fall below usage plus outstanding reservations, and terminal goals cannot be edited.

**Recovery.** On budget_exceeded, inspect the held and consumed amounts rather than erasing old receipts. On a revision conflict, compare the current objective and checks before submitting a fresh decision. Criteria changes should describe the intended outcome, not disguise missing evidence.


### goals.transition

Use the shown active transition only for a proposed or otherwise resumable goal, with its current revision. Activation permits eligible associated resource reservations; it does not automatically create a plan, schedule, or model conversation.

```sh
cat > goals-transition.json <<'JSON'
{
  "id": "GOAL_ID",
  "state": "active",
  "expected_revision": 1
}
JSON
sturgis command AGENT_ID goals.transition --params-file goals-transition.json --request-id recipe_goals_transition_1
```

**Expected result.** Expect the updated goal state. Pausing or ending a goal fences associated nonterminal work and pauses active associated schedules. Achievement requires explicit checks and matching scoped evidence; failed, blocked, or abandoned transitions require an explanatory reason.

**Recovery.** A transition outside the state graph is rejected. Resolve the actual blocked condition before reactivation. Returning a goal to active does not automatically resume schedules paused by the earlier fence; inspect and deliberately resume those desired schedules separately.


### goals.archive

Use a goal already in achieved, failed, or abandoned state. Read its current revision and verify you intend to remove it from normal active goal management. Archiving a live goal is rejected.

```sh
cat > goals-archive.json <<'JSON'
{
  "id": "TERMINAL_GOAL_ID",
  "expected_revision": 4
}
JSON
sturgis command AGENT_ID goals.archive --params-file goals-archive.json --request-id recipe_goals_archive_1
```

**Expected result.** Expect the existing terminal goal marked archived with a new revision. Its recorded outcome, usage, and evidence are retained; archive does not convert failure to achievement or delete the supporting history.

**Recovery.** If the goal is still live, first make the appropriate evidence-supported terminal transition rather than using archive as cancellation. If the revision changes, reread. Agent archive is a different operation with its own runtime and ownership fence.


## Commitments and durable waits

Feature guide: [Commitments and durable waits](../work/commitments.md).


### commitments.create

Describe the responsibility and intended beneficiary. The example uses owner, which is also the consumer default. Optional criteria should specify real fulfillment checks; an optional expiry is a time for assessing overdue responsibility, not automatic execution.

```sh
cat > commitments-create.json <<'JSON'
{
  "description": "Review corrected dates",
  "beneficiary": "owner"
}
JSON
sturgis command AGENT_ID commitments.create --params-file commitments-create.json --request-id recipe_commitments_create_1
```

**Expected result.** Expect a proposed commitment with an ID, revision, acceptance state, and progress. Nothing has been accepted or scheduled solely by creation. Keep its revision for an explicit transition to accepted when the responsibility is actually taken on.

**Recovery.** If creation acknowledgement is uncertain, recover the same request before making another promise. Use a workflow or schedule to arrange model execution; a commitment label does not create a hidden background task or guarantee work by its expiry.


### commitments.list

Use this when reviewing accepted responsibilities, checking blocked progress, or finding the current revision and wait identity. It reads commitment state without requiring you to infer responsibility from conversation wording.

```sh
sturgis command AGENT_ID commitments.list
```

**Expected result.** Expect commitment records including computed overdue status. Acceptance state and progress are distinct: an accepted responsibility may be pending, in progress, blocked, or waiting. Overdue does not automatically mean failed, fulfilled, or cancelled.

**Recovery.** If a waiting commitment has not resumed, inspect its registered trigger and actual source observation. Repeatedly listing does not manufacture a fired wake. For an obligation no longer intended, make an explicit cancellation with the current revision and reason.


### commitments.transition

Read the proposal and use its current revision for the shown accepted transition. The state change makes an explicit responsibility; it still does not supply compute, schedule a task, or prove any result.

```sh
cat > commitments-transition.json <<'JSON'
{
  "id": "COMMITMENT_ID",
  "state": "accepted",
  "expected_revision": 1
}
JSON
sturgis command AGENT_ID commitments.transition --params-file commitments-transition.json --request-id recipe_commitments_transition_1
```

**Expected result.** Expect accepted state and updated revision. Proposed commitments can be cancelled; accepted ones can be fulfilled, cancelled, or failed through permitted transitions. Fulfillment requires declared checks and actual evidence, while cancellation/failure need an explanatory reason.

**Recovery.** Do not mark a responsibility fulfilled merely to dismiss overdue or blocked status. Inspect the supporting event IDs and checks. If terminal, create a new responsibility for a new intention instead of treating terminal labels as reversible progress controls.


### commitments.progress

Use an accepted commitment without an active durable wait. The example marks it blocked with a concrete reason. Supported manual progress values are pending, in_progress, and blocked; waiting is established through commitments.wait.

```sh
cat > commitments-progress.json <<'JSON'
{
  "id": "COMMITMENT_ID",
  "progress": "blocked",
  "expected_revision": 2,
  "reason": "Waiting for corrected source"
}
JSON
sturgis command AGENT_ID commitments.progress --params-file commitments-progress.json --request-id recipe_commitments_progress_1
```

**Expected result.** Expect a progress update on the same accepted responsibility. Blocked records why work cannot proceed but does not register a wake trigger or necessarily pause all associated execution. Keep the resulting revision for a later deliberate progress change.

**Recovery.** If the commitment is waiting, resolve its wait or cancel the responsibility rather than overwriting progress. If blocked by missing source material, obtain the source first. Changing the label to in_progress does not validate that the dependency was satisfied.


### commitments.wait

Use an accepted responsibility with no existing wait. Select an actual conversation for the user_response trigger. Replace after_seq zero with the observed current cursor if you intend to wait only for a future response.

```sh
cat > commitments-wait.json <<'JSON'
{
  "id": "COMMITMENT_ID",
  "expected_revision": 2,
  "trigger": {
    "kind": "user_response",
    "conversation_id": "CONVERSATION_ID"
  },
  "after_seq": 0
}
JSON
sturgis command AGENT_ID commitments.wait --params-file commitments-wait.json --request-id recipe_commitments_wait_1
```

**Expected result.** Expect the commitment in waiting progress with a durably registered wait identity. The wait is armed before execution can yield. Earlier eligible events may satisfy a zero cursor; the trigger name does not itself install a producer or remote connector.

**Recovery.** If a wait already exists, inspect that dependency instead of replacing it blindly. A timed-out or merely armed wait cannot be resumed as fired. Other trigger kinds need their exact producer identifiers and matching operational observations; see the commitment guide.


### commitments.resume

Retrieve the accepted waiting commitment and its current revision after the registered trigger has actually fired. This is the follow-up to a matching durable wait, not a general way to restart any blocked responsibility.

```sh
cat > commitments-resume.json <<'JSON'
{
  "id": "COMMITMENT_ID",
  "expected_revision": 3
}
JSON
sturgis command AGENT_ID commitments.resume --params-file commitments-resume.json --request-id recipe_commitments_resume_1
```

**Expected result.** Expect wait_id and trigger cleared and progress returned to in_progress. The service checks the exact wait belongs to this commitment and is fired. Resumption acknowledges that dependency; it does not automatically create a model task.

**Recovery.** If invalid_wait is returned, inspect the source condition and registered wait rather than fabricating an event. Expiry alone is not a successful trigger. If the responsibility is no longer appropriate, cancel it explicitly instead of forcing resumption.


## Schedules

Feature guide: [Schedules](../work/schedules.md).


### schedules.create

Choose an existing active conversation, actual prompt, and positive model-work allocation. The example schedules weekday mornings in Europe/Berlin, with Monday zero through Friday four, and an explicit first-occurrence daylight-saving fold policy.

```sh
cat > schedules-create.json <<'JSON'
{
  "conversation_id": "CONVERSATION_ID",
  "text": "Review maintenance notes",
  "spec": {
    "kind": "calendar",
    "timezone": "Europe/Berlin",
    "hour": 9,
    "minute": 0,
    "weekdays": [
      0,
      1,
      2,
      3,
      4
    ],
    "fold": "first"
  },
  "resources": {
    "tokens": 16000,
    "iterations": 2,
    "elapsed_ms": 60000,
    "tool_calls": 2
  }
}
JSON
sturgis command AGENT_ID schedules.create --params-file schedules-create.json --request-id recipe_schedules_create_1
```

**Expected result.** Expect an active schedule with its ID, current revision, next due instant, and bounded resources. Each occurrence admits work separately. Creation does not establish that the model is ready or that the future prompt will succeed.

**Recovery.** Missed policies are skip, run_once, and replay_bounded; consumer replay uses a ten-occurrence cap. Nonexistent local times are skipped. For a missing result, inspect due work and provider/permission evidence rather than creating a duplicate schedule after a lost acknowledgement.


### schedules.list

Use this before changing a timer or checking whether future work can wake a suspended agent. Keep the conversation and goal relationship in view so you know where each occurrence will be admitted.

```sh
sturgis command AGENT_ID schedules.list
```

**Expected result.** Expect schedule records with rule, state, revision, next due time, resources, and occurrence/catch-up observations. A past due timestamp is an admission opportunity, not a receipt proving a completed model turn or successful external effect.

**Recovery.** If a schedule is paused by a goal or permission fence, resolve that underlying condition before resuming it. If it is completed or cancelled, it cannot be restarted. Inspect associated work to distinguish a missed timer from failed execution.


### schedules.state

Read the schedule's current revision. The example pauses a live schedule; use active to resume an eligible paused one or cancelled to terminate it. Neither state nor revision should be guessed from an old screenshot.

```sh
cat > schedules-state.json <<'JSON'
{
  "id": "SCHEDULE_ID",
  "state": "paused",
  "expected_revision": 1
}
JSON
sturgis command AGENT_ID schedules.state --params-file schedules-state.json --request-id recipe_schedules_state_1
```

**Expected result.** Expect the updated schedule state. Pause stops future occurrence admission while retaining its rule. Cancel is terminal. Changing the schedule does not automatically settle an occurrence that already became a work record.

**Recovery.** For an already admitted occurrence, inspect work and use work.cancel if you intend to stop it. Completed or cancelled schedules reject restart; create a new timer for a new intention. Resumption remains subject to missed-run policy and current authority.

## Workflow plans

Feature guide: [Workflow plans](../work/workflows.md).


### workflows.create

Define the step descriptions, dependency graph, and shared budget before creating a plan. This one-step example has its own allocation matching the shared allowance. Omitted assignee defaults to the agent; creation alone does not start execution.

```sh
cat > workflows-create.json <<'JSON'
{
  "steps": [
    {
      "id": "review",
      "description": "Review the source",
      "dependencies": [],
      "budget": {
        "tokens": 16000,
        "iterations": 2,
        "elapsed_ms": 60000,
        "tool_calls": 2
      }
    }
  ],
  "budget": {
    "tokens": 16000,
    "iterations": 2,
    "elapsed_ms": 60000,
    "tool_calls": 2
  }
}
JSON
sturgis command AGENT_ID workflows.create --params-file workflows-create.json --request-id recipe_workflows_create_1
```

**Expected result.** Expect a versioned active plan with normalized steps and completion checks. Consumer steps without explicit checks receive a model-turn completion check whose external outcome remains unasserted. Add real outcome checks when the task requires evidence beyond finished inference.

**Recovery.** Cycles, unknown dependencies, invalid assignments, and unsupported fields are rejected. Use description, not title or text. For multiple steps, budget both the shared total and each allocation deliberately; a retry attempt consumes cumulative allocation rather than resetting the plan's allowance.


### workflows.list

Use this to discover saved plans before choosing one to run, pause, revise, or inspect. The list belongs to the selected agent; a goal or commitment is not itself a workflow record.

```sh
sturgis command AGENT_ID workflows.list
```

**Expected result.** Expect plan records with IDs, revisions, lifecycle states, and step definitions. Follow up with workflows.get when you need execution attempts and completion_basis, rather than assuming the list alone certifies every dependency or external result.

**Recovery.** If a plan is terminal, do not try to revive it by changing its display label. Create a deliberate new plan when the intention changes. Missing provider readiness can prevent execution while saved plans remain available for inspection.


### workflows.get

Take a real plan ID from creation or listing. Inspect it before recovering a failed step, because the plan state, individual step state, and recorded execution run can describe different parts of the same interruption.

```sh
cat > workflows-get.json <<'JSON'
{
  "id": "PLAN_ID"
}
JSON
sturgis command AGENT_ID workflows.get --params-file workflows-get.json
```

**Expected result.** Expect plan details, recorded run/attempt evidence, and a completion_basis for each step. Model-turn basis confirms a completed inference turn with external outcome unasserted; outcome_checks describes the explicitly supplied completion criteria.

**Recovery.** If an attempt failed, inspect its actual work and schedule IDs. A blocked step is not pending, and a successful sibling does not satisfy its dependency. Do not mark agent steps completed manually to make the plan appear finished.


### workflows.revise

Read a live plan with no in-progress steps and supply the complete desired step list plus a reason. The example applies only to a one-step plan; including it verbatim in a larger plan would replace that larger definition.

```sh
cat > workflows-revise.json <<'JSON'
{
  "id": "PLAN_ID",
  "expected_revision": 1,
  "steps": [
    {
      "id": "review",
      "description": "Review the corrected source",
      "budget": {
        "tokens": 16000,
        "iterations": 2,
        "elapsed_ms": 60000,
        "tool_calls": 2
      }
    }
  ],
  "reason": "Source corrected"
}
JSON
sturgis command AGENT_ID workflows.revise --params-file workflows-revise.json --request-id recipe_workflows_revise_1
```

**Expected result.** Expect a new plan revision. Steps whose description, dependencies, assignee, checks, expectation, and budget are unchanged keep their state and evidence. Deliberately changed definitions are normalized as new pending work while execution history remains available.

**Recovery.** After interruption, an unchanged blocked step stays blocked. Review why it failed before revising its definition. Cumulative attempt allocations remain in the run, so revision cannot erase spent allowance; if the shared budget cannot fit more work, plan a new bounded intention.


### workflows.run

Choose an active plan and an existing nonarchived conversation. Supply the plan's current revision. Check provider readiness, associated goal state, resources, and required permissions before asking the monitor to admit ready steps.

```sh
cat > workflows-run.json <<'JSON'
{
  "id": "PLAN_ID",
  "conversation_id": "CONVERSATION_ID",
  "expected_revision": 1
}
JSON
sturgis command AGENT_ID workflows.run --params-file workflows-run.json --request-id recipe_workflows_run_1
```

**Expected result.** Expect a recorded execution run and admission of eligible pending agent steps through real scheduled/model work. Dependencies must be completed. Once a run records its conversation, the plan cannot later switch execution to a different conversation.

**Recovery.** Paused plans must first return to active. Blocked steps are not automatically reset by running again; inspect and revise deliberately. A cancelled attempt or exhausted cumulative allocation remains evidence, and duplicating Run is not a shortcut around those conditions.


### workflows.state

Retrieve the current plan revision. Use the shown paused transition for an active plan. Active resumes a paused plan; abandoned requires a reason and ends a live intention. These are plan lifecycle states, separate from step progress.

```sh
cat > workflows-state.json <<'JSON'
{
  "id": "PLAN_ID",
  "state": "paused",
  "expected_revision": 2
}
JSON
sturgis command AGENT_ID workflows.state --params-file workflows-state.json --request-id recipe_workflows_state_1
```

**Expected result.** Expect an updated plan lifecycle. Pausing or abandoning fences further assignment and requests cancellation of active attempts. A pause before any step admission differs from interrupting a step already running; inspect what actually happened before resuming.

**Recovery.** Interrupted agent attempts can become blocked. Active followed by Run only admits pending eligible steps, so it cannot reset those blocked definitions. Review attempts and remaining allocation; deliberately revise affected definitions or create a new bounded plan when necessary.


### workflows.step

This recipe requires an active plan with a pending human_review step explicitly assigned as {"kind":"user"} and completed dependencies. Create that definition with workflows.create or revise a suitable live plan before using its ID here.

```sh
cat > workflows-step.json <<'JSON'
{
  "id": "USER_PLAN_ID",
  "step_id": "human_review",
  "state": "in_progress",
  "expected_revision": 1
}
JSON
sturgis command AGENT_ID workflows.step --params-file workflows-step.json --request-id recipe_workflows_step_1
```

**Expected result.** Expect that user-assigned step to enter in_progress. The consumer rejects manual in_progress or completed for agent/child assignments; use workflows.run for those. Completing a user step still requires its explicit checks and actual scoped evidence.

**Recovery.** If dependency_pending appears, inspect the predecessor result rather than skipping its evidence. Blocked and skipped transitions need a reason. Skipping a predecessor does not count as completed for dependent steps, so revise the plan intentionally if its dependency structure has changed.


## Execution records

Feature guide: [Execution records](../work/overview.md).


### work.overview

Use this when you need one view of the agent's work domains rather than provider settings. It can help connect a pending task to its goal, commitment, timer, workflow, child, or initiative proposal.

```sh
sturgis command AGENT_ID work.overview
```

**Expected result.** Expect work, goals, commitments, schedules, workflows, children, and inbox collections. These are records with separate ownership and lifecycles. A proposed goal or pending inbox item does not become an execution receipt merely by appearing beside work.

**Recovery.** Inspect the specific record whose state is uncertain with its dedicated get/list command. For example, use workflows.get for attempt evidence and usage.summary for resource receipts; the broad overview does not replace those narrower observations.


### work.list

Run this after a prompt or timer has been admitted and you need its scheduler work ID. The provider may still be unavailable; queued execution can remain recorded while its admission requirements are unresolved.

```sh
sturgis command AGENT_ID work.list
```

**Expected result.** Expect scoped work records with IDs, states, payloads, resource bounds, and relationships. Queued means waiting for admission; running means a current execution slot. Completed model work does not automatically establish a successful external effect.

**Recovery.** For a missing response, locate the intended conversation or schedule relationship before cancelling anything. A similarly named objective can belong to different work. Preserve the original work ID while inspecting errors, claims, or terminal observations.


### work.get

Use the scheduler work ID found in list, message admission, or workflow evidence. A goal, conversation, or tool attempt ID is a different identity and cannot be substituted for this lookup.

```sh
cat > work-get.json <<'JSON'
{
  "id": "WORK_ID"
}
JSON
sturgis command AGENT_ID work.get --params-file work-get.json
```

**Expected result.** Expect one work record with its state, payload, resources, parent/goal relationships, and recorded execution information. Compare its allocation with actual usage receipts and check whether unresolved claims remain even if the visible turn stopped.

**Recovery.** If the record is unavailable, verify the selected agent and the ID's type. For a failed work item, correct the underlying dependency before creating new work. This command does not include a hidden retry or declare-success operation.


### work.cancel

Identify the exact nonterminal work you intend to end and provide a useful reason. Use message cancellation when your intention is conversation-wide; this command operates from a specific scheduler work record and its affected lineage.

```sh
cat > work-cancel.json <<'JSON'
{
  "id": "WORK_ID",
  "reason": "Owner ended this work"
}
JSON
sturgis command AGENT_ID work.cancel --params-file work-cancel.json --request-id recipe_work_cancel_1
```

**Expected result.** Expect affected work_ids and a statement that new execution is fenced while in-flight cancellation is requested. Relevant descendants and runtime turns are signalled consistently. This records cancellation intent and authority changes, not reversal of every external side effect.

**Recovery.** Inspect later work/turn evidence to confirm local cleanup. Unknown external effects remain unresolved until a supported operational observation settles them. Do not cancel a different task with a similar label simply because the original ID is no longer visible in an active list.


## Delegated children

Feature guide: [Delegated children](../work/delegation.md).


### children.delegate

Use an actual eligible live parent work record with sufficient remaining resources and delegable capabilities. This example grants no tool capabilities; include the bounded material needed for review in the supported task description rather than assuming parent files are inherited.

```sh
cat > children-delegate.json <<'JSON'
{
  "parent_work_id": "LIVE_PARENT_WORK_ID",
  "objective": "Review unsupported dates",
  "capabilities": [],
  "resources": {
    "tokens": 16000,
    "iterations": 2,
    "elapsed_ms": 60000,
    "tool_calls": 2
  },
  "workspace_mode": "isolated",
  "retained": false
}
JSON
sturgis command AGENT_ID children.delegate --params-file children-delegate.json --request-id recipe_children_delegate_1
```

**Expected result.** Expect a recorded bounded child and actual child execution under its parent authority. Isolated workspace mode uses a distinct child workspace. The requested allocation and capabilities cannot exceed what the parent can delegate, and retained does not create a new top-level agent.

**Recovery.** If delegation is denied, inspect parent state and cumulative allocations. Do not enlarge child authority based only on text in its objective. Parent content/workspace wrappers reject delegated access; use supported child-aware capabilities and explicit context when a subtask needs tools or source material.


### children.list

Use this after delegated work exists, or when checking whether a parent cancellation affected its children. The web Inbox also exposes delegated records, but this command provides their actual IDs for precise inspection.

```sh
sturgis command AGENT_ID children.list
```

**Expected result.** Expect child records with objectives, work relationships, states, workspace modes, and evidence. Active does not prove the child has completed its objective; a terminal child summary must still be assessed against recorded completion checks and operational observations.

**Recovery.** If no child appears, inspect the original delegation acknowledgement and parent eligibility before submitting another request. Delegation records belong to the same selected agent scope, even when execution uses a distinct child workspace and identity.


### children.get

Copy a child record ID from children.list. Use it to inspect the delegation itself; use the returned work_id with work.get when investigating scheduler execution or resource consumption underneath that child.

```sh
cat > children-get.json <<'JSON'
{
  "id": "CHILD_ID"
}
JSON
sturgis command AGENT_ID children.get --params-file children-get.json
```

**Expected result.** Expect the child's bounded task, parent relationship, workspace/grant information, state, and result evidence. Compare claimed findings with referenced observations. A child saying that it changed an external system is not, by itself, proof of that effect.

**Recovery.** If a result is incomplete or its evidence is absent, preserve that finding and inspect the actual source or tool attempt. There is no consumer action that simply promotes a child's narrative to verified outcome or silently grants additional parent authority.


### children.cancel

Identify the child you no longer need, review any active external attempt, and choose a reason that explains the parent's decision. This is a child cancellation request, not deletion of a top-level user agent.

```sh
cat > children-cancel.json <<'JSON'
{
  "id": "CHILD_ID",
  "reason": "Parent no longer needs this review"
}
JSON
sturgis command AGENT_ID children.cancel --params-file children-cancel.json --request-id recipe_children_cancel_1
```

**Expected result.** Expect the child's cancellation state and underlying work fencing. Parent cancellation can also fence descendants. Retained child mode does not exempt a child from current authority constraints, resource limits, or deliberate cancellation.

**Recovery.** If the child had an uncertain external operation, cancellation cannot prove the target never changed. Inspect the existing work and tool evidence before retrying through another child. Keep the original request identity when cancellation acknowledgement itself is unknown.


## Initiative, attention, and inbox decisions

Feature guide: [Initiative, attention, and inbox decisions](../work/initiative-and-attention.md).


### initiative.propose

Choose an actual source event from this agent and a concrete proposed action. Supply all six finite signals between zero and one, a future expiry, and a stable dedupe key for that particular source/action opportunity.

```sh
cat > initiative-propose.json <<'JSON'
{
  "source_event_id": "ACTUAL_SOURCE_EVENT_ID",
  "action": "Review the corrected source",
  "signals": {
    "urgency": 0.5,
    "expected_value": 0.8,
    "confidence": 0.8,
    "interruption_cost": 0.2,
    "resource_cost": 0.2,
    "duplication_penalty": 0
  },
  "expires_at": 1893456000,
  "dedupe_key": "review_corrected_source_1"
}
JSON
sturgis command AGENT_ID initiative.propose --params-file initiative-propose.json --request-id recipe_initiative_propose_1
```

**Expected result.** Expect a proposal record linked to its supporting event. It becomes available for attention evaluation; it is not automatically a prompt, accepted responsibility, or granted tool call. An action object needs executable text before an owner can accept it as scheduled work.

**Recovery.** A fabricated source ID or expired timestamp is rejected. Keep the same dedupe key only for the same proposal, not an unrelated future event. Inspect inbox and attention evidence before assuming policy selected the proposal for execution.


### attention.policy

Choose the desired proactive policy and read the last saved policy revision. The example uses suggest with a score threshold and expected revision zero only for an absent policy. It does not enable autonomous execution.

```sh
cat > attention-policy.json <<'JSON'
{
  "policy": {
    "mode": "suggest",
    "minimum_score": 0.6
  },
  "expected_revision": 0
}
JSON
sturgis command AGENT_ID attention.policy --params-file attention-policy.json --request-id recipe_attention_policy_1
```

**Expected result.** Expect a stored revisioned policy. Modes are disabled, remember, suggest, and bounded. Quiet hours can add timezone and minute-of-day boundaries. Bounded mode additionally requires a valid conversation_id and positive resources inside the policy object.

**Recovery.** If the revision conflicts, preserve intervening owner policy rather than resetting to zero. Quiet hours, duplicate suppression, and foreground workload can defer admission. A score or eligible decision does not bypass scheduler budgets, connection grants, or tool permissions.


### inbox.list

Use this after the monitor has evaluated initiative candidates or when the web Inbox shows a proposal. No ready provider is needed simply to inspect what was proposed and why attention selected a particular disposition.

```sh
sturgis command AGENT_ID inbox.list
```

**Expected result.** Expect inbox records with the candidate, attention decision, owner-decision state, and any scheduled work identity. Pending is a proposal awaiting a decision; started identifies admitted scheduling rather than a completed model response.

**Recovery.** If a recent proposal has not appeared yet, inspect its recorded source and monitor state rather than creating another candidate. Check expiry before acceptance. A remembered or deferred proposal should not be described as already executed simply because it has an inbox record.


### inbox.decide

Inspect a pending proposal before deciding. The example dismisses it and requires no execution resources. To accept, use decision accept with an active conversation_id and an explicit positive resources allocation suitable for the proposed prompt.

```sh
cat > inbox-decide.json <<'JSON'
{
  "id": "PROPOSAL_ID",
  "decision": "dismiss"
}
JSON
sturgis command AGENT_ID inbox.decide --params-file inbox-decide.json --request-id recipe_inbox_decide_1
```

**Expected result.** Expect a final inbox decision: dismissed, or started with a schedule_id for accepted prompt work. Acceptance records actual scheduling through the normal runtime path; it does not establish model completion or grant arbitrary effects mentioned in the proposal.

**Recovery.** Expired candidates cannot be accepted, and an already decided item cannot receive a conflicting second decision. On lost acknowledgement, inspect the original receipt and inbox record. For failed scheduled execution, investigate its work instead of trying to accept the same proposal again.

## Model providers

Feature guide: [Model providers](../models/connect-a-model.md).


### providers.configure

Obtain the exact streaming chat-completions endpoint, model name, and documented limits from the service operator. The example deliberately configures text-only behavior; it names no bundled model.

```sh
cat > providers-configure.json <<'JSON'
{
  "provider_id": "research",
  "endpoint": "https://model.example.com/v1/chat/completions",
  "model": "SERVICE_MODEL",
  "context_limit": 32768,
  "output_limit": 4096,
  "tools": false
}
JSON
sturgis command AGENT_ID providers.configure --params-file providers-configure.json --request-id recipe_providers_configure_1
```

**Expected result.** Expect a revisioned provider configuration, without automatic activation or proven readiness. Add a current opaque credential_ref when required. Set tools true only for compatible canonical tool-call transport, then probe that exact configuration.

**Recovery.** Private endpoints require the operator's exact-origin exception. For edits, provide the current expected_revision. Changing configuration invalidates old readiness; larger declared limits cannot repair an incompatible service protocol.


### providers.list

Use this to discover saved provider IDs and determine which configuration is selected. It reads configuration and recorded probe evidence rather than issuing a fresh inference request.

```sh
sturgis command AGENT_ID providers.list
```

**Expected result.** Expect an array of provider records containing ready, active, credential_current, and probe information. Several configurations may exist, but an active selection refers to one exact configuration revision.

**Recovery.** A stale probe or rotated credential can make ready false. Inspect the intended provider, correct its credential/configuration as necessary, and probe before activation. A saved model name alone is not working inference.


### providers.inspect

Take a provider_id from the list. The example inspects research only; omitting provider_id returns all configurations, matching providers.list. Use this before editing or activating a particular revision.

```sh
cat > providers-inspect.json <<'JSON'
{
  "provider_id": "research"
}
JSON
sturgis command AGENT_ID providers.inspect --params-file providers-inspect.json
```

**Expected result.** Expect its configuration revision, ready flag, active selection flag, credential_current, and recorded probe observation. The probe has its own record revision and config_revision; activation uses the configuration revision.

**Recovery.** If inactive but ready, explicitly activate the intended configuration. If credential_current is false, rotate/rebind deliberately before probing. Do not copy the probe record's revision into expected_revision for a configuration operation.


### providers.probe

Read the current configuration revision and confirm its credential is still current. This performs a real bounded inference request and can consume provider resources, even though it does not execute a model-proposed tool.

```sh
cat > providers-probe.json <<'JSON'
{
  "provider_id": "research",
  "expected_revision": 1
}
JSON
sturgis command AGENT_ID providers.probe --params-file providers-probe.json --request-id recipe_providers_probe_1
```

**Expected result.** Expect provider inspection with recorded ready/failed probe evidence, expiry, and any error. Text-only probes check a complete text stream; tool-capable probes check the canonical transport. Readiness lasts five minutes for unchanged bindings.

**Recovery.** For busy, retry deliberately after capacity is available. For failed protocol or credential evidence, fix the cause before a new probe. Missing reported usage is not evidence of zero cost.


### providers.activate

Choose a provider with a successful unexpired probe for its current configuration and credential revision. Use that configuration revision, not the separate probe record's revision, in this request.

```sh
cat > providers-activate.json <<'JSON'
{
  "provider_id": "research",
  "expected_revision": 1
}
JSON
sturgis command AGENT_ID providers.activate --params-file providers-activate.json --request-id recipe_providers_activate_1
```

**Expected result.** Expect the selected provider binding to become active for new work. Text-only configurations suppress tool advertisements and reject unsolicited tool proposals before execution. Activation does not replay earlier failed turns.

**Recovery.** If provider_unavailable is returned, inspect and probe the exact current configuration. After changing the endpoint or credential, review the new binding rather than trying to reuse stale readiness or silently switch a running task's authority.


## Credential vault

Feature guide: [Credential vault](../models/credentials.md).


### providers.credentials.put

Use the dedicated credential command so the secret is requested privately and never placed in a parameters file or shell argument. The example creates model_key; rotation adds --revision with the current credential revision.

```sh
sturgis credential AGENT_ID model_key
```

**Expected result.** Expect metadata containing the name, new revision, active state, and opaque reference. Use that returned reference in provider or connection configuration. Ordinary inspection cannot retrieve the stored secret.

**Recovery.** If acknowledgement is uncertain, inspect credential metadata before submitting another value. Rotation invalidates old references and readiness; rebind dependent configurations deliberately. A new secret is not a continuation of an unresolved different mutation.


### providers.credentials.list

Use this to choose a stored opaque credential reference or obtain the current revision before rotation or revocation. It requires owner access to this agent, not a model task.

```sh
sturgis command AGENT_ID providers.credentials.list
```

**Expected result.** Expect credential names, revisions, references, and active state without secret values. References belong to their exact vault revision; a matching display name is insufficient to keep an old connection binding current.

**Recovery.** If a dependent model reports a changed credential, select the latest valid reference and reprobe its configuration. Do not paste a secret into a document because metadata inspection intentionally does not reveal it.


### providers.credentials.revoke

Inspect the credential's current revision and identify dependent providers or connections before revocation. Use this when the old value should no longer authorize future requests from the agent.

```sh
cat > providers-credentials-revoke.json <<'JSON'
{
  "name": "model_key",
  "expected_revision": 2
}
JSON
sturgis command AGENT_ID providers.credentials.revoke --params-file providers-credentials-revoke.json --request-id recipe_providers_credentials_revoke_1
```

**Expected result.** Expect the credential marked inactive at a new revision. Existing references become invalid for new use. This does not revoke the key at the remote vendor or prove an already submitted operation was undone.

**Recovery.** For a conflict, reread before deciding again. Replace the key at its issuing service when appropriate, store a new value privately, and explicitly rebind/reprobe dependencies rather than assuming revocation installed a replacement.


## Connection configuration and grants

Feature guide: [Connection configuration and grants](../connections/overview.md).


### connections.configure

Choose a supported adapter kind and a real compatible endpoint. This example defines HTTP fetch; optional allowed_origins defaults to the endpoint's exact origin, while credentials use opaque current vault references.

```sh
cat > connections-configure.json <<'JSON'
{
  "id": "reference",
  "kind": "http",
  "endpoint": "https://reference.example.com/notes"
}
JSON
sturgis command AGENT_ID connections.configure --params-file connections-configure.json --request-id recipe_connections_configure_1
```

**Expected result.** Expect a revisioned active configuration with empty grants and no probe. Configure does not authorize fetching or prove endpoint compatibility. Editing an existing connection requires its current expected_revision and resets grants/readiness.

**Recovery.** For denied private destinations, review the operator exception and connection origin list. Then grant the exact supported operations and probe. Changing a kind label cannot translate a vendor's incompatible response schema.


### connections.list

Use this to discover configured connection IDs and readiness states. It reads local records; it does not fetch every endpoint or automatically grant operations simply because the owner saved an address.

```sh
sturgis command AGENT_ID connections.list
```

**Expected result.** Expect configurations with current revision, kind, grants, credential status, and readiness. Ready, needs test, revoked, and changed credential describe different conditions. Follow up with get before making a revisioned change.

**Recovery.** If a tool is missing, inspect grants and readiness, then model-action permissions. Installing a credential alone does not supply a connection. Do not create duplicates to compensate for an existing record that merely needs reprobe.


### connections.get

Use a connection ID from configuration or list. Inspect it before editing, granting, probing, signing external traffic, or revoking; each of those operations depends on the exact current binding.

```sh
cat > connections-get.json <<'JSON'
{
  "id": "reference"
}
JSON
sturgis command AGENT_ID connections.get --params-file connections-get.json
```

**Expected result.** Expect current options, revision, grants, active/readiness state, and recorded probe evidence. MCP readiness also carries selected-catalog information. The connection ID is not a tool version; registered tool versions track current revisions.

**Recovery.** If credential status changed, rebind the intended reference and review grants. A ready catalog or pairing probe is not evidence every external operation works. Read the adapter-specific guide before trusting its readiness meaning.


### connections.grant

Read the connection revision and select only operations offered by its adapter. HTTP uses fetch; MCP uses call; webhook uses receive. This example grants the reference fetch connection its single read operation.

```sh
cat > connections-grant.json <<'JSON'
{
  "id": "reference",
  "operations": [
    "fetch"
  ],
  "expected_revision": 1
}
JSON
sturgis command AGENT_ID connections.grant --params-file connections-grant.json --request-id recipe_connections_grant_1
```

**Expected result.** Expect updated grants and revision, with readiness cleared for a new probe. The grant supplies connection capability; per-tool permission and scheduler admission still govern actual model actions. An empty operation list is rejected.

**Recovery.** Use revoke when no operation should remain authorized. For invalid_grant, inspect the adapter's available operation names rather than inventing a capability. Reprobe the resulting revision, not the pre-grant revision.


### connections.probe

Read the active connection's current revision after configuration and grants. Ensure its credential is current. The probe may contact a real service and is an observation, not a universal harmless health endpoint.

```sh
cat > connections-probe.json <<'JSON'
{
  "id": "reference",
  "expected_revision": 2
}
JSON
sturgis command AGENT_ID connections.probe --params-file connections-probe.json --request-id recipe_connections_probe_1
```

**Expected result.** Expect a newer connection revision containing probe evidence. HTTP observes a response; search issues a readiness query; MCP checks its selected catalog. Media and device readiness explicitly do not certify a completed operation.

**Recovery.** A failed probe can still return a saved connection record, so inspect probe.ok and status. Fix the recorded cause and use the new current revision for another deliberate probe; stale revisions cannot validate changed configuration.


### connections.revoke

Inspect the selected connection and current revision before removing future access. Use this for all its operations rather than attempting to grant an empty operation list. Consider any already submitted external requests.

```sh
cat > connections-revoke.json <<'JSON'
{
  "id": "reference",
  "expected_revision": 3
}
JSON
sturgis command AGENT_ID connections.revoke --params-file connections-revoke.json --request-id recipe_connections_revoke_1
```

**Expected result.** Expect grants cleared, active false, readiness removed, and a newer revision. Effective capabilities are refreshed so subsequent claims cannot use the revoked binding. Existing external effects are not represented as automatically reversed.

**Recovery.** To restore access deliberately, inspect the new revision, grant only the intended operations, and reprobe. Do not reuse old tool versions or approval fingerprints; those refer to the earlier connection authority.


## Compatible devices

Feature guide: [Compatible devices](../connections/devices.md).


### devices.list

Use this after configuring device-kind connections, or when checking which compatible clients have paired. It does not discover arbitrary computers on the local network or install actuator software.

```sh
sturgis command AGENT_ID devices.list
```

**Expected result.** Expect device projections containing connection identities, paired status, revisions, declared device identity, and last_seen where available. Pairing is authentication evidence; it is not proof the client can perform an operation.

**Recovery.** For an unpaired record, perform explicit pairing with the compatible client. For a silent paired device, inspect last_seen and pending work. Repeated listing cannot manufacture a device response or widen its declared operations.


### devices.get

Use the connection_id of a device-kind connection. It differs from the external client's device_id and from a pending device request ID. Read it before pairing, signing at a revision, or revoking.

```sh
cat > devices-get.json <<'JSON'
{
  "connection_id": "DEVICE_CONNECTION_ID"
}
JSON
sturgis command AGENT_ID devices.get --params-file devices-get.json
```

**Expected result.** Expect current connection status and revision, paired state, configured device identity, and observed last_seen. The signing secret is intentionally absent; the claim response is its one-time disclosure to the compatible client.

**Recovery.** If revision changed, use the supported signed sync protocol before further client requests. Lost one-time secret material cannot be recovered through get; perform a new deliberate pairing instead of assuming metadata contains hidden credentials.


### devices.pair

Configure the device identity and operation schemas first, then read its connection revision. This recipe writes the full one-time pairing response to a new private file because normal CLI output redacts recognized token fields.

```sh
cat > devices-pair.json <<'JSON'
{
  "connection_id": "DEVICE_CONNECTION_ID",
  "expected_revision": 1,
  "ttl": 300
}
JSON
sturgis --output-file pairing-proof.json command AGENT_ID devices.pair --params-file devices-pair.json --request-id recipe_devices_pair_1
```

**Expected result.** Expect pairing_token and expiry in pairing-proof.json, created with mode 0600. Transfer that token privately to the compatible client's claim flow within the 300-second example lifetime. Claim returns its signing secret and updated connection revision.

**Recovery.** Do not log the private response or overwrite a retained proof accidentally. If token/secret acknowledgement is unrecoverable or expired, inspect current state and make a new deliberate pairing attempt. Then regrant and probe the claimed revision.


### devices.revoke

Read the device connection's current revision and verify this is the client whose future access should end. Revocation is distinct from cancelling one outstanding operation or deleting a computer's installed client software.

```sh
cat > devices-revoke.json <<'JSON'
{
  "connection_id": "DEVICE_CONNECTION_ID",
  "expected_revision": 3
}
JSON
sturgis command AGENT_ID devices.revoke --params-file devices-revoke.json --request-id recipe_devices_revoke_1
```

**Expected result.** Expect the device connection revoked and its effective capabilities refreshed. Subsequent signed activity cannot use the old active binding. Previously received results and uncertain effects remain evidence rather than disappearing with the pairing state.

**Recovery.** If the revision conflicts, refresh device metadata before deciding again. For later reuse, perform the appropriate deliberate pairing/grant/probe flow. Inspect already dispatched work separately; revocation alone cannot prove a physical operation was stopped.


## Tool policies and exact approvals

Feature guide: [Tool policies and exact approvals](../settings/permissions.md).


### permissions.list

Use this before changing a tool policy. The list reflects tools currently registered through built-ins, ready connections, or active plugins; it is not an unbounded catalog of every tool a model might name.

```sh
sturgis command AGENT_ID permissions.list
```

**Expected result.** Expect each tool descriptor with required capabilities, read/effect characteristics, current mode, policy revision, and stale binding status. Baseline content/workspace reads allow by default; other new tools normally ask.

**Recovery.** Review changed descriptors before replacing stale policy. If a tool is absent, fix its connection or extension availability first. Allowing a similarly named tool does not grant the missing original capability.


### permissions.set

Read the current workspace.write policy revision and descriptor. The example sets ask only when revision zero denotes no saved policy; substitute the observed policy revision for an existing setting.

```sh
cat > permissions-set.json <<'JSON'
{
  "tool": "workspace.write",
  "mode": "ask",
  "expected_revision": 0
}
JSON
sturgis command AGENT_ID permissions.set --params-file permissions-set.json --request-id recipe_permissions_set_1
```

**Expected result.** Expect the revisioned policy, effective capabilities, and any work/schedule fencing caused by restriction. Modes are ask, allow, and deny. Policy attaches to the current descriptor and related connection binding.

**Recovery.** For a conflict or stale binding, inspect the changed tool before deciding again. Restricting access can cancel or fence work, but cannot undo completed external effects. Allow does not turn an earlier failed action into success.


### permissions.pending

Use this after a model action was rejected with approval_required. It projects only requests that remain pending from trusted tool-runner rejection evidence. Use permissions.request with a saved event ID to inspect a later decision or stale/expired state.

```sh
sturgis command AGENT_ID permissions.pending
```

**Expected result.** Expect bounded request records for actual rejected invocations. Inspect the event identity, tool call, arguments, constraints, and state before approving or denying. A plain conversational request to approve is not equivalent evidence.

**Recovery.** If no request appears, inspect the actual tool error; a capability or schema failure is not automatically approval_required. Do not invent event IDs or broaden a policy to conceal a different admission failure.


### permissions.request

Take an event_id from the pending request projection. It must identify the trusted rejected invocation itself, not a conversation message, proposed goal, or unrelated successful tool result.

```sh
cat > permissions-request.json <<'JSON'
{
  "event_id": "REQUEST_EVENT_ID"
}
JSON
sturgis command AGENT_ID permissions.request --params-file permissions-request.json
```

**Expected result.** Expect the exact call, work_id, delegated flag, invocation digest, current decision, and freshness state. Inspect work.get for underlying workspace constraints. Compare the arguments with what you intended before issuing one-use authorization.

**Recovery.** If stale or unavailable, inspect the current tool and obtain a new real request when appropriate. A changed descriptor, policy, credential, or connection can invalidate the old fingerprint even when the human-readable tool name matches.


### permissions.approve

Inspect the exact pending request and confirm the proposed arguments and constraints. The example gives its matching future invocation a 300-second window; the supported expiry range is one to 3,600 seconds.

```sh
cat > permissions-approve.json <<'JSON'
{
  "event_id": "REQUEST_EVENT_ID",
  "expires_in": 300
}
JSON
sturgis command AGENT_ID permissions.approve --params-file permissions-approve.json --request-id recipe_permissions_approve_1
```

**Expected result.** Expect a recorded single-use approval for the current fingerprint. It authorizes a future exact claim only when normal work grants remain valid. Approval does not execute the tool or automatically retry the original failed turn.

**Recovery.** If no result follows, inspect work rather than approving repeatedly. Changed arguments or bindings need a new matching request. Expired or consumed authority cannot be reused to bless another action or manufacture a successful receipt.


### permissions.deny

Use the actual request event after inspecting its proposed tool, arguments, and binding. Deny this invocation when it should not receive one-use authority; changing the general tool policy is a separate decision.

```sh
cat > permissions-deny.json <<'JSON'
{
  "event_id": "REQUEST_EVENT_ID"
}
JSON
sturgis command AGENT_ID permissions.deny --params-file permissions-deny.json --request-id recipe_permissions_deny_1
```

**Expected result.** Expect a recorded denied decision associated with that request. It does not delete the rejected call or rewrite its history, and it does not establish a global deny policy for every future invocation.

**Recovery.** If the request has changed or already reached an incompatible state, inspect current evidence. To restrict the tool generally, use permissions.set with the current policy revision and review any resulting execution fences.


## Reusable procedures

Feature guide: [Reusable procedures](../settings/procedures.md).


### skills.install

Prepare a bounded manifest with id, version, description, triggers, steps, and required_tools. This example installs instructions for source review with no required tools; it is a procedure, not executable plugin code.

```sh
cat > skills-install.json <<'JSON'
{
  "manifest": {
    "id": "review_sources",
    "version": "1",
    "description": "Review source evidence",
    "triggers": [
      "source review"
    ],
    "steps": [
      "Identify the source for every claimed date."
    ],
    "required_tools": []
  }
}
JSON
sturgis command AGENT_ID skills.install --params-file skills-install.json --request-id recipe_skills_install_1
```

**Expected result.** Expect a stored authored procedure available for conditional selection. Trigger matches, platform compatibility, tool availability, and context limits still decide whether it enters a particular model turn. Installation does not prove its steps were followed.

**Recovery.** Identical content is rejected as a duplicate. For a new intended procedure version, change the manifest deliberately. Do not add tool names as a way to grant capabilities; required_tools only states dependencies that must already be ready.


### skills.list

Use this to inspect available authored or discovered procedures before enabling, disabling, or choosing a prior version. IDs are normalized for the consumer view; private host discovery paths are omitted.

```sh
sturgis command AGENT_ID skills.list
```

**Expected result.** Expect procedure manifests, origins, versions, states, hashes, and revision information where stored. Available means eligible for selection, not guaranteed inclusion in every context. Imported portable procedures begin disabled.

**Recovery.** If a procedure is absent from a turn, inspect its trigger, platform, required tools, and context budget. A discovered package without a stored concurrency revision is not automatically eligible for the same editing controls as an authored record.


### skills.enable

Choose a stored procedure with an actual current revision. This example disables review_sources using enabled false; use true after review when you want that stored procedure eligible for selection again.

```sh
cat > skills-enable.json <<'JSON'
{
  "id": "review_sources",
  "enabled": false,
  "expected_revision": 1
}
JSON
sturgis command AGENT_ID skills.enable --params-file skills-enable.json --request-id recipe_skills_enable_1
```

**Expected result.** Expect the same procedure updated to withdrawn or available with a new revision. The instructions remain preserved. Enabling changes selection eligibility and does not grant missing tools or run the procedure immediately.

**Recovery.** If revision conflicts, list procedures again before deciding. If no stored record exists, use the supported authored install workflow rather than guessing a revision for an operator-discovered file. Review imported content before enabling it.


### skills.rollback

Inspect the stored procedure and verify a prior version exists. Use the current record revision; this is distinct from the human-selected manifest version string such as 1 or 2.

```sh
cat > skills-rollback.json <<'JSON'
{
  "id": "review_sources",
  "expected_revision": 2
}
JSON
sturgis command AGENT_ID skills.rollback --params-file skills-rollback.json --request-id recipe_skills_rollback_1
```

**Expected result.** Expect the retained previous procedure content restored as a new record state. Review its steps and availability afterward. Rollback changes the procedure used by future selection, not the history of model turns that already used another version.

**Recovery.** If no previous version exists, author the intended change explicitly instead of repeating rollback. A stale concurrency revision requires a fresh list. Do not treat procedure rollback as reversal of external effects performed by past tools.


## Executable plugin lifecycle

Feature guide: [Executable plugin lifecycle](../settings/plugins.md).


### plugins.list

Use this only to discover plugins already installed by an operator. The consumer surface does not download arbitrary packages, install from a URL, or provide a public marketplace through this command.

```sh
sturgis command AGENT_ID plugins.list
```

**Expected result.** Expect plugin records with IDs, revisions, statuses, and retained versions. Installation and activation are separate. Private host installation paths are removed from the consumer projection, while useful version and lifecycle evidence remain visible.

**Recovery.** An empty list means there is no consumer-manageable installed plugin here. Ask the operator about installation rather than supplying a fabricated plugin ID. Procedures belong to skills.list and cannot substitute for executable plugin code.


### plugins.inspect

Choose an actual installed plugin ID and inspect it before activation, rollback, or uninstall. This is the safe metadata step for an inactive package; plugins.health instead requires an active plugin.

```sh
cat > plugins-inspect.json <<'JSON'
{
  "id": "INSTALLED_PLUGIN_ID"
}
JSON
sturgis command AGENT_ID plugins.inspect --params-file plugins-inspect.json
```

**Expected result.** Expect status, current revision, active version, retained versions, and lifecycle state without private package paths. Check pending transitions and any previous compatible state before deciding which lifecycle operation applies.

**Recovery.** A quarantined or interrupted state needs explicit review. Do not assume installed means running or that a retained version is automatically rollback-compatible. Use the observed revision rather than a manifest version string in mutations.


### plugins.activate

Inspect an operator-installed package and use its current record revision. Activation executes its lifecycle backend and can change plugin state; trust in installed code is an operator responsibility.

```sh
cat > plugins-activate.json <<'JSON'
{
  "id": "INSTALLED_PLUGIN_ID",
  "expected_revision": 2
}
JSON
sturgis command AGENT_ID plugins.activate --params-file plugins-activate.json --request-id recipe_plugins_activate_1
```

**Expected result.** Expect an active plugin, refreshed registered tools, and effective capability reconciliation. Tool permission and scheduler admission still apply to later calls; activation does not authorize arbitrary effects merely because the plugin is present.

**Recovery.** On activation failure or quarantine, inspect the recorded state before another attempt. A lost acknowledgement should resolve the original receipt first. Review compatibility and retained state before considering rollback instead of repeatedly activating uncertain code.


### plugins.health

Run this only after metadata reports status active. It invokes the active plugin's health backend, so it is not a purely local inspection of an inactive installation.

```sh
cat > plugins-health.json <<'JSON'
{
  "id": "INSTALLED_PLUGIN_ID"
}
JSON
sturgis command AGENT_ID plugins.health --params-file plugins-health.json
```

**Expected result.** Expect the actual backend health data. Health can report operational information beyond metadata, but it does not certify every plugin tool or erase existing work uncertainty. A backend failure can quarantine the plugin.

**Recovery.** If inactive is returned, inspect and explicitly activate when intended before checking health. After quarantine, review the failure and current revision; do not bypass lifecycle validation merely to make a health label appear successful.


### plugins.disable

Inspect the active plugin and use its current revision when you intend to remove future use. The host drains its active lifecycle before completing deactivation; this is more than a presentation toggle.

```sh
cat > plugins-disable.json <<'JSON'
{
  "id": "INSTALLED_PLUGIN_ID",
  "expected_revision": 3
}
JSON
sturgis command AGENT_ID plugins.disable --params-file plugins-disable.json --request-id recipe_plugins_disable_1
```

**Expected result.** Expect disabled state and removal of registered plugin tools, with capabilities refreshed. Deactivation may update retained plugin state. A lifecycle failure can instead leave quarantine evidence for explicit operator investigation.

**Recovery.** Inspect the resulting state before attempting uninstall. Disabling cannot prove that an earlier external call was reversed. On uncertainty, recover the original request; repeatedly toggling activation can create additional lifecycle effects.


### plugins.rollback

Confirm metadata contains a compatible retained previous active version/state, then use the current plugin revision. This is a deliberate return to that retained executable state, not an arbitrary version selector.

```sh
cat > plugins-rollback.json <<'JSON'
{
  "id": "INSTALLED_PLUGIN_ID",
  "expected_revision": 3
}
JSON
sturgis command AGENT_ID plugins.rollback --params-file plugins-rollback.json --request-id recipe_plugins_rollback_1
```

**Expected result.** Expect activation of the compatible previous version with its retained state. The previous/pending transition information is consumed or cleared as specified by the host, and current tool registration is refreshed.

**Recovery.** If no compatible prior state is retained, rollback is rejected. Ask the operator to prepare an explicit supported replacement rather than guessing a version. Rolling back code does not undo external effects caused by its earlier execution.


### plugins.uninstall

Inspect the plugin first. It must be installed, disabled, or quarantined with no pending transition; an active plugin must be disabled and drained before uninstall. Use the current revision after that lifecycle change.

```sh
cat > plugins-uninstall.json <<'JSON'
{
  "id": "INSTALLED_PLUGIN_ID",
  "expected_revision": 4
}
JSON
sturgis command AGENT_ID plugins.uninstall --params-file plugins-uninstall.json --request-id recipe_plugins_uninstall_1
```

**Expected result.** Expect status uninstalled and active_version cleared. This is a durable host lifecycle update, not arbitrary recursive deletion of operator package files or a promise that past external effects were removed.

**Recovery.** If invalid_state appears, inspect and finish the required drain/recovery first. Do not remove host files through workspace commands to force success. Restoring availability later requires an operator-supported installation/lifecycle path and fresh review of capabilities.
