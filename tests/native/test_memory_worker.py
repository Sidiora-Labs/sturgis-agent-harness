"""Exercise the compiled worker through real pipes and temporary SQLite stores."""
from __future__ import annotations

import itertools
import json
import os
from pathlib import Path
import select
import sqlite3
import struct
import subprocess
import time

import pytest


BINARY = Path(os.environ.get("STURGIS_MEMORY_BINARY", ".build/native/sturgis-memory")).resolve()


def record(identifier="m1", content="compiler dependency missing header", **changes):
    value = {
        "id": identifier, "kind": "experience", "content": content,
        "sources": [{"id": "event_1", "revision": 1, "start": 0, "end": 8}],
        "source_time": 100.0, "observed_at": 101.0, "valid_from": 100.0,
        "valid_to": None, "concepts": ["compiler", "dependency"],
        "embedding": {"model": "test-space-v1", "vector": [1.0, 0.0]},
    }
    value.update(changes)
    return value


class Worker:
    def __init__(self, db):
        self.db = Path(db)
        self.counter = itertools.count()
        self.process = subprocess.Popen(
            [str(BINARY), "--db", str(db)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
        )

    def exact(self, size):
        output = b""
        deadline = time.monotonic() + 10
        while len(output) < size:
            ready, _, _ = select.select([self.process.stdout], [], [], max(0, deadline - time.monotonic()))
            assert ready, "worker response timed out"
            part = os.read(self.process.stdout.fileno(), size - len(output))
            if not part:
                raise AssertionError(f"worker exited: {self.process.stderr.read()!r}")
            output += part
        return output

    def receive(self):
        size = struct.unpack(">I", self.exact(4))[0]
        assert 0 < size <= 1048576
        return json.loads(self.exact(size))

    def raw(self, text):
        payload = text.encode() if isinstance(text, str) else text
        self.process.stdin.write(struct.pack(">I", len(payload)) + payload)
        return self.receive()

    def call(self, operation, arguments=None, scope="s1", **envelope):
        request = {"protocol_version": "memory/1", "request_id": f"request-{next(self.counter)}",
                   "operation": operation, "scope": scope, "arguments": {} if arguments is None else arguments}
        request.update(envelope)
        return self.raw(json.dumps(request))

    def mutate(self, operation, **arguments):
        arguments.setdefault("operation_id", f"operation-{next(self.counter)}")
        return self.call(operation, arguments)

    def ingest(self, value=None, expected_revision=0, **kwargs):
        return self.mutate("ingest", record=value or record(), expected_revision=expected_revision, **kwargs)

    def begin(self, parent=0, **kwargs):
        response = self.mutate("begin_generation", expected_parent=parent, **kwargs)
        assert response["status"] == "ok", response
        return response["data"]["generation"]

    def publish(self, generation, parent=0, **kwargs):
        return self.mutate("publish", generation=generation, expected_parent=parent, **kwargs)

    def lease(self, generation, lease_id="reader", **kwargs):
        response = self.mutate("lease", generation=generation, lease_id=lease_id, **kwargs)
        assert response["status"] == "ok", response
        return response

    def close(self):
        if getattr(self, "closed", False):
            return
        if self.process.poll() is None:
            self.process.stdin.close()
        assert self.process.wait(timeout=10) == 0, self.process.stderr.read()
        assert self.process.stderr.read() == b""
        self.process.stdout.close()
        self.process.stderr.close()
        self.closed = True


@pytest.fixture
def worker(tmp_path):
    instance = Worker(tmp_path / "memory.sqlite")
    yield instance
    instance.close()


def ids(response):
    return [item["record"]["id"] for item in response["data"]["records"]]


def test_durable_revision_evidence_and_idempotency(worker):
    assert worker.call("ping")["data"] == {"ready": True, "current_generation": 0, "pending_generations": 0}
    initial = worker.ingest(operation_id="stable")
    assert initial["status"] == "ok"
    memory = initial["data"]["record"]
    assert memory["scope"] == "s1" and memory["revision"] == 1 and memory["watermark"] == 1
    assert memory["embedding"]["dimension"] == 2
    assert memory["sources"] == record()["sources"] and not memory["retracted"]
    retry = worker.ingest(operation_id="stable")
    assert retry["data"] == initial["data"] and retry["watermark"] == 1
    conflict = worker.ingest(record(content="different"), operation_id="stable")
    assert conflict["status"] == "conflict" and conflict["error"]["code"] == "idempotency"
    assert worker.ingest()["error"]["code"] == "revision"
    updated = worker.ingest(record(content="compiler repaired"), expected_revision=1)
    assert updated["data"]["record"]["revision"] == 2
    assert updated["watermark"] == 2
    assert worker.call("get_revision", {"id": "m1", "revision": 1})["data"]["record"]["content"] == record()["content"]
    assert worker.call("get_revision", {"id": "m1"})["data"]["record"]["content"] == "compiler repaired"
    assert worker.call("get_revision", {"id": "unknown"})["error"]["code"] == "not_found"
    assert worker.call("get_revision", {"id": "m1"}, scope="other")["error"]["code"] == "not_found"
    assert ids(worker.call("query", scope="other")) == []
    worker.close()
    replacement = Worker(worker.db)
    try:
        assert replacement.call("get_revision", {"id": "m1"})["data"]["record"]["revision"] == 2
        assert replacement.ingest(operation_id="stable")["data"] == initial["data"]
    finally:
        replacement.close()


def test_lexical_concept_vector_scores_and_time(worker):
    worker.ingest(record("a", "Compiler dependency!"))
    worker.ingest(record("b", "compiler logging", concepts=["observability"],
                         embedding={"model": "test-space-v1", "vector": [0.0, 1.0]}))
    worker.ingest(record("c", "unrelated database", concepts=[], valid_from=200))
    query = worker.call("query", {"text": "COMPILER dependency", "concepts": ["dependency"],
                                    "embedding": {"model": "test-space-v1", "vector": [1, 0]}, "at": 150})
    assert query["status"] == "ok" and ids(query) == ["a", "b"]
    first = query["data"]["records"][0]
    assert first["lexical_score"] == 1 and first["concept_score"] == 1 and first["vector_score"] == pytest.approx(1)
    assert first["score"] == 3
    assert ids(worker.call("query", {"at": 99})) == []
    assert ids(worker.call("query", {"at": 100, "text": "compiler"})) == ["a", "b"]
    assert ids(worker.call("query", {"at": 200, "limit": 1})) == ["a"]
    assert ids(worker.call("query", {"concepts": ["observability"]})) == ["b"]
    worker.ingest(record("a", valid_to=300), expected_revision=1)
    assert "a" not in ids(worker.call("query", {"at": 300}))
    assert "a" in ids(worker.call("query", {"at": 299}))
    # Expired latest revisions must not resurrect historical revisions.
    assert "a" not in ids(worker.call("query", {"at": 400}))
    no_embedding = record("no-vector", "documents", concepts=[])
    del no_embedding["embedding"]
    del no_embedding["valid_to"]
    worker.ingest(no_embedding)
    assert ids(worker.call("query", {"text": "documents"})) == ["no-vector"]


def test_embedding_spaces_are_explicit_and_scoped(worker):
    worker.ingest()
    wrong = record("bad", embedding={"model": "test-space-v1", "vector": [1, 0, 0]})
    response = worker.ingest(wrong)
    assert response["status"] == "conflict" and response["error"]["code"] == "embedding_space"
    assert worker.call("ping")["watermark"] == 1
    worker.ingest(record("other", embedding={"model": "other-space", "vector": [1, 0]}))
    query = worker.call("query", {"embedding": {"model": "test-space-v1", "vector": [1, 0]}})
    assert query["status"] == "partial" and ids(query) == ["m1"]
    unknown = worker.call("query", {"text": "header", "embedding": {"model": "missing", "vector": [1, 0]}})
    assert unknown["status"] == "partial" and ids(unknown) == ["m1", "other"]
    dimension = worker.call("query", {"embedding": {"model": "test-space-v1", "vector": [1, 0, 0]}})
    assert dimension["status"] == "partial" and ids(dimension) == []
    arguments = {"operation_id": "other", "expected_revision": 0, "record": wrong}
    assert worker.call("ingest", arguments, scope="separate")["status"] == "ok"


def test_generation_publication_leases_retractions_and_restart(worker):
    worker.ingest()
    generation = worker.begin(operation_id="begin")
    assert worker.begin(operation_id="begin") == generation
    rival = worker.begin()
    assert worker.call("ping")["data"]["pending_generations"] == 2
    staged = record("procedure", "Include required compiler headers", kind="procedure")
    assert worker.mutate("stage", generation=generation, records=[staged])["status"] == "ok"
    assert "procedure" not in ids(worker.call("query"))
    assert worker.publish(generation, operation_id="pub")["status"] == "ok"
    assert worker.publish(generation, operation_id="pub")["status"] == "ok"
    assert set(ids(worker.call("query"))) == {"m1", "procedure"}
    assert worker.publish(rival)["status"] == "conflict"
    assert worker.mutate("begin_generation", expected_parent=0)["error"]["code"] == "parent"
    worker.lease(generation)
    worker.ingest(record(content="new actual observation"), expected_revision=1)
    worker.ingest(record("fresh", "fresh canonical observation"))
    assert set(ids(worker.call("query"))) == {"m1", "fresh", "procedure"}
    snapshot = worker.call("query", {"lease_id": "reader"})
    assert snapshot["data"]["source_watermark"] == 1 and snapshot["watermark"] == 3
    assert set(ids(snapshot)) == {"m1", "procedure"}
    assert next(x for x in snapshot["data"]["records"] if x["record"]["id"] == "m1")["record"]["revision"] == 1
    worker.mutate("retract", id="m1", revision=1, reason="bad original evidence")
    assert "m1" not in ids(worker.call("query", {"lease_id": "reader"}))
    assert "m1" in ids(worker.call("query"))
    assert worker.call("get_revision", {"id": "m1", "revision": 1})["data"]["record"]["retracted"]
    worker.mutate("retract", id="m1", reason="suppress all versions")
    worker.ingest(record(content="later revision"), expected_revision=2)
    assert "m1" not in ids(worker.call("query"))
    worker.close()
    replacement = Worker(worker.db)
    try:
        assert replacement.call("ping")["data"]["current_generation"] == generation
        assert "m1" not in ids(replacement.call("query", {"lease_id": "reader"}))
        assert replacement.publish(generation, operation_id="pub")["status"] == "ok"
        assert replacement.call("release", {"lease_id": "reader"})["data"]["released"]
        assert not replacement.call("release", {"lease_id": "reader"})["data"]["released"]
        assert replacement.call("query", {"lease_id": "reader"})["error"]["code"] == "lease"
    finally:
        replacement.close()


def test_stage_atomicity_parent_copy_and_canonical_precedence(worker):
    worker.ingest()
    g1 = worker.begin()
    first = record("derived", "first strategy", kind="belief")
    worker.mutate("stage", generation=g1, records=[first])
    overwrite = record("derived", "second strategy", kind="observation")
    worker.mutate("stage", generation=g1, records=[overwrite])
    worker.publish(g1)
    worker.lease(g1, "old")
    g2 = worker.begin(parent=g1)
    # Whole invalid batch rolls back even when its first record was valid.
    response = worker.mutate("stage", generation=g2, records=[record("rolled-back"), record()])
    assert response["status"] == "conflict"
    assert worker.mutate("stage", generation=g2, records=[record("x"), record("x")])["status"] == "invalid_request"
    worker.mutate("stage", generation=g2, records=[record("derived", "third strategy")])
    worker.publish(g2, parent=g1)
    assert "rolled-back" not in ids(worker.call("query"))
    assert "x" not in ids(worker.call("query"))
    old = worker.call("query", {"lease_id": "old", "text": "strategy"})
    assert old["data"]["records"][0]["record"]["content"] == "second strategy"
    new = worker.call("query", {"text": "strategy"})
    assert new["data"]["records"][0]["record"]["content"] == "third strategy"
    worker.ingest(record("derived", "canonical replacement"))
    assert ids(worker.call("query", {"text": "strategy"})) == []
    assert ids(worker.call("query", {"text": "canonical"})) == ["derived"]
    assert worker.mutate("stage", generation=g2, records=[first])["error"]["code"] == "generation_state"
    assert worker.publish(999, parent=g2)["error"]["code"] == "generation_state"


def test_snapshot_watermark_validation_and_lease_expiration(worker):
    assert worker.mutate("lease", lease_id="x")["error"]["code"] == "generation_state"
    worker.ingest()
    worker.ingest(record(content="changed"), expected_revision=1)
    g1 = worker.begin(source_watermark=1)
    worker.publish(g1)
    worker.lease(g1, ttl_seconds=1)
    assert worker.mutate("lease", generation=g1, lease_id="reader")["error"]["code"] == "lease"
    assert worker.call("query", {"lease_id": "reader"}, scope="other")["error"]["code"] == "lease"
    assert worker.mutate("begin_generation", expected_parent=g1, source_watermark=0)["status"] == "invalid_request"
    assert worker.mutate("begin_generation", expected_parent=g1, source_watermark=3)["status"] == "invalid_request"
    with sqlite3.connect(worker.db) as db:
        db.execute("UPDATE leases SET expires=0")
    assert worker.call("query", {"lease_id": "reader"})["error"]["code"] == "lease"
    assert worker.mutate("lease", lease_id="auto")["status"] == "ok"
    assert worker.mutate("lease", lease_id="wrong", ttl_seconds=3601)["status"] == "invalid_request"


def test_bounded_prune_protects_leases_current_and_canonical_evidence(worker):
    worker.ingest()
    first = worker.begin()
    worker.mutate("stage", generation=first, records=[record("shared", "reusable insight")])
    worker.publish(first)
    worker.lease(first, "first-reader")
    abandoned = worker.begin(parent=first)
    worker.mutate("stage", generation=abandoned, records=[record("unused", "abandoned insight")])
    current = worker.begin(parent=first)
    worker.publish(current, parent=first)
    result = worker.mutate("prune", generations=[first, abandoned, current, 999])
    assert result["status"] == "ok"
    assert result["data"]["pruned"] == [abandoned]
    assert result["data"]["derived_removed"] == 1
    assert result["data"]["protected"] == [
        {"generation": first, "reason": "leased"}, {"generation": current, "reason": "current"},
        {"generation": 999, "reason": "not_found"},
    ]
    assert set(ids(worker.call("query", {"lease_id": "first-reader"}))) == {"m1", "shared"}
    worker.call("release", {"lease_id": "first-reader"})
    result = worker.mutate("prune", generations=[first], operation_id="retire-first")
    assert result["data"]["pruned"] == [first] and result["data"]["derived_removed"] == 0
    assert worker.mutate("prune", generations=[first], operation_id="retire-first")["data"] == result["data"]
    assert set(ids(worker.call("query"))) == {"m1", "shared"}
    assert worker.mutate("lease", generation=first, lease_id="retired")["error"]["code"] == "generation_state"
    assert worker.mutate("stage", generation=abandoned, records=[record("x")])["error"]["code"] == "generation_state"
    assert worker.call("get_revision", {"id": "m1"})["status"] == "ok"
    with sqlite3.connect(worker.db) as db:
        assert db.execute("SELECT count(*) FROM objects WHERE origin=0").fetchone()[0] == 1
        assert db.execute("SELECT state FROM generations WHERE generation=?", (first,)).fetchone()[0] == "retired"
        assert db.execute("SELECT count(*) FROM operations WHERE operation='stage'").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM objects WHERE id='unused'").fetchone()[0] == 0
    for batch in [[], [current, current], list(range(1, 102)), [0]]:
        assert worker.mutate("prune", generations=batch)["status"] == "invalid_request"
    assert worker.call("ping")["generation"] == current


def test_database_projection_failure_rolls_back_and_blocks_publication(worker):
    worker.ingest()
    generation = worker.begin()
    # Storage failure after canonical watermark advancement must roll everything back.
    with sqlite3.connect(worker.db) as db:
        db.execute("CREATE TRIGGER broken BEFORE INSERT ON projections BEGIN SELECT RAISE(ABORT, 'injected disk-layer failure'); END")
    response = worker.ingest(record("failure"), operation_id="retry-after-repair")
    assert response["status"] == "unavailable" and response["error"]["code"] == "storage"
    assert worker.call("ping")["watermark"] == 1
    with sqlite3.connect(worker.db) as db:
        assert db.execute("SELECT count(*) FROM objects WHERE id='failure'").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM operations WHERE id='retry-after-repair'").fetchone()[0] == 0
        db.execute("DROP TRIGGER broken")
    assert worker.ingest(record("failure"), operation_id="retry-after-repair")["status"] == "ok"
    # A snapshot lacking a projection cannot be atomically published as complete.
    with sqlite3.connect(worker.db) as db:
        db.execute("DELETE FROM projections WHERE uid=(SELECT uid FROM objects WHERE id='m1')")
    assert worker.publish(generation)["status"] == "unavailable"
    assert worker.call("ping")["generation"] == 0


def test_concurrent_workers_revision_and_parent_cas(worker):
    other = Worker(worker.db)
    try:
        assert worker.ingest()["status"] == "ok"
        assert other.ingest(operation_id="second-worker")["status"] == "conflict"
        g1 = worker.begin(operation_id="first-generation")
        g2 = other.begin(operation_id="second-generation")
        assert worker.publish(g1, operation_id="first-publish")["status"] == "ok"
        assert other.publish(g2, operation_id="second-publish")["status"] == "conflict"
    finally:
        other.close()


def test_crash_during_staging_recovers_atomic_batch_and_receipt(tmp_path):
    worker = Worker(tmp_path / "crash.db")
    worker.ingest()
    generation = worker.begin(operation_id="crash-generation")
    arguments = {"operation_id": "crash-stage", "generation": generation,
                 "records": [record(f"derived-{i}", "evidence " * 1000) for i in range(50)]}
    request = {"protocol_version": "memory/1", "request_id": "crash-request", "operation": "stage",
               "scope": "s1", "arguments": arguments}
    payload = json.dumps(request).encode()
    worker.process.stdin.write(struct.pack(">I", len(payload)) + payload)
    worker.process.kill()
    worker.process.wait(timeout=10)
    for pipe in [worker.process.stdin, worker.process.stdout, worker.process.stderr]:
        pipe.close()
    with sqlite3.connect(worker.db) as db:
        count = db.execute("SELECT count(*) FROM objects WHERE origin=?", (generation,)).fetchone()[0]
        receipts = db.execute("SELECT count(*) FROM operations WHERE id='crash-stage'").fetchone()[0]
        assert (count, receipts) in {(0, 0), (50, 1)}
    replacement = Worker(worker.db)
    try:
        assert replacement.call("ping")["data"]["pending_generations"] == 1
        assert replacement.call("stage", arguments)["status"] == "ok"
        assert replacement.publish(generation, operation_id="crash-publish")["status"] == "ok"
        assert len(ids(replacement.call("query", {"limit": 100}))) == 51
    finally:
        replacement.close()


@pytest.mark.parametrize("changes", [
    {"kind": "invented"}, {"scope": "escape"}, {"id": ""}, {"content": ""},
    {"id": "nul\0id"}, {"id": "x" * 257}, {"content": "x" * 32769},
    {"sources": []}, {"sources": "not-array"}, {"sources": [{}]},
    {"sources": [{"id": "s", "revision": 1, "start": 8, "end": 8}]},
    {"sources": [{"id": "s", "revision": 0, "start": 0, "end": 8}]},
    {"sources": [{"id": "s", "revision": 2**64-1, "start": 0, "end": 8}]},
    {"source_time": -1}, {"observed_at": "today"}, {"valid_from": -1}, {"valid_to": 99},
    {"concepts": ["a", "a"]}, {"concepts": "a"}, {"concepts": ["x"] * 65},
    {"embedding": {"model": "space", "vector": []}},
    {"embedding": {"model": "space", "vector": [0, 0]}},
    {"embedding": {"model": "space", "vector": ["bad"]}},
    {"embedding": {"model": "space", "vector": [1] * 2049}},
    {"embedding": {"model": "space", "vector": [1], "dimension": 1}},
])
def test_invalid_records_have_no_effect(worker, changes):
    response = worker.ingest(record(**changes))
    assert response["status"] == "invalid_request"
    assert worker.call("ping")["watermark"] == 0
    assert ids(worker.call("query")) == []


@pytest.mark.parametrize("operation,args", [
    ("unknown", {}), ("ping", {"other": 1}), ("query", {"limit": 0}),
    ("query", {"limit": True}), ("query", {"at": -1}), ("query", {"limit": 101}),
    ("get_revision", {"id": "m", "revision": 0}), ("ingest", {}),
    ("ingest", {"operation_id": "", "record": {}, "expected_revision": 0}),
    ("ingest", {"operation_id": "o", "record": record(), "expected_revision": 1.5}),
    ("release", {"lease_id": 3}),
    ("retract", {"operation_id": "o", "id": "m1", "reason": "", "revision": 1}),
])
def test_invalid_operations(worker, operation, args):
    assert worker.call(operation, args)["status"] == "invalid_request"
    assert worker.call("ping")["watermark"] == 0


@pytest.mark.parametrize("envelope", [
    {"protocol_version": "memory/2"}, {"request_id": 7}, {"scope": ""},
    {"scope": "x\0y"}, {"operation": False}, {"arguments": []}, {"unexpected": True},
])
def test_strict_envelope(worker, envelope):
    assert worker.call(**({"operation": "ping"} | envelope))["status"] == "invalid_request"
    assert worker.call("ping")["status"] == "ok"


@pytest.mark.parametrize("payload", [
    "{}", "[]", "null", "not-json", "{\"a\":1,\"a\":2}",
    "{\"a\":{\"x\":1,\"x\":2}}", "[" * 34 + "0" + "]" * 34,
    "{\"value\":NaN}", "{\"value\":1e999}", b'{"value":"\xff"}',
])
def test_malformed_json_does_not_desynchronize(worker, payload):
    response = worker.raw(payload)
    assert response["status"] == "invalid_request"
    assert worker.call("ping")["status"] == "ok"


@pytest.mark.parametrize("wire", [b"\0", struct.pack(">I", 100) + b"short", struct.pack(">I", 0), struct.pack(">I", 1048577)])
def test_bad_frame_terminates_cleanly(tmp_path, wire):
    process = subprocess.run([str(BINARY), "--db", str(tmp_path / "frames.db")], input=wire,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    assert process.returncode == 2 and process.stdout == b"" and process.stderr


def test_multiple_frames_and_fragmented_input(worker):
    payload = json.dumps({"protocol_version": "memory/1", "request_id": "chunks",
                          "operation": "ping", "scope": "s1", "arguments": {}}).encode()
    frame = struct.pack(">I", len(payload)) + payload
    for part in [frame[:1], frame[1:3], frame[3:10], frame[10:]]:
        worker.process.stdin.write(part)
    assert worker.receive()["request_id"] == "chunks"
    worker.process.stdin.write(frame + frame)
    assert worker.receive()["status"] == "ok"
    assert worker.receive()["status"] == "ok"


def test_startup_errors(tmp_path):
    assert subprocess.run([str(BINARY)], capture_output=True).returncode == 2
    assert subprocess.run([str(BINARY), "--other", "x"], capture_output=True).returncode == 2
    failure = subprocess.run([str(BINARY), "--db", str(tmp_path / "missing" / "db")], capture_output=True)
    assert failure.returncode == 2 and failure.stderr
    db_path = tmp_path / "future.db"
    with sqlite3.connect(db_path) as db:
        db.execute("PRAGMA user_version=9")
    assert subprocess.run([str(BINARY), "--db", str(db_path)], capture_output=True).returncode == 2
    garbage = tmp_path / "garbage.db"
    garbage.write_bytes(b"not a database at all" * 100)
    assert subprocess.run([str(BINARY), "--db", str(garbage)], capture_output=True).returncode == 2


def test_response_bound_is_truthful(worker):
    for i in range(34):
        assert worker.ingest(record(f"large-{i}", "word " * 6500))["status"] == "ok"
    response = worker.call("query", {"limit": 100})
    assert response["status"] == "partial" and response["error"]["code"] == "response_size"
    assert response["watermark"] == 34 and response["generation"] == 0
    assert worker.call("query", {"limit": 1})["status"] == "ok"


def test_query_scan_bound_reports_partial(worker):
    # Seed repeated real stored projections to exercise a large indexed corpus cheaply.
    worker.ingest()
    with sqlite3.connect(worker.db) as db:
        payload, lexical, concepts, model, dimension, vector = db.execute(
            "SELECT payload,lexical,concepts,model,dimension,vector FROM objects JOIN projections USING(uid)"
        ).fetchone()
        for i in range(10001):
            row = json.loads(payload)
            row["id"] = f"bulk-{i:05}"
            cursor = db.execute("INSERT INTO objects(scope,id,revision,payload,watermark,origin) VALUES(?,?,1,?,1,0)",
                                ("s1", row["id"], json.dumps(row)))
            db.execute("INSERT INTO projections VALUES(?,?,?,?,?,?)", (cursor.lastrowid, lexical, concepts, model, dimension, vector))
    response = worker.call("query", {"text": "compiler", "limit": 2})
    assert response["status"] == "partial" and response["data"]["truncated"]
    assert response["error"]["code"] == "candidate_limit" and len(ids(response)) == 2
