# Using Sturgis

Sturgis gives each of your agents a private space for conversations, work, files,
knowledge, and memory. Choose an agent in the sidebar before you begin. The same
Material 3 interface is available on a self-hosted installation and a hosted
installation; the available connections depend on what the operator has enabled.

For installation, see [Deployment](deployment.md). The current verification
record belongs in [Qualification](qualification.md); this guide does not certify
a particular model vendor or a public deployment.

## Create your space

Open the address supplied by your operator. On a new local installation this is
`http://127.0.0.1:8787`. The first account needs the installation token from the
operator's private `bootstrap.token` file, plus an email address and a password of
at least 12 characters.
Bootstrap signs you in automatically. Registration for additional
accounts is available only when the operator enables it.

Create an agent with a display name. An agent owns its conversations, workspace,
knowledge, memory, credentials, and work. Switching agents switches that whole
space. Other accounts cannot inspect it, including through a guessed agent ID.

The sidebar opens **Conversation**, **Work**, **Knowledge**, **Files**,
**Connections**, and **Settings**. Use `Ctrl+K` or `Cmd+K` to open the view selector.
The layout adapts to smaller screens, with the same agent selection and navigation.

## Connect a model

You can organize files and knowledge before connecting a model. To have a
conversation with model responses:

1. Open **Settings → Models & credentials**. If your endpoint needs a key, choose
   **Add credential**. The saved value is never shown again; use its reference
   when configuring a provider.
2. Choose **Add model** (or **Connect your first model** in an empty space).
   Enter a provider ID, the complete streaming endpoint
   URL, the model identifier, and the context and output limits supported by your
   endpoint. Add the credential reference if required.
3. Leave **Model supports canonical tool calls** unchecked for a text-only model.
   Enable it only for an endpoint that supports Sturgis's tool-call transport.
4. Save, choose **Probe model**, then **Use model** after the test succeeds.

The supported model connection is a bounded chat-completions streaming protocol,
described in [Deployment](deployment.md#model-and-adapter-compatibility). A
successful text response and support for tool calls are separate capabilities.
The test makes an actual request and may consume provider tokens. Limits you enter
are declared configuration; the test does not discover a vendor's full context
window or certify every model feature.

Editing a configuration or rotating its credential requires a new readiness
check. An expired check for an unchanged selected provider can be refreshed before
new model work. A local endpoint may also need an operator-approved private origin;
an account cannot bypass that network policy from Settings.

## Have a continuing conversation

Open **Conversation**, start a conversation, and send a message. Previous
conversations remain in the sidebar and can be reopened, renamed, or archived.
Archiving a conversation removes it from the active list while keeping its history.

An accepted message is recorded before a sleeping runtime wakes. Streamed output
is persisted and can be replayed after a reconnect. The connection indicator and
work state help distinguish a reconnecting browser, accepted input, running work,
and a finished turn. A finished model response alone does not prove an external
action succeeded.

While work is active, choose **Steer active work** to change direction, or use the
stop control to request cancellation. Cancellation remains a recorded request; an
external operation whose result is unknown stays unknown. Retrying the same
accepted input preserves its identity. Changing its content requires a new input.
The browser keeps drafts locally, so use a trusted browser profile for private work.

Attach source files with the composer. Uploads belong to the selected agent and
retain their original bytes. Supported text is included with source labels and
explicit bounds. An uploaded document is source material, not an instruction that
automatically gains permission to run tools.

## Build knowledge and refine memory

In **Knowledge → Source library**, choose **Add a source** to upload a file of up
to 4 MiB. Sturgis extracts bounded UTF-8 text from plain text, Markdown, CSV, JSON,
and HTML. HTML extraction excludes scripts and styles. The extraction limit is
256 KiB of text; oversized extraction is deferred without extracted text. Status
also identifies unsupported or invalid content. Original files remain available
as original downloads. OCR, PDF, and office-document decoding are not bundled.

Choose a source's add-to-knowledge action to index its extracted text. Open the
result to inspect the text and its source. Knowledge keeps revisions and source
references. Removing a document from knowledge retracts its indexed memories; the
original upload remains in the source library.

In **Knowledge → Agent memory**, search for an idea, fact, or concept. Inspect a
record's provenance and revision before correcting it. A correction creates a new
revision. Retraction records a reason and removes the affected material from
current recall without erasing its history. Memory availability and freshness are
reported separately; an unavailable native worker is not presented as an empty,
successfully searched memory store.

## Work with files

**Files** shows the selected agent's workspace. Open folders, filter entries,
create files, edit readable text, and download content. Binary content can be
downloaded without claiming a text preview. Saves and deletes check the version
you opened; if someone changed the file, reload it before applying your edit.

Paths stay inside that workspace. Runtime internals, credentials, other agents,
and arbitrary server directories are not exposed in the file browser. Sources in
Knowledge and generated artifacts also have authenticated downloads; they are
kept distinct from editable workspace files.

## Give work a direction

**Work** gathers current activity, goals, commitments, schedules, workflows,
proposals, and delegated work. Open a record's evidence to see what supports its
state.

| Need | Action |
| --- | --- |
| Pursue an outcome | Create a goal with its resource allowance, then activate it deliberately. Inspect progress, wait/block reasons, and completion evidence. |
| Track a responsibility | Create a commitment for a named beneficiary and accept it. Keep its progress and waiting state explicit. |
| Run a prompt later | Create a schedule, select its conversation, start time, and a one-time or repeating interval. Pause, resume, or cancel it when needed. |
| Carry out dependent steps | Create a workflow and run it with a bounded allowance. Inspect step dependencies, attempts, and evidence before resuming blocked work. |
| Review an agent proposal | Open the proposal in Work and make a recorded decision. A proposal is not already an accepted responsibility or completed action. |
| Understand delegation | Inspect the child work and its evidence. A child's summary does not independently certify a world effect. |

Schedules use the runtime's durable scheduler. Suspending an idle runtime retains
its next scheduled wake. Archiving the agent prevents scheduled work from waking
it. If work cannot proceed because of a provider, resource limit, permission, or
unresolved effect, the corresponding state remains visible instead of becoming a
successful result.

## Add connections and control access

In **Connections**, add a service and select its kind. Then choose **Manage
grants** to grant the particular operation and **Test connection** to check the
resulting configuration.
Configuration, granted access, and readiness are separate steps. Editing or
revoking a connection invalidates the old authorization.

The implemented kinds are **Web fetch**, **Web search**, **App server (MCP)**,
**Managed browser**, **Speech to text**, **Text to speech**, **Image generation**,
**Incoming webhook**, and **Paired device**. They use the specific protocols in
the deployment guide. A service with a similar name is not necessarily compatible.
Media readiness checks its model catalog; it does not prove that a later
transcription, speech, or image request will succeed.

A paired device needs a compatible device client. Configure its device identity
and declared operations, generate a pairing token, and use that short-lived token
in the device client. The token is shown only for that attempt. After the claim,
review access and test readiness. Pairing verifies identity; Sturgis does not ship
native Windows, macOS, or Linux desktop actuators, and pairing alone does not prove
that a device can perform an operation.

Managed browsing is a contained, read-oriented page inspection capability. It can
return rendered text and a screenshot when the operator's Linux browser backend
is available. It is not unrestricted desktop control. On the qualification host,
the hosted worker's nested browser namespace is denied and readiness reports that
specific limitation. The rest of the agent remains usable.

Open **Settings → Permissions** to choose **Ask first**, **Allow**, or **Deny** for
current tool versions. Review exact action requests there. Approvals are bounded,
single-use, and tied to the original invocation; changing the relevant tool,
workspace, or grant does not carry approval forward. A connection grant and a
model action's permission remain separate requirements.

In **Settings → Procedures**, manage reusable procedures with explicit triggers
and steps. Inspect, enable, disable, or roll back their versions. Installed
extensions appear with lifecycle and health information; package installation is
an operator function, not a public marketplace or arbitrary URL installer.

## Inspect your agent and move its data

**Settings → Models & credentials** includes usage evidence. Consumption,
overages, estimates, and outstanding reservations are distinct from a provider
invoice. Missing usage does not mean zero cost.

**Settings → Agent & data** lets you rename the agent, start or suspend its runtime,
recover it, inspect diagnostics, export a backup, restore a backup, or archive it.
Suspension and backup require settled work. Recovery replaces the current process
and retains unresolved outcomes. Archive is a durable execution fence; it does not
delete historical records, and there is no unarchive action in this release.

The web client pauses private data queries while the runtime is suspended. Choose
**Start runtime** in Agent & data to resume those views; scheduled wakes remain
active. CLI commands that need private runtime data can wake a suspended agent.

Web and CLI backups use the same `sturgis-backup/1` ZIP archive. A new restore
creates a fresh agent owned by the signed-in account. Retrying the same restore
request after a lost response returns that original agent instead of creating a
duplicate. It preserves workspace
content, sources, knowledge revisions, memory, and labeled conversation history.
Imported history cannot replay old messages or authorize new effects.

Credentials and execution authority must be reconnected: providers, connections,
devices, permissions, plugins, schedules, workflows, and child work are not
reactivated. Imported procedures arrive disabled. Archive limits are 4 MiB
compressed, 32 MiB expanded, 512 files, and 8 MiB per file. Oversized exports fail
explicitly. Treat an archive as private data; ordinary files or conversations may
contain information you deliberately placed there.

## Use the same agent from the CLI

Global options such as `--url` and `--json` go before the command. The CLI uses the
same authenticated service as the browser.

```sh
sturgis --url http://127.0.0.1:8787 login --email you@example.com
sturgis agents list
sturgis agents create "Research companion"
```

Login prompts for the password. On first bootstrap, add `--bootstrap` and enter
the installation token at its private prompt. Set `agent_id` and `conversation_id`
below from the IDs returned by your own installation:

```sh
agent_id=YOUR_AGENT_ID
sturgis chat "$agent_id"
sturgis command "$agent_id" conversations.list
conversation_id=YOUR_CONVERSATION_ID
sturgis events "$agent_id" --conversation "$conversation_id" --follow
sturgis stop "$agent_id" "$conversation_id"
sturgis doctor --agent "$agent_id"
sturgis backup "$agent_id" ./research.sturgis
sturgis restore ./research.sturgis --name "Restored research"
```

Repeating the CLI restore with the same archive and name reuses its request
identity. For an intentional additional copy, choose a new identity explicitly:
`sturgis restore ./research.sturgis --name "Restored research" --request-id second_copy`.
Keep that identity when retrying an ambiguous response. An interrupted,
unpublished restore is removed and reports `restore_interrupted`; a new attempt
then needs a new identity. Reusing an identity with changed content or name is a
conflict. Restore identities are private to the authenticated account.

`chat` prompts for text. Use `--text-file notes.txt`, or `--text-file -` for stdin,
to submit existing text; `--no-wait` returns after admission. `events --after N`
replays from a saved cursor. Store a model credential with
`sturgis credential "$agent_id" model_key`, which prompts privately.

For a supported domain operation with parameters, use a JSON file:

```sh
sturgis command "$agent_id" knowledge.list --params-file query.json
```

For example, `query.json` may contain `{"query":"observatory","limit":20}`.
Use a stable `--request-id` only when retrying the same exact command. An uncertain
effectful request is not automatically re-executed. Use `sturgis --help` and
`sturgis COMMAND --help` for the installed command surface.
