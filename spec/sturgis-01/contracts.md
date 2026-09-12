# Frozen implementation seams

These are the initial shared APIs for parallel work. Public changes require root coordination. Domain modules may define richer records internally but exchange JSON-compatible values and the canonical types below. All paths use Python import names with underscores.

## Python foundation

`agent.protocol` exports:

- `CoreError(code: str, message: str, **details)` with `.code`, `.details`, `.as_dict()`.
- `new_id(prefix: str) -> str`, `canonical_json(value) -> str`, `strict_loads(text: str | bytes) -> object`; reject duplicate JSON keys/nonfinite numbers and oversized/non-JSON data at the relevant boundary.
- `Event` frozen record with `id`, `seq`, `scope`, `kind`, `source`, `payload`, `created_at`.
- `ToolCall` with `tool`, `arguments`, `tool_version="1"`, `schema_version="1.0"`, `expectation`; `from_dict()` validates the uniform model-visible call.
- `ToolResult` with `action_id`, `attempt_id`, `tool`, `state`, `outcome`, `data`, `error`, `effects`, `completeness`; `.as_dict()` and lifecycle validation.

`agent.state` exports `Journal(path)` and frozen `Record(namespace, id, scope, revision, value)`:

```python
with journal.transaction():             # synchronous, short, nested-safe; never await here
    row = journal.get("goals", key, scope)
    updated = journal.put("goals", key, scope, body,
                          expected_revision=row.revision if row else 0)
    event = journal.append(scope, "goal.changed", body,
                           source="goals", dedupe_key=None)
```

`get(namespace, key, scope) -> Record | None`; `put(..., expected_revision=0) -> Record` creates with revision 1, updates only when the existing revision matches, and raises `CoreError("conflict", ...)` otherwise. `list(namespace, scope) -> list[Record]`; `delete(namespace,key,scope,expected_revision)` checks the revision. `events(scope, after=0, limit=1000, kinds=None) -> list[Event]`; `cursor(scope, consumer) -> int`; `advance_cursor(scope, consumer, seq)` is monotonic; `close()` releases resources. Event dedupe is keyed within scope and must reject reuse with different kind/content/source. Journal metadata comes from runtime code, never model text.

`agent.artifacts` exports `ArtifactStore(root)`, `put(scope, data: bytes, media_type="application/octet-stream") -> Artifact`, `get(scope, artifact_id) -> bytes`. `Artifact` carries `id`, `scope`, `size`, `sha256`, `media_type`. Storage paths are rooted/scoped and content hashes verified on read.

`agent.framing` exports `FrameCodec(max_frame=1048576).feed(bytes) -> list[bytes]`, `.finish()` for EOF validation, `encode_frame(payload: bytes, max_frame=1048576) -> bytes`, `async read_frame(reader,max_frame=...) -> bytes | None` and `async write_frame(writer,payload,max_frame=...)`. Header is four unsigned big-endian bytes followed by exactly that many bytes. Clean EOF differs from truncated frame. ACP newline transport is not reinterpreted as this internal codec.

## Native memory/1

Worker executable is `sturgis-memory`, launched with `--db PATH`; stdin/stdout are framed JSON, stderr contains diagnostics. No application/network listener is added. C++20 uses SQLite and a pinned external JSON parser. Every request is one object:

```json
{"protocol_version":"memory/1","request_id":"r1","operation":"ping","scope":"s1","arguments":{}}
```

Response repeats `request_id`, with `status` (`ok`, `partial`, `unavailable`, `conflict`, `invalid_request`), `data`, `error`, and available generation/watermark metadata. Unknown operations and malformed fields never become success. A maximum frame size and finite numeric validation apply.

The native owner publishes exact argument schemas for `ingest`, `query`, `get_revision`, `begin_generation`, `stage`, `publish`, `release`, `retract` and `ping` in `native/memory/PROTOCOL.md` before Python client implementation. Records include scope, memory/concept identity, revision, content, source evidence/time, validity/retraction, embedding model/dimension/vector. Publication includes expected parent and an idempotent operation ID; source watermark and snapshot lease are explicit. The Python owner must use the actual compiled worker, never an in-process replacement to satisfy tests.

## Domain integration

- Synchronous durable domain operations use the Journal APIs above; async runtime calls them through bounded off-loop work where needed.
- Scheduler exposes `authorize(scope: str, grant: dict, call: ToolCall) -> dict`, synchronous inside the dispatch journal transaction after intent append. Grant includes runtime-bound `work_id`, `generation`, runner-generated `claim_id=attempt_id`, `action_id`, `attempt_id`, registry-derived `required_capabilities` and requested `resources`. `ToolDispatcher(journal, *, authorize)` has no permissive default; `async invoke(call, *, scope, work_id, generation, request_id=None, cancel=None) -> ToolResult`. Request replay deduplicates before reservation. Production runtime binds the actual scheduler callback.
- Goal accounting exposes cumulative `reserve`, `charge` and `release`. Actual usage above a reservation is durably recorded as overage and prevents further allowance; observed usage must not be rejected or capped to hide the overrun. Commitment waiting uses `register_wait(scope, owner_id, trigger, *, wait_id, after_seq, expires_at) -> Record` and `cancel_wait(scope, wait_id)` supplied by the scheduler.
- Tool-runner is the common invocation facade with registered executors. Process/file/plugin/control executors retain specific payload schemas and restrictions. Inference cannot create scope/grant metadata from emitted arguments.
- Provider adapters expose asynchronous canonical stream events for visible text, complete tool proposals, usage and terminal/error state. They accept immutable role-preserving context and explicit cancellation. Boundary owner publishes the concrete API before runtime composition.
- Delivery and channel/ACP correlation are durable domain records. Ordinary ACP notifications remain unconfirmed sends. No facade spins an independent planning loop.
- Agent owners publish class/function signatures and their focused test command to root before dependent tasks start. Root composes the actual APIs in `agent/runtime`; no duplicate domain implementations are allowed there.

Delegated tool authority adds `constraints={workspace_root: absolute_canonical_path, workspace_writable: bool}`. Rooted file/process executors enforce these scheduler-owned fields; executors without an explicit constraint-aware implementation reject delegated claims before effects. Caller/model arguments cannot set them.

`Journal.event(scope,event_id)` retrieves a scoped immutable event by indexed identity, returning `None` when absent; durable runtime input lookup does not depend on a bounded history scan.
