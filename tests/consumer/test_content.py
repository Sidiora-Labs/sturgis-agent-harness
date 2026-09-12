"""Consumer content journeys through real filesystem, SQLite and compiled native memory."""
import asyncio
import base64
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.protocol import CoreError, canonical_json
from agent.runtime import CoreRuntime
from consumer.content import ConsumerData, MAX_TEXT, MAX_UPLOAD


@pytest.fixture
async def content(tmp_path):
    workspace = tmp_path / "workspace"; workspace.mkdir()
    core = CoreRuntime(tmp_path / "state", scope="owner", workspace=workspace,
                       memory_binary=Path(".build/native/sturgis-memory").resolve())
    service = ConsumerData(core)
    yield service
    await core.close()


async def uploaded(content, text="A sourced knowledge document", *, op="upload", media="text/plain"):
    return await content.upload("notes.txt", media, text.encode(), operation_id=op)


def memory_ids(response):
    return [r["record"]["id"] for r in response["data"]["records"]]


async def test_upload_is_durable_typed_idempotent_and_integrity_checked(content):
    data = b"original bytes\n"
    first = await content.upload("report.txt", "text/plain", data, operation_id="upload-1")
    assert await content.upload("report.txt", "text/plain", data, operation_id="upload-1") == first
    with pytest.raises(CoreError, match="reused"):
        await content.upload("report.txt", "text/plain", b"different", operation_id="upload-1")
    duplicate = await content.upload("copy.txt", "text/plain", data, operation_id="upload-2")
    assert first["artifact_id"] == duplicate["artifact_id"] and first["id"] != duplicate["id"]
    assert (await content.attachment(first["id"])) == first
    listing = await content.attachments(limit=1)
    assert len(listing["items"]) == 1 and listing["truncated"]
    full = await content.read_attachment(first["id"])
    assert base64.b64decode(full["data_base64"]) == data and full["complete"]
    part = await content.read_attachment(first["id"], offset=2, limit=4)
    assert base64.b64decode(part["data_base64"]) == data[2:6] and not part["complete"]
    event = content.journal.event(content.scope, first["source_event_id"])
    assert event.source == "consumer.content" and event.payload["sha256"] == hashlib.sha256(data).hexdigest()
    artifact_root = content.core.artifacts.root / hashlib.sha256(content.scope.encode()).hexdigest()
    (artifact_root / first["artifact_id"]).write_bytes(b"tampered")
    with pytest.raises(CoreError, match="changed"):
        await content.read_attachment(first["id"])


@pytest.mark.parametrize("media,data,status", [
    ("application/pdf", b"%PDF actual bytes retained", "unsupported"),
    ("image/png", b"\x89PNG", "unsupported"),
    ("text/plain", b"\xff", "unsupported"),
    ("text/plain", b"a\0b", "unsupported"),
    ("text/plain", b"  \n", "unsupported"),
    ("text/plain", b"x" * (MAX_TEXT + 1), "deferred"),
    ("text/html", b"<p>" + b"x" * (MAX_TEXT + 1) + b"</p>", "deferred"),
])
async def test_unsupported_and_oversized_extraction_retains_original(content, media, data, status):
    attachment = await content.upload("input.bin", media, data, operation_id="opaque")
    assert attachment["extraction"]["status"] == status
    actual = await content.read_attachment(attachment["id"], limit=MAX_UPLOAD)
    assert base64.b64decode(actual["data_base64"]) == data
    view = (await content.attachment_context([attachment["id"]]))["sources"][0]
    assert view["status"] == status and "text" not in view
    with pytest.raises(CoreError) as error:
        await content.knowledge_add(attachment["id"], "unsupported", operation_id="knowledge")
    assert error.value.code == "unsupported_format"


async def test_html_excludes_active_content_and_attachment_budget_is_utf8_safe(content):
    html = "<head><title>hidden title</title><style>hidden style</style></head><p>Visible &amp; café</p><script>steal()</script><template>hidden template</template><div>Second<br>line</div>"
    first = await uploaded(content, html, media="text/html")
    second = await uploaded(content, "é" * 20, op="second")
    complete = await content.attachment_context([first["id"]])
    text = complete["sources"][0]["text"]
    assert "Visible & café" in text and "Second\nline" in text
    assert "hidden" not in text and "steal" not in text
    view = await content.attachment_context([second["id"], first["id"]], max_bytes=5)
    assert sum(len(s["text"].encode()) for s in view["sources"]) <= 5
    assert view["truncated"] and view["sources"][0]["text"] == "éé"
    assert all(s["origin"] == "uploaded_document" and s["authority"] == "untrusted_content" for s in view["sources"])
    assert (await content.attachment_context([]))["sources"] == []


async def test_fixed_scope_denies_foreign_content_even_with_shared_stores(content, tmp_path):
    attachment = await uploaded(content)
    knowledge = await content.knowledge_add(attachment["id"], "owner private", operation_id="knowledge")
    other_workspace = tmp_path / "other"; other_workspace.mkdir()
    content.core.profiles.register("other", other_workspace)
    other = ConsumerData(SimpleNamespace(scope="other", journal=content.journal,
        profiles=content.core.profiles, artifacts=content.core.artifacts, memory=content.core.memory))
    for operation in (other.attachment(attachment["id"]), other.read_attachment(attachment["id"]),
                      other.attachment_context([attachment["id"]]), other.knowledge_get(knowledge["document"]["id"])):
        with pytest.raises(CoreError) as error:
            await operation
        assert error.value.code == "not_found"
    assert (await other.attachments())["items"] == []
    assert memory_ids(await other.memory_query()) == []
    assert (await other.knowledge_list())["items"] == []


async def test_workspace_conditional_effects_protected_state_and_symlinks(content, tmp_path):
    result = await content.write_file("notes/todo.txt", b"first")
    assert result["before_sha256"] is None
    read = await content.read_file("notes/todo.txt")
    assert base64.b64decode(read["data_base64"]) == b"first"
    with pytest.raises(CoreError) as error:
        await content.write_file("notes/todo.txt", b"lost update", expected_sha256="stale")
    assert error.value.code == "precondition"
    changed = await content.write_file("notes/todo.txt", b"second", expected_sha256=read["sha256"])
    assert (await content.files("notes"))["entries"][0]["name"] == "todo.txt"
    assert all(e["name"] != ".sturgis" for e in (await content.files())["entries"])
    with pytest.raises(CoreError):
        await content.delete_file("notes/todo.txt", expected_sha256=read["sha256"])
    await content.delete_file("notes/todo.txt", expected_sha256=changed["after_sha256"])
    assert (await content.files("notes"))["entries"] == []
    outside = tmp_path / "outside"; outside.write_text("private")
    (content.fs.root / "link").symlink_to(outside)
    with pytest.raises(CoreError):
        await content.read_file("link")
    with pytest.raises(CoreError):
        await content.write_file("link", b"corrupt")
    assert outside.read_text() == "private"
    assert len(content.journal.events(content.scope, kinds=["content.file_written"])) == 2
    assert len(content.journal.events(content.scope, kinds=["content.file_deleted"])) == 1


@pytest.mark.parametrize("path", [".sturgis", "notes/.sturgis/secret", "../outside", "/etc/passwd", "a\\b", "bad\0path"])
async def test_reserved_or_escaping_paths_are_denied_for_reads_and_writes(content, path):
    for operation in (content.files(path), content.read_file(path), content.write_file(path, b"x"),
                      content.delete_file(path, expected_sha256=None)):
        with pytest.raises(CoreError):
            await operation


async def test_knowledge_chunks_exact_evidence_search_revisions_and_retraction(content):
    original = 'A café "source" with exact attribution. ' * 700
    attachment = await uploaded(content, original)
    first = await content.knowledge_add(attachment["id"], "Café manual", operation_id="add")
    doc = first["document"]
    assert first["indexing"]["status"] == "ok" and len(doc["memory_ids"]) > 1
    retried = await content.knowledge_add(attachment["id"], "Café manual", operation_id="add")
    assert retried["document"] == doc
    assert (await content.knowledge_list("CAFÉ"))["items"][0]["id"] == doc["id"]
    assert (await content.knowledge_list("missing"))["items"] == []
    assert (await content.knowledge_get(doc["id"]))["text"] == original
    response = await content.memory_query(text="café", concepts=["uploaded_document"], limit=100)
    assert set(memory_ids(response)) == set(doc["memory_ids"])
    for match in response["data"]["records"]:
        record = match["record"]; reference = record["sources"][0]
        event = content.journal.event(content.scope, reference["id"])
        encoded = canonical_json(event.payload).encode()
        assert reference["start"] == 0 and reference["end"] == len(encoded)
        assert record["content"].endswith(event.payload["text"])
        assert record["kind"] == "observation" and event.payload["origin"] == "uploaded_document"
    newer = await uploaded(content, "The corrected manual", op="newer")
    changed = await content.knowledge_add(newer["id"], "Café manual revised", operation_id="revise",
        document_id=doc["id"], expected_revision=1)
    assert changed["document"]["revision"] == 2
    assert (await content.knowledge_get(doc["id"], 1))["text"] == original
    assert set(memory_ids(await content.memory_query())) == set(changed["document"]["memory_ids"])
    for old_id in doc["memory_ids"]:
        assert (await content.memory_get(old_id))["data"]["record"]["retracted"]
    with pytest.raises(CoreError, match="revision"):
        await content.knowledge_add(newer["id"], "conflict", operation_id="stale", document_id=doc["id"], expected_revision=1)
    deleted = await content.knowledge_delete(doc["id"], expected_revision=2, operation_id="delete")
    assert deleted["document"]["deleted"] and deleted["indexing"]["status"] == "ok"
    assert (await content.knowledge_delete(doc["id"], expected_revision=2, operation_id="delete"))["document"] == deleted["document"]
    assert (await content.knowledge_list())["items"] == [] and memory_ids(await content.memory_query()) == []
    assert (await content.knowledge_get(doc["id"], 1))["currently_deleted"]
    with pytest.raises(CoreError, match="deleted"):
        await content.knowledge_delete(doc["id"], expected_revision=3, operation_id="delete-again")


async def test_user_memory_correction_preserves_history_and_actual_retraction(content):
    attachment = await uploaded(content, "The initial observation")
    doc = (await content.knowledge_add(attachment["id"], "Observation", operation_id="add"))["document"]
    identity = doc["memory_ids"][0]
    correction = await content.memory_correct(identity, "The user disputes that observation", expected_revision=1, operation_id="correct")
    assert correction["status"] == "ok"
    record = correction["data"]["record"]
    assert record["revision"] == 2 and record["kind"] == "belief"
    assert record["content"].startswith("User correction:\n")
    event = content.journal.event(content.scope, record["sources"][0]["id"])
    assert event.payload["content"] == "The user disputes that observation"
    retry = await content.memory_correct(identity, event.payload["content"], expected_revision=1, operation_id="correct")
    assert retry["data"] == correction["data"]
    assert (await content.memory_get(identity, 1))["data"]["record"]["content"].endswith("The initial observation")
    stale = await content.memory_correct(identity, "stale update", expected_revision=1, operation_id="stale-correction")
    assert stale["status"] == "conflict"
    missing = await content.memory_correct("absent", "new", expected_revision=1, operation_id="missing")
    assert missing["status"] == "invalid_request"
    with pytest.raises(CoreError, match="reused"):
        await content.memory_correct(identity, "changed retry", expected_revision=1, operation_id="correct")
    retracted = await content.memory_retract(identity, "Owner retracted", operation_id="retract")
    assert retracted["status"] == "ok" and retracted["source_event_id"]
    assert (await content.memory_retract(identity, "Owner retracted", operation_id="retract"))["data"] == retracted["data"]
    assert memory_ids(await content.memory_query()) == []
    with pytest.raises(CoreError, match="Retracted"):
        await content.memory_correct(identity, "cannot revive", expected_revision=2, operation_id="revive")


async def test_unavailable_indexing_is_truthful_and_resumes_from_durable_sources(content, tmp_path):
    attachment = await uploaded(content)
    binary = content.core.memory.binary
    content.core.memory.binary = str(tmp_path / "missing-worker")
    first = await content.knowledge_add(attachment["id"], "Pending", operation_id="add")
    assert first["indexing"]["status"] == "unavailable"
    assert len(content.journal.events(content.scope, kinds=["content.knowledge_source"])) == 1
    content.core.memory.binary = binary
    resumed = await content.knowledge_add(attachment["id"], "Pending", operation_id="add")
    assert resumed["indexing"]["status"] == "ok" and resumed["document"] == first["document"]
    await content.core.memory._stop_process()
    content.core.memory.binary = str(tmp_path / "missing-worker")
    deleted = await content.knowledge_delete(first["document"]["id"], expected_revision=1, operation_id="delete")
    assert deleted["indexing"]["status"] == "unavailable" and deleted["document"]["deleted"]
    content.core.memory.binary = binary
    assert (await content.knowledge_delete(first["document"]["id"], expected_revision=1, operation_id="delete"))["indexing"]["status"] == "ok"
    assert memory_ids(await content.memory_query()) == []


@pytest.mark.parametrize("kwargs", [
    {"name": "../x"}, {"name": "."}, {"name": "x\ny"}, {"name": ""},
    {"name": "x" * 256}, {"media_type": "TEXT/PLAIN"}, {"media_type": None},
    {"data": "not bytes"}, {"data": b"x" * (MAX_UPLOAD + 1)}, {"operation_id": ""},
])
async def test_upload_validation(content, kwargs):
    args = {"name": "x", "media_type": "text/plain", "data": b"safe", "operation_id": "id"}
    args.update(kwargs)
    with pytest.raises(CoreError):
        await content.upload(**args)
    assert (await content.attachments())["items"] == []


async def test_range_revision_and_context_validation(content):
    attachment = await uploaded(content)
    calls = [content.attachments(limit=True), content.attachment("missing"),
        content.read_attachment(attachment["id"], offset=-1), content.read_attachment(attachment["id"], offset=999),
        content.read_attachment(attachment["id"], limit=0), content.attachment_context([attachment["id"]] * 2),
        content.attachment_context([None]), content.attachment_context([], max_bytes=0),
        content.knowledge_add(attachment["id"], "title", operation_id="bad", expected_revision=True),
        content.knowledge_add(attachment["id"], "title", operation_id="bad", expected_revision=1),
        content.knowledge_get("missing", 0), content.knowledge_get("missing"),
        content.memory_correct("any", "text", expected_revision=0, operation_id="bad"),
        content.memory_retract("any", "", operation_id="bad")]
    for call in calls:
        with pytest.raises(CoreError):
            await call


async def test_independent_content_calls_remain_responsive_while_native_is_stopped(content):
    import os
    import signal
    attachment = await uploaded(content)
    assert await content.core.memory.start()
    process = content.core.memory.process
    os.kill(process.pid, signal.SIGSTOP)
    try:
        pending = asyncio.create_task(content.knowledge_add(attachment["id"], "Pending", operation_id="add"))
        for _ in range(100):
            if content.journal.list("consumer.knowledge", content.scope): break
            await asyncio.sleep(.01)
        assert not pending.done()
        assert (await asyncio.wait_for(content.attachment(attachment["id"]), .5))["id"] == attachment["id"]
        assert await asyncio.wait_for(content.files(), .5)
        os.kill(process.pid, signal.SIGCONT)
        assert (await pending)["indexing"]["status"] == "ok"
    finally:
        if process.returncode is None: os.kill(process.pid, signal.SIGCONT)
