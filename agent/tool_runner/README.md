# Capability execution API

`ToolDispatcher(journal, *, authorize)` requires the real scheduler callback in
runtime composition. `authorize(scope, grant, call)` executes synchronously
inside the same `Journal.transaction()` as durable intent and attempt creation.
The grant contains authenticated `work_id`/`generation`, runner-created
`action_id`, `attempt_id`, `claim_id=attempt_id`, registry-derived capabilities,
and worst-case resources `{tool_calls: 1, elapsed_ms: ceil(timeout * 1000)}`.

Register an asynchronous executor with:

```python
dispatcher.register(name, executor, input_schema=..., output_schema=...,
                    version="1", required_capabilities=(), read_only=False,
                    timeout=30, output_limit=1048576)
result = await dispatcher.invoke(call, scope=scope, work_id=work_id,
                                 generation=generation, request_id=request_id,
                                 cancel=cancellation_event)
```

The executor receives `(arguments, ExecutionContext)` and returns
`ExecutorResult(data, outcome="success", effects="unknown")`. The context carries
scope, work/generation, action/attempt identities and cancellation. Local schema
references are supported; remote schema retrieval is explicitly rejected.

Known terminal attempts persist a deduplicated `tool.usage` event in the same
transaction as their terminal result. Its payload is `{work_id, claim_id,
resources: {tool_calls: 1, elapsed_ms: observed_monotonic_ms}}`. Timeouts, uncertain
cancellation and recovered in-flight attempts retain unresolved reservations.
`reconcile(scope, request_id, terminal_result, evidence_event_ids=[...], usage=...)`
records scoped reconciliation; observed local usage is reused if present, and a
crash with no measured usage never invents a zero receipt. Scheduler recovery
must resolve any remaining reservation explicitly.

Reconciliation accepts only `tool.observation` events whose source is
`tool_observer`. A trusted backend observer checks the actual process, file, or
external receipt, then appends this exact payload:

```python
observation = journal.append(scope, "tool.observation", {
    "schema_version": "tool-observation/1",
    "request_id": request_id,
    "work_id": recorded_request.value["work_id"],
    "request_event_id": original_tool_request_event.id,
    "result": terminal_result.as_dict(),
    "usage": measured_resources,  # {tool_calls: 1, elapsed_ms: integer}, or None
}, source="tool_observer")
admitted = dispatcher.reconcile(scope, request_id, terminal_result,
    evidence_event_ids=[observation.id], usage=measured_resources)
```

The observation's result is the full canonical `ToolResult`, including data,
error, completeness, and effect certainty. Its `effects.evidence_event_ids` is
initially empty. The dispatcher validates the exact earlier `tool_runner` /
`tool.request` event, scope, work, invocation arguments, action, and attempt.
Every supplied observation must attest the same terminal result and usage.
The returned and persisted result attaches the admitted observation IDs; the
original indeterminate result remains in the journal and reconciliation record.
Unrelated, missing, duplicate, cross-scope, mismatched, or narrative evidence
rejects the whole transaction. Completion of an invocation can coexist with
unknown world effects; the observer must not infer effect certainty from success.

When prior local measured usage exists, an omitted `usage` argument reuses it,
and the observation must attest those same counts. After a crash without local
measurements, a trusted observer may supply actual measured counts explicitly.
If both are unavailable, the observation records `usage: None`: terminal evidence
can be admitted, but no `tool.usage` event settles the outstanding reservation.

`tool_observer` is an explicit trusted in-process backend boundary, not a
cryptographic identity or attestation. Only trusted observer integrations receive
direct journal access under that source. Model output, memory summaries, child
summaries, and ordinary incoming channel content are evidence candidates; their
own events cannot authorize reconciliation. Do not expose a generic append-event
or reconcile capability that lets generated content choose observer provenance.
The core verifies causal joins and matching observations; the observer remains
responsible for verifying the external fact it records.

`ProcessRunner(root, allowed_programs, environment_keys=(), mounts=(),
writable=True)` runs actual Linux `bubblewrap` namespaces with `prlimit` CPU and
address-space bounds, a fresh environment, isolated networking, bounded separate
output streams and process-tree cleanup. There is no reduced-isolation fallback.
Linux, `bwrap`, `prlimit`, and working namespace support are required. Read-only
system files under `/usr`, `/lib`, `/lib64`, and `/bin` are visible; additional
mounts are explicit trusted-runtime configuration. Environment keys are opt-in.
Processes address their granted root as `/workspace`. This is not a Windows
execution backend.

`WorkspaceFs` provides descriptor-rooted read/list/search, preimage-checked atomic
write/edit/copy/rename/delete. `process_executor(runner)` and
`file_executor(workspace)` adapt these implementations to the dispatcher. File
operation arguments name `operation` plus the corresponding method's keyword
arguments; write uses a text `content` field. Binary results retain explicit
base64 encoding rather than silently replacing bytes. Executors still require
operation-specific registration schemas; registering `{}` does not define a
safe tool argument contract.

`PluginHost(journal, root, workspace_root=None)` installs immutable package
revisions, executes `plugin/1` framed Python workers in these namespaces, and
owns activation, state migration, drain/disable, health, rollback, quarantine
and restart recovery. `register_tools` publishes separately named
`plugin.ID.tool.NAME` and `plugin.ID.command.NAME` capabilities pinned to a
version and scope. Filesystem grants are `workspace.read` and `workspace.write`;
other grants are unsupported. Plugins can use `agent.plugin_sdk.serve(handler)`
for one request/result exchange. Handler state is explicit JSON, committed only
after a valid result; restoring a prior state does not undo external effects.

`SkillRegistry` discovers bounded `.skill.json` packages in configured built-in,
global and workspace roots; profile records have highest precedence. Selection
reports relevance, platform, unavailable-tool and context-budget exclusions.
Synthesis requires distinct successful scoped canonical tool observations;
configured review and explicit activation publish learned procedures. Procedure
selection evidence is not a claim that arbitrary future workflows will succeed.
