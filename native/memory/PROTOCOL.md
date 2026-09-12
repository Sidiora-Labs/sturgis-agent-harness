# Native memory/1

`sturgis-memory --db PATH` is a C++20 SQLite worker. Build `sturgis-memory`
from `native/memory/main.cpp` and `engine.cpp`, include this directory, and link
`SQLite::SQLite3`. The vendored nlohmann/json v3.12.0 header is the JSON parser.

Each stdin/stdout message has a four-byte unsigned big-endian byte length followed
by UTF-8 JSON. Length must be 1..1048576. JSON nesting is limited to 32 levels;
duplicate keys, nonfinite numbers, unknown fields, wrong types and versions fail.
Stdout contains frames only. Clean EOF exits zero; truncated/oversized input exits
nonzero. Malformed complete JSON receives an invalid_request response and the next
frame remains usable. Fatal startup/storage failures are diagnostics on stderr.

Request fields (all required, no others):
`protocol_version:"memory/1", request_id:string, operation:string, scope:string,
arguments:object`. IDs/scopes are 1..256 UTF-8 bytes without embedded NUL. One
worker serializes mutations; several workers may use the same database. Scope
is supplied by trusted runtime code; this private process does not authenticate
arbitrary untrusted callers. Envelope scope is always applied to every lookup.

Responses contain `protocol_version`, `request_id` (null when unparseable),
`status` (`ok`, `partial`, `unavailable`, `conflict`, `invalid_request`), `data`,
`error` (null or `{code,message}`), `generation`, and `watermark` (latest canonical
scope source sequence). Error codes include validation, not_found, revision,
idempotency, parent, generation_state, lease, embedding_space and storage.

## Record

Input record fields, all required except `embedding` and `valid_to`:

```json
{"id":"m1","kind":"experience","content":"Compile failed: missing header",
 "sources":[{"id":"event_7","revision":1,"start":0,"end":37}],
 "source_time":100.0,"observed_at":102.0,"valid_from":100.0,"valid_to":null,
 "concepts":["compiler","dependency"],
 "embedding":{"model":"embedding-v1","vector":[1.0,0.0]}}
```

Kinds are `fact`, `experience`, `observation`, `belief`, `procedure`.
Content is nonempty and at most 32768 bytes. Sources are 1..64 exact source
references with positive revision and byte interval `[start,end)` (`end>start`).
Concepts are unique bounded IDs (at most 64); source/time numbers must be finite
and nonnegative. Validity is `[valid_from,valid_to)`; null means unbounded.
Embeddings have model ID and 1..2048 finite coordinates with nonzero norm;
model ID binds to one dimension within a scope. Returned records add `scope`,
`revision`, `watermark`, `retracted`, and `embedding.dimension` where applicable.
Ingested source text is evidence, not authority that an external effect occurred.

## Operations and arguments

Every mutation except release requires `operation_id` (scope-wide idempotency
key). Retrying exactly the same operation and arguments returns its stored result;
reusing the key with different data conflicts. Failed operations do not reserve it.

| Operation | Arguments | Result data |
| --- | --- | --- |
| `ping` | `{}` | `ready:true`, `pending_generations`, `current_generation` |
| `ingest` | `{operation_id,expected_revision,record}` | `{record}`; expected 0 creates, otherwise matches latest canonical revision |
| `get_revision` | `{id,revision?}` | `{record}` or not_found; omitted revision means latest canonical; includes current retraction |
| `query` | `{text?,concepts?,embedding?,at?,limit?,lease_id?}` | `{records:[{record,score,lexical_score,concept_score,vector_score}],view,source_watermark,truncated}` |
| `begin_generation` | `{operation_id,expected_parent,source_watermark?}` | `{generation,parent,source_watermark,state:"staging"}` |
| `stage` | `{operation_id,generation,records:[record...]}` | `{generation,staged}` |
| `publish` | `{operation_id,generation,expected_parent}` | `{generation,source_watermark,state:"published"}` |
| `lease` | `{operation_id,lease_id,generation?,ttl_seconds?}` | `{lease_id,generation,source_watermark,expires_at}` |
| `release` | `{lease_id}` | `{released:boolean}`; idempotent |
| `retract` | `{operation_id,id,revision?,reason}` | `{id,revision,retracted:true}`; omitted revision suppresses all revisions including future ones |
| `prune` | `{operation_id,generations:[id...]}` | `{pruned:[id...],protected:[{generation,reason}],derived_removed}` |

`query` combines lexical token-overlap score, concept overlap and cosine similarity
by addition. Missing query components contribute zero. Blank query lists memories.
Vector scores are computed only within the requested embedding model/dimension;
other spaces are skipped and response is `partial` with `embedding_space` error.
An unregistered query space is also partial. Lexical/concept searches remain usable
in partial responses. `at` selects validity (default current Unix seconds); it does
not mean knowledge-as-of. Latest canonical revision wins even when it is expired.
Limit defaults to 20, maximum 100. A bounded 10000-record candidate scan reports
partial/truncated when exceeded. Score ties sort by ID. Historical revisions are
available only through `get_revision`, never silently substituted for invalid new
ones. Explicit retraction overrides every generation and historical lookup flag.

Live queries combine latest canonical revisions and the current published derived
records, with canonical IDs taking precedence. Fresh ingestion is immediately
eligible for live queries independently of consolidation. `source_watermark`
on a live query is the latest canonical sequence; on a leased query it is the
generation source watermark. Top-level `watermark` always reports the current
scope source sequence, so clients can compute snapshot staleness.

`begin_generation` requires current parent (0 initially), snapshots latest canonical
records at the requested watermark (default latest), and copies prior published
derived records. A watermark must be between parent source watermark and latest.
All copied/staged records and lexical/concept/vector projections are transactionally
complete before `publish` can CAS the current pointer. `stage` writes derived
records (revision 1, generation-local); canonical IDs in the snapshot cannot be
overwritten. Staged records do not affect live queries before publication.
Staging batches allow 1..100 records; each batch and its operation receipt are atomic.
Publish requires its stored parent and caller expected parent both equal current.
Conflicting generations remain staging for explicit diagnosis, not auto-rebased.

Leases pin an existing published generation for 1..3600 seconds (default 300).
Lease IDs are unique within scope and may not be reused while present. Leases are
durable across restart and released/expired leases cannot read. Generation 0 has
no leasable snapshot. `prune` explicitly requests 1..100 distinct generation IDs;
the current generation and generations with unexpired reader leases are protected.
Other requested generations (published or abandoned staging) become `retired`.
Their snapshot memberships and unreferenced derived records/projections are removed;
derived records shared by retained snapshots survive. Canonical revisions, source
evidence, generation metadata and idempotency receipts (including staging payloads)
remain durable. Missing IDs are returned as protected/not_found; a retired generation
cannot be leased, staged or published. No background pruning occurs without a request.
Current revision/all-ID retractions suppress old leased snapshots immediately.

SQLite WAL, FULL synchronous mode, foreign keys and `BEGIN IMMEDIATE` provide
atomic mutations, expected-parent/revision checks and durable idempotency receipts.
Startup reopens committed staging generations, leases and current publication;
SQLite rolls back incomplete transactions. No implicit publication occurs on restart.
Database schema version is 1; unknown versions fail closed. Mutation receipts are
retained without compaction. This worker provides memory, not model training or
external operation execution.
