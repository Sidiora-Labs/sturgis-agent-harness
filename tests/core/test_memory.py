"""Memory integration uses the compiled native worker and durable operational journal."""
import asyncio
import copy
from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import sqlite3
import sys
import time

import pytest

from agent.context import ContextCompiler
from agent.memory import MemoryIngestor, MemorySleep, NativeMemoryClient, memory_blocks
from agent.memory.sleep import consolidate_duplicates, validate_candidate
from agent.protocol import CoreError, canonical_json
from agent.state import Journal


BINARY = Path(".build/native/sturgis-memory").resolve()


def record(identifier="m1", content="Known compiler behavior", **changes):
    result = {"id": identifier, "kind": "experience", "content": content,
              "sources": [{"id": "source-1", "revision": 1, "start": 0, "end": 20}],
              "source_time": 100.0, "observed_at": 100.0, "valid_from": 100.0,
              "valid_to": None, "concepts": ["compiler"]}
    result.update(changes)
    return result


@pytest.fixture
async def client(tmp_path):
    value = NativeMemoryClient(tmp_path / "native.db", binary=BINARY)
    assert await value.start()
    yield value
    await value.close()


@pytest.fixture
def journal(tmp_path):
    value = Journal(tmp_path / "journal.db")
    yield value
    value.close()


async def seed(client, count=2):
    for i in range(count):
        result = await client.ingest("s1", record(f"source-{i}"), operation_id=f"seed-{i}")
        assert result["status"] == "ok"


def result_ids(response):
    return [item["record"]["id"] for item in response["data"]["records"]]


async def test_client_real_multiplexing_retrieval_and_scoped_context(client):
    replies = await asyncio.gather(*[client.ingest("s1", record(f"m-{i}"), operation_id=f"ingest-{i}") for i in range(20)])
    assert all(reply["status"] == "ok" for reply in replies)
    assert len({reply["request_id"] for reply in replies}) == 20
    query = await client.query("s1", text="compiler", concepts=["compiler"], at=200, limit=3)
    assert len(result_ids(query)) == 3
    assert result_ids(await client.query("different")) == []
    blocks, freshness = memory_blocks("s1", query)
    assert freshness["status"] == "ok" and freshness["stale"] is False
    assert all(block.role == "user" and block.kind == "memory" for block in blocks)
    packet = ContextCompiler().compile("s1", "work", blocks, context_limit=10000, output_reserve=100,
                                       metadata={"memory": freshness})
    assert packet.metadata["memory"]["current_watermark"] == 20
    assert len(packet.manifest) == 3
    denied = copy.deepcopy(query)
    denied["data"]["records"][0]["record"]["scope"] = "escape"
    with pytest.raises(CoreError, match="another scope"):
        memory_blocks("s1", denied)
    await client.close()
    assert not await client.start()
    unavailable = await client.query("s1")
    assert unavailable["status"] == "unavailable" and not unavailable["error"]["dispatched"]
    assert memory_blocks("s1", unavailable)[0] == []


async def test_deadline_cancel_and_late_frame_drain_preserve_responsiveness(client):
    await seed(client, 1)
    os.kill(client.process.pid, signal.SIGSTOP)
    try:
        heartbeat = asyncio.Event()
        async def foreground():
            await asyncio.sleep(0.01)
            heartbeat.set()
        task = asyncio.create_task(foreground())
        timeout = await client.query("s1", timeout=0.03)
        assert heartbeat.is_set()
        assert timeout["status"] == "unavailable" and timeout["error"]["effect_state"] == "unknown"
        pending = asyncio.create_task(client.query("s1"))
        await asyncio.sleep(0.01)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert not client._pending
        await task
    finally:
        os.kill(client.process.pid, signal.SIGCONT)
    # Both late responses get drained; new request correlation still works.
    assert result_ids(await client.query("s1")) == ["source-0"]


async def test_capacity_and_writer_wait_deadlines(client):
    client.max_pending = 1
    os.kill(client.process.pid, signal.SIGSTOP)
    first = asyncio.create_task(client.query("s1"))
    try:
        await asyncio.sleep(0.01)
        full = await client.query("s1")
        assert full["error"]["code"] == "capacity" and not full["error"]["dispatched"]
    finally:
        os.kill(client.process.pid, signal.SIGCONT)
    assert (await first)["status"] == "ok"
    await client._writer_lock.acquire()
    try:
        timeout = await client.query("s1", timeout=0.01)
        assert timeout["error"]["effect_state"] == "not_dispatched"
    finally:
        client._writer_lock.release()


async def test_worker_death_restart_and_unknown_dispatched_mutation(client):
    await seed(client, 1)
    os.kill(client.process.pid, signal.SIGSTOP)
    pending = asyncio.create_task(client.ingest("s1", record("unknown"), operation_id="recoverable"))
    await asyncio.sleep(0.02)
    client.process.kill()
    result = await pending
    assert result["status"] == "unavailable" and result["error"]["dispatched"]
    assert (await client.ingest("s1", record("unknown"), operation_id="recoverable"))["status"] == "ok"
    assert set(result_ids(await client.query("s1"))) == {"source-0", "unknown"}


async def test_snapshot_freshness_partial_vector_status_and_release(client):
    await seed(client, 1)
    begin = await client.request("s1", "begin_generation", {"operation_id": "begin", "expected_parent": 0})
    generation = begin["data"]["generation"]
    await client.request("s1", "publish", {"operation_id": "publish", "generation": generation, "expected_parent": 0})
    await client.request("s1", "lease", {"operation_id": "lease", "generation": generation, "lease_id": "reader"})
    await client.ingest("s1", record("later"), operation_id="later")
    snapshot = await client.query("s1", lease_id="reader")
    assert memory_blocks("s1", snapshot)[1]["stale"]
    partial = await client.query("s1", text="compiler", embedding={"model": "missing-space", "vector": [1, 0]})
    assert partial["status"] == "partial"
    assert len(memory_blocks("s1", partial)[0]) == 2
    assert memory_blocks("s1", partial)[1]["deferred"]
    await client.request("s1", "release", {"lease_id": "reader"})
    assert (await client.query("s1", lease_id="reader"))["status"] == "invalid_request"


@pytest.mark.parametrize("options", [{"timeout": 0}, {"timeout": float("nan")}, {"max_pending": 0},
                                     {"max_pending": True}, {"max_frame": 0}, {"max_frame": 1048577}])
def test_client_configuration(options, tmp_path):
    with pytest.raises(CoreError):
        NativeMemoryClient(tmp_path / "db", **options)


async def test_invalid_requests_and_startup_failure(client, tmp_path):
    for scope, operation, arguments, options in [("", "ping", {}, {}), ("s", "", {}, {}), ("s", "ping", [], {}),
                                                ("s", "ping", {}, {"timeout": 0}), ("s", "ping", {}, {"timeout": float("inf")})]:
        with pytest.raises(CoreError):
            await client.request(scope, operation, arguments, **options)
    with pytest.raises(CoreError):
        await client.ingest("s1", record(content="x" * 1048577), operation_id="oversized")
    absent = NativeMemoryClient(tmp_path / "db", binary=tmp_path / "missing")
    assert not await absent.start() and absent.diagnostic
    assert (await absent.query("s1"))["status"] == "unavailable"
    await absent.close()
    invalid_db = NativeMemoryClient(tmp_path / "no-directory" / "db", binary=BINARY)
    assert not await invalid_db.start()
    assert invalid_db.diagnostic
    await invalid_db.close()


@pytest.mark.parametrize("payload", [b'{"status":"ok"}', b"broken JSON"])
async def test_bad_peer_frames_fail_closed_without_deadlock(tmp_path, payload):
    # Supplemental real pipe fault fixture; all storage/normal IPC tests use the C++ binary.
    peer = tmp_path / "bad-peer"
    peer.write_text(f"#!{sys.executable}\nimport sys,struct\nh=sys.stdin.buffer.read(4)\n"
                    "sys.stdin.buffer.read(struct.unpack('>I',h)[0])\n"
                    f"payload={payload!r}\n"
                    "sys.stderr.write('diagnostic ' * 1000)\nsys.stderr.flush()\n"
                    "sys.stdout.buffer.write(struct.pack('>I',len(payload))+payload)\nsys.stdout.buffer.flush()\n")
    peer.chmod(0o700)
    client = NativeMemoryClient(tmp_path / "unused", binary=peer)
    assert not await client.start()
    assert len(client.diagnostic) <= 4096
    await client.close()


async def test_startup_deadline_and_closing_stalled_worker(tmp_path):
    peer = tmp_path / "stalled-peer"
    peer.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(60)\n")
    peer.chmod(0o700)
    client = NativeMemoryClient(tmp_path / "unused", binary=peer)
    result = await client.query("s1", timeout=0.02)
    assert result["error"]["code"] == "deadline" and not result["error"]["dispatched"]
    assert not client.available
    process = client.process
    await client.close()
    assert process.returncode is not None


async def test_event_replay_exact_sources_rejections_and_no_recursive_ingestion(client, journal):
    ingestor = MemoryIngestor(journal, client)
    one = journal.append("s1", "user.prompt", {"text": "Café compiler"}, source="channel_core")
    ignored = journal.append("s1", "memory.sleep_completed", {"cycle": "old"}, source="memory_sleep")
    two = journal.append("s1", "tool.result", {"state": "indeterminate", "effects": "unknown"}, source="tool_runner")
    oversized = journal.append("s1", "user.prompt", {"text": "x" * 33000}, source="channel_core")
    journal.append("other", "user.prompt", {"text": "private"}, source="channel_core")
    memory = ingestor.process_event(one)
    assert memory["content"] == "Journal event user.prompt from channel_core:\n" + canonical_json(one.payload)
    assert memory["sources"] == [{"id": one.id, "revision": 1, "start": 0, "end": len(canonical_json(one.payload).encode())}]
    assert ingestor.process_event(ignored) is None
    first = await ingestor.replay("s1", limit=2)
    assert first["ingested"] == 1 and first["skipped"] == 1 and first["has_more"]
    rest = await ingestor.replay("s1")
    assert rest["status"] == "partial" and rest["ingested"] == 1
    assert rest["rejected"][0]["event_id"] == oversized.id
    assert journal.get("memory.ingestion_errors", oversized.id, "s1") is not None
    # Consume the explicit rejection event; it cannot generate more memories.
    assert (await ingestor.replay("s1"))["skipped"] == 1
    assert len(result_ids(await client.query("s1"))) == 2
    assert "unknown" in (await client.request("s1", "get_revision", {"id": "event:" + two.id}))["data"]["record"]["content"]
    assert (await ingestor.replay("s1"))["ingested"] == 0
    assert journal.cursor("other", ingestor.consumer) == 0


async def test_ingestion_receipt_replay_after_cursor_loss(client, journal):
    ingestor = MemoryIngestor(journal, client)
    event = journal.append("s1", "awareness.observed", {"state": "running"}, source="awareness")
    # Native commit occurs before journal cursor: simulate recovery at that exact boundary.
    assert (await client.ingest("s1", ingestor.process_event(event), operation_id="journal:" + event.id))["status"] == "ok"
    replay = await ingestor.replay("s1")
    assert replay["watermark"] == 1 and replay["cursor"] == event.seq
    ingestor._acknowledge(event)  # Monotonic acknowledgement is safe across concurrent replay owners.
    assert (await client.request("s1", "ping"))["watermark"] == 1


async def test_native_event_rejection_is_durable_and_acknowledgment_idempotent(client, journal):
    ingestor = MemoryIngestor(journal, client)
    event = journal.append("s1", "user.prompt", {"text": "historical malformed source"}, source="invalid\0source")
    result = await ingestor.replay("s1")
    assert result["status"] == "partial" and result["rejected"][0]["event_id"] == event.id
    rejection = result["rejected"][0]["error"]
    ingestor._acknowledge(event, rejection)
    assert len(journal.list("memory.ingestion_errors", "s1")) == 1
    assert len(journal.events("s1", kinds=["memory.ingestion_rejected"])) == 1
    assert (await client.request("s1", "ping"))["watermark"] == 0


async def test_unavailable_ingestion_retains_cursor(client, journal):
    event = journal.append("s1", "user.prompt", {"text": "later"})
    ingestor = MemoryIngestor(journal, client)
    await client.close()
    response = await ingestor.replay("s1")
    assert response["status"] == "unavailable" and response["cursor"] == 0 and response["has_more"]
    replacement = NativeMemoryClient(client.db_path, binary=BINARY)
    try:
        replay = await MemoryIngestor(journal, replacement).replay("s1")
        assert replay["cursor"] == event.seq and replay["status"] == "ok"
    finally:
        await replacement.close()


def test_ingestor_configuration_and_invalid_source(client, journal):
    for kinds in ["*", {"*"}, {"memory.generated"}, {""}, {3}]:
        with pytest.raises(CoreError):
            MemoryIngestor(journal, client, allowed_kinds=kinds)
    ingestor = MemoryIngestor(journal, client)
    with pytest.raises(CoreError):
        ingestor.process_event({})
    own = journal.append("s1", "user.prompt", {"text": "generated"}, source="memory")
    assert ingestor.process_event(own) is None


async def test_sleep_default_consolidation_pruning_and_same_day_idempotency(client, journal):
    await seed(client)
    ingestor = MemoryIngestor(journal, client)
    sleep = MemorySleep(journal, client, ingestor, timezone="Europe/Berlin")
    timestamp = time.time()
    first = await sleep.run_cycle("s1", now=timestamp)
    assert first["status"] == "ok" and first["state"] == "complete"
    memories = (await client.query("s1"))["data"]["records"]
    observations = [m["record"] for m in memories if m["record"]["kind"] == "observation"]
    assert len(observations) == 1 and "Repeated recorded evidence (2 records)" in observations[0]["content"]
    assert (await sleep.run_cycle("s1", now=timestamp))["generation"] == first["generation"]
    await client.request("s1", "lease", {"operation_id": "pin", "generation": first["generation"], "lease_id": "first"})
    second = await sleep.run_cycle("s1", now=timestamp + 86400)
    assert second["status"] == "ok" and second["pending_prune"] == [first["generation"]]
    await client.request("s1", "release", {"lease_id": "first"})
    third = await sleep.run_cycle("s1", now=timestamp + 2 * 86400)
    assert third["status"] == "ok" and third["pending_prune"] == []
    with sqlite3.connect(client.db_path) as db:
        assert db.execute("SELECT state FROM generations WHERE generation=?", (first["generation"],)).fetchone()[0] == "retired"
        assert db.execute("SELECT count(*) FROM objects WHERE origin=0").fetchone()[0] == 2
    assert journal.get("memory.prune", "pending", "s1").value == {"generations": []}


async def test_sleep_admission_and_ingestion_are_bounded(client, journal):
    for i in range(3):
        journal.append("s1", "user.prompt", {"text": f"input-{i}"}, source="channel_core")
    admitted = False
    calls = []
    async def admission(scope, proposal):
        calls.append((scope, proposal))
        return admitted
    sleep = MemorySleep(journal, client, MemoryIngestor(journal, client), max_events=1, admission=admission)
    assert (await sleep.run_cycle("s1"))["state"] == "deferred"
    assert (await client.request("s1", "ping"))["watermark"] == 0
    admitted = True
    first = await sleep.run_cycle("s1")
    assert first["status"] == "partial" and first["state"] == "replaying_ingestion" and first["cursor"] == 1
    await sleep.run_cycle("s1")
    last = await sleep.run_cycle("s1")
    assert last["state"] == "complete" and last["cursor"] == 3
    assert all(p["priority"] == "background" for _, p in calls)
    with pytest.raises(CoreError):
        await sleep.ingestor.replay("s1", limit=0)


async def test_sleep_candidate_evidence_rejection_and_partial_truth(client, journal):
    await seed(client, 1)
    async def consolidate(scope, sources, cycle):
        good = record("learned", kind="procedure", sources=sources[0]["sources"])
        invented = record("invented", kind="belief", sources=[{"id": "missing", "revision": 1, "start": 0, "end": 1}])
        return [good, invented, good]
    sleep = MemorySleep(journal, client, MemoryIngestor(journal, client), consolidator=consolidate)
    result = await sleep.run_cycle("s1")
    assert result["state"] == "complete" and result["status"] == "partial" and len(result["rejected"]) == 2
    assert set(result_ids(await client.query("s1"))) == {"source-0", "learned"}


async def test_sleep_conflict_rebases_next_attempt_and_prunes_abandoned(client, journal):
    await seed(client, 1)
    race = True
    async def consolidate(scope, sources, cycle):
        nonlocal race
        if race:
            race = False
            begin = await client.request(scope, "begin_generation", {"operation_id": "rival-begin", "expected_parent": 0})
            await client.request(scope, "publish", {"operation_id": "rival-publish", "expected_parent": 0, "generation": begin["data"]["generation"]})
        return []
    sleep = MemorySleep(journal, client, MemoryIngestor(journal, client), consolidator=consolidate)
    first = await sleep.run_cycle("s1")
    assert first["status"] == "conflict"
    retry = await sleep.run_cycle("s1")
    assert retry["status"] == "ok" and retry["attempt"] == 2
    with sqlite3.connect(client.db_path) as db:
        assert db.execute("SELECT state FROM generations WHERE generation=?", (first["generation"],)).fetchone()[0] == "retired"


async def test_sleep_preserves_prune_work_added_during_another_cycle(client, journal):
    await seed(client, 1)
    ingestor = MemoryIngestor(journal, client)
    sleep = MemorySleep(journal, client, ingestor)
    timestamp = time.time()
    first = await sleep.run_cycle("s1", now=timestamp)
    abandoned = await client.request("s1", "begin_generation", {"operation_id": "abandoned",
                                      "expected_parent": first["generation"]})
    generation = abandoned["data"]["generation"]
    def consolidate(scope, sources, cycle):
        row = journal.get("memory.prune", "pending", scope)
        journal.put("memory.prune", "pending", scope, {"generations": [generation]}, row.revision)
        return []
    sleep.consolidator = consolidate
    second = await sleep.run_cycle("s1", now=timestamp + 86400)
    assert second["pending_prune"] == [generation]
    sleep.consolidator = lambda *args: []
    assert (await sleep.run_cycle("s1", now=timestamp + 172800))["pending_prune"] == []


async def test_sleep_lost_cycle_ownership_cannot_publish(client, journal):
    await seed(client, 1)
    def takeover(scope, sources, cycle):
        row = journal.get("memory.sleep", cycle["cycle_id"], scope)
        journal.put("memory.sleep", cycle["cycle_id"], scope,
                    {**row.value, "owner": "replacement-owner", "owner_until": time.time() + 100}, row.revision)
        return []
    sleep = MemorySleep(journal, client, MemoryIngestor(journal, client), consolidator=takeover)
    result = await sleep.run_cycle("s1")
    assert result["status"] == "conflict" and result["error"]["code"] == "memory_cycle_owner"
    assert (await client.request("s1", "ping"))["generation"] == 0
    assert journal.get("memory.sleep", result["cycle_id"], "s1").value["owner"] == "replacement-owner"


async def test_sleep_cancel_then_resume_and_parallel_foreground(client, journal):
    await seed(client, 1)
    entered, release = asyncio.Event(), asyncio.Event()
    async def consolidate(scope, sources, cycle):
        entered.set()
        await release.wait()
        return [record("learned", kind="belief", sources=sources[0]["sources"])]
    sleep = MemorySleep(journal, client, MemoryIngestor(journal, client), consolidator=consolidate)
    task = asyncio.create_task(sleep.run_cycle("s1"))
    await asyncio.wait_for(entered.wait(), 2)
    input_event = await asyncio.to_thread(journal.append, "s1", "user.steer", {"text": "new input"}, "channel_core")
    assert input_event.seq > 0
    assert (await client.query("s1"))["status"] == "ok"
    competing = MemorySleep(journal, client, MemoryIngestor(journal, client))
    assert (await competing.run_cycle("s1"))["state"] == "busy"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    resumed = await sleep.run_cycle("s1")
    assert resumed["status"] == "ok" and resumed["attempt"] == 1
    assert "learned" in result_ids(await client.query("s1"))


async def test_sleep_publication_receipt_recovers_lost_ack(client, journal, monkeypatch):
    await seed(client, 1)
    sleep = MemorySleep(journal, client, MemoryIngestor(journal, client))
    original = client.request
    lost = False
    async def request(scope, operation, arguments=None, **kwargs):
        nonlocal lost
        response = await original(scope, operation, arguments, **kwargs)
        if operation == "publish" and not lost:
            lost = True
            raise asyncio.CancelledError
        return response
    monkeypatch.setattr(client, "request", request)
    with pytest.raises(asyncio.CancelledError):
        await sleep.run_cycle("s1")
    monkeypatch.setattr(client, "request", original)
    recovered = MemorySleep(journal, client, MemoryIngestor(journal, client))
    result = await recovered.run_cycle("s1")
    assert result["status"] == "ok" and result["attempt"] == 1
    with sqlite3.connect(client.db_path) as db:
        assert db.execute("SELECT count(*) FROM generations WHERE state='published'").fetchone()[0] == 1


async def test_cycle_timeout_and_callback_failures_are_resumable(client, journal):
    await seed(client, 1)
    async def slow(*args):
        await asyncio.sleep(60)
    sleep = MemorySleep(journal, client, MemoryIngestor(journal, client), consolidator=slow, cycle_timeout=0.1)
    result = await sleep.run_cycle("s1")
    assert result["status"] == "unavailable" and result["error"]["code"] == "deadline"
    sleep.consolidator = lambda *args: {"wrong": "shape"}
    assert (await sleep.run_cycle("s1"))["status"] == "partial"
    def broken(*args):
        raise RuntimeError("consolidator fault")
    sleep.consolidator = broken
    assert (await sleep.run_cycle("s1"))["error"]["code"] == "memory_cycle_error"
    sleep.consolidator = lambda *args: []
    assert (await sleep.run_cycle("s1"))["status"] == "ok"


async def test_iana_day_boundary_and_invalid_sleep_configuration(client, journal):
    ingestor = MemoryIngestor(journal, client)
    sleep = MemorySleep(journal, client, ingestor, timezone="Europe/Berlin")
    before = datetime(2026, 9, 12, 21, 59, tzinfo=timezone.utc).timestamp()
    first = await sleep.run_cycle("s1", now=before)
    second = await sleep.run_cycle("s1", now=before + 120)
    assert first["cycle_id"] == "Europe/Berlin|2026-09-12"
    assert second["cycle_id"] == "Europe/Berlin|2026-09-13"
    for options in [{"timezone": "No/Such/Zone"}, {"max_events": 0}, {"max_candidates": 101}, {"cycle_timeout": 0}]:
        with pytest.raises(CoreError):
            MemorySleep(journal, client, ingestor, **options)
    with pytest.raises(CoreError):
        await sleep.run_cycle("s1", now=float("nan"))
    with pytest.raises(CoreError):
        await sleep.run_cycle("s1", now=1e30)


@pytest.mark.parametrize("changes", [
    {"kind": "fact"}, {"scope": "escape"}, {"valid_to": 99},
    {"id": "\0id"}, {"id": "é" * 256}, {"content": "é" * 20000},
    {"embedding": {"model": "e", "vector": [0, 0]}},
    {"sources": [{"id": "source-1", "revision": 1, "start": 21, "end": 22}]},
    {"sources": [{"id": "source-1", "revision": 1, "start": 10, "end": 10}]},
    {"content": "x" * 1048001},
])
def test_candidate_validation_boundaries(changes):
    candidate = record("derived", kind="belief")
    candidate.update(changes)
    with pytest.raises(CoreError):
        validate_candidate(candidate, record()["sources"])


def test_default_consolidation_respects_evidence_and_content_bounds():
    duplicate = record()
    giant = record("giant", "x" * 32768)
    values = consolidate_duplicates("s", [duplicate, duplicate, giant, giant], {"started_at": 200})
    assert len(values) == 1
    embedded = record("derived", kind="belief", embedding={"model": "e", "vector": [1, 0]})
    assert validate_candidate(embedded, record()["sources"])["embedding"]["model"] == "e"
