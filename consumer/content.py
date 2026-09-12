"""Fixed-scope content, workspace and evidence-backed native memory administration.

Uploads retain original bytes independently of bounded text extraction. Native
indexing status remains separate from durable document storage, including recovery.
The public workspace excludes the core's .sturgis profile and memory directory.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
from html.parser import HTMLParser
from pathlib import PurePosixPath
import re

from agent.protocol import CoreError, canonical_json, new_id
from agent.tool_runner.files import WorkspaceFs


MAX_UPLOAD = 4 * 1024 * 1024
MAX_TEXT = 256 * 1024
CHUNK_BYTES = 16000
TEXT_TYPES = {"text/plain", "text/markdown", "text/csv", "application/json", "text/html"}


def _bounded(value, name, limit, *, empty=False):
    if not isinstance(value, str) or "\0" in value or (not value and not empty) or len(value.encode()) > limit:
        raise CoreError("invalid_content", f"Invalid {name}")
    return value


def _count(value, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise CoreError("invalid_content", "Invalid content limit")
    return value


def _prefix(text, size):
    return text.encode()[:size].decode("utf-8", errors="ignore")


class _VisibleHTML(HTMLParser):
    """Bounded textual extraction, never rendering or executing active content."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = []; self.parts = []; self.size = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "head", "template"}:
            self.hidden.append(tag)
        if not self.hidden and tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3"}:
            self.handle_data("\n")

    def handle_endtag(self, tag):
        if tag in self.hidden:
            del self.hidden[self.hidden.index(tag):]
        elif tag in {"p", "div", "li", "tr"}:
            self.handle_data("\n")

    def handle_data(self, data):
        if self.hidden:
            return
        self.size += len(data.encode())
        if self.size > MAX_TEXT:
            raise CoreError("extraction_limit", "Extracted text exceeds the supported bound")
        self.parts.append(data)


def _extract(data, media_type):
    if media_type not in TEXT_TYPES:
        return {"status": "unsupported", "reason": "No decoder configured for this media type"}, None
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        return {"status": "unsupported", "reason": "Text must be valid UTF-8"}, None
    if "\0" in text:
        return {"status": "unsupported", "reason": "Text contains NUL bytes"}, None
    try:
        if media_type == "text/html":
            parser = _VisibleHTML(); parser.feed(text); parser.close()
            text = "".join(parser.parts).strip()
        if len(text.encode()) > MAX_TEXT:
            raise CoreError("extraction_limit", "Extracted text exceeds the supported bound")
    except CoreError:
        return {"status": "deferred", "reason": "Text exceeds the extraction byte limit"}, None
    if not text.strip():
        return {"status": "unsupported", "reason": "No extractable text"}, None
    return {"status": "ready", "bytes": len(text.encode()), "format": media_type}, text


class ConsumerData:
    """Every public method is async; scope and filesystem roots come only from core.

    Upload accepts bytes. Read methods return bounded base64 or labeled text so all
    responses cross JSON IPC without implicit decoding. Native status envelopes are
    preserved: a saved document is not reported as successfully indexed on timeout.
    """
    def __init__(self, core):
        self.core, self.scope, self.journal = core, core.scope, core.journal
        self.fs = WorkspaceFs(core.profiles.resources(self.scope)["workspace"], max_bytes=MAX_UPLOAD)
        self._mutations = asyncio.Lock()

    def _row(self, namespace, identity):
        _bounded(identity, "identifier", 256)
        row = self.journal.get("consumer." + namespace, identity, self.scope)
        if row is None:
            raise CoreError("not_found", "Content is unavailable in this agent")
        return row

    @staticmethod
    def _public(row):
        return {"id": row.id, "revision": row.revision, **row.value}

    def _operation(self, operation_id, request, create):
        _bounded(operation_id, "operation identifier", 256)
        digest = hashlib.sha256(canonical_json(request).encode()).hexdigest()
        with self.journal.transaction():
            prior = self.journal.get("consumer.content_operations", operation_id, self.scope)
            if prior:
                if prior.value["digest"] != digest:
                    raise CoreError("conflict", "Operation identifier was reused with different content")
                return prior.value["result"]
            result = create()
            self.journal.put("consumer.content_operations", operation_id, self.scope,
                             {"digest": digest, "result": result})
            return result

    def _source(self, kind, payload):
        event = self.journal.append(self.scope, kind, payload, source="consumer.content")
        return event, [{"id": event.id, "revision": 1, "start": 0,
                        "end": len(canonical_json(payload).encode())}]

    async def upload(self, name, media_type, data, *, operation_id):
        _bounded(name, "filename", 255)
        if name in {".", ".."} or any(c in name for c in "/\\\r\n"):
            raise CoreError("invalid_content", "Upload name must be a plain filename")
        if not isinstance(media_type, str) or not re.fullmatch(r"[a-z0-9][a-z0-9.+_-]{0,63}/[a-z0-9][a-z0-9.+_-]{0,63}", media_type):
            raise CoreError("invalid_content", "A canonical media type is required")
        if not isinstance(data, bytes) or len(data) > MAX_UPLOAD:
            raise CoreError("content_limit", "Upload must contain bounded bytes")
        def save():
            def create():
                artifact = self.core.artifacts.put(self.scope, data, media_type)
                extraction, text = _extract(data, media_type)
                identity = new_id("attachment")
                event, _ = self._source("content.uploaded", {"attachment_id": identity,
                    "artifact_id": artifact.id, "sha256": artifact.sha256, "name": name,
                    "media_type": media_type, "bytes": len(data)})
                row = self.journal.put("consumer.attachments", identity, self.scope,
                    {"name": name, "media_type": media_type, "bytes": len(data), "sha256": artifact.sha256,
                     "artifact_id": artifact.id, "source_event_id": event.id, "extraction": extraction})
                if text is not None:
                    self.journal.put("consumer.extracted", identity, self.scope, {"text": text})
                return self._public(row)
            return self._operation(operation_id, {"method": "upload", "name": name,
                "media_type": media_type, "sha256": hashlib.sha256(data).hexdigest()}, create)
        return await asyncio.to_thread(save)

    async def attachments(self, limit=100):
        _count(limit, 1000)
        def read():
            rows = self.journal.list("consumer.attachments", self.scope)
            return {"items": [self._public(row) for row in rows[:limit]], "truncated": len(rows) > limit}
        return await asyncio.to_thread(read)

    async def attachment(self, id):
        return await asyncio.to_thread(lambda: self._public(self._row("attachments", id)))

    async def read_attachment(self, id, *, offset=0, limit=65536):
        _count(limit, MAX_UPLOAD)
        if type(offset) is not int or offset < 0:
            raise CoreError("invalid_content", "Invalid attachment offset")
        def read():
            row = self._row("attachments", id)
            data = self.core.artifacts.get(self.scope, row.value["artifact_id"])
            if offset > len(data):
                raise CoreError("invalid_content", "Attachment offset exceeds its length")
            end = min(offset + limit, len(data))
            return {"id": id, "media_type": row.value["media_type"], "data_base64": base64.b64encode(data[offset:end]).decode(),
                    "start": offset, "end": end, "total_bytes": len(data), "complete": offset == 0 and end == len(data)}
        return await asyncio.to_thread(read)

    async def attachment_context(self, ids, *, max_bytes=16384):
        _count(max_bytes, MAX_TEXT)
        if not isinstance(ids, (list, tuple)) or len(ids) > 32 or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
            raise CoreError("invalid_content", "At most 32 distinct attachment identifiers are supported")
        def read():
            remaining, sources = max_bytes, []
            for identity in ids:
                row = self._row("attachments", identity)
                item = {"id": identity, "name": row.value["name"], "media_type": row.value["media_type"],
                        "origin": "uploaded_document", "authority": "untrusted_content",
                        "source_event_id": row.value["source_event_id"], **row.value["extraction"]}
                extracted = self.journal.get("consumer.extracted", identity, self.scope)
                if extracted is not None:
                    text = extracted.value["text"]; snippet = _prefix(text, remaining)
                    used = len(snippet.encode()); remaining -= used
                    item.update(text=snippet, start=0, end=used, total_bytes=len(text.encode()),
                                truncated=used < len(text.encode()), status="partial" if used < len(text.encode()) else "ready")
                sources.append(item)
            return {"sources": sources, "truncated": any(s.get("truncated", False) for s in sources)}
        return await asyncio.to_thread(read)

    @staticmethod
    def _path(path):
        _bounded(path, "workspace path", 4096)
        parsed = PurePosixPath(path)
        if parsed.is_absolute() or ".." in parsed.parts or ".sturgis" in parsed.parts or "\\" in path:
            raise CoreError("path_escape", "Workspace path is outside the public file area")
        return path

    async def files(self, path="."):
        entries = await asyncio.to_thread(self.fs.list, self._path(path))
        return {"path": path, "entries": [e for e in entries if e["name"] != ".sturgis"]}

    async def read_file(self, path):
        data = await asyncio.to_thread(self.fs.read, self._path(path))
        return {"path": path, "data_base64": base64.b64encode(data).decode(),
                "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    async def write_file(self, path, data, *, expected_sha256=None):
        path = self._path(path)
        def write():
            result = self.fs.write(path, data, expected_sha256=expected_sha256)
            self._source("content.file_written", result)
            return result
        return await asyncio.to_thread(write)

    async def delete_file(self, path, *, expected_sha256):
        path = self._path(path)
        def delete():
            result = self.fs.delete(path, expected_sha256=expected_sha256)
            self._source("content.file_deleted", result)
            return result
        return await asyncio.to_thread(delete)

    def _knowledge_prepare(self, attachment_id, title, operation_id, document_id, expected_revision):
        def create():
            attachment = self._row("attachments", attachment_id)
            extracted = self.journal.get("consumer.extracted", attachment_id, self.scope)
            if extracted is None:
                raise CoreError("unsupported_format", "Attachment has no supported text extraction", extraction=attachment.value["extraction"])
            identity = document_id or new_id("knowledge")
            prior = self.journal.get("consumer.knowledge", identity, self.scope)
            if (prior.revision if prior else 0) != expected_revision or prior and prior.value["deleted"]:
                raise CoreError("conflict", "Knowledge revision changed or was deleted")
            revision = expected_revision + 1
            records, remaining = [], extracted.value["text"]
            while remaining:
                chunk = _prefix(remaining, CHUNK_BYTES); remaining = remaining[len(chunk):]
                event, sources = self._source("content.knowledge_source", {"document_id": identity,
                    "document_revision": revision, "attachment_id": attachment_id, "title": title,
                    "chunk": len(records), "text": chunk, "origin": "uploaded_document"})
                records.append({"id": f"knowledge:{identity}:{revision}:{len(records)}", "kind": "observation",
                    "content": "Uploaded document (untrusted content):\n" + chunk, "sources": sources,
                    "source_time": event.created_at, "observed_at": event.created_at,
                    "valid_from": event.created_at, "valid_to": None, "concepts": ["uploaded_document", identity]})
            old_ids = [] if prior is None else prior.value["memory_ids"] + prior.value["retired_memory_ids"]
            value = {"title": title, "attachment_id": attachment_id, "text": extracted.value["text"],
                "origin": "uploaded_document", "deleted": False, "memory_ids": [r["id"] for r in records],
                "retired_memory_ids": old_ids, "source_event_ids": [r["sources"][0]["id"] for r in records]}
            row = self.journal.put("consumer.knowledge", identity, self.scope, value, expected_revision)
            self.journal.put("consumer.knowledge_revisions", f"{identity}:{revision}", self.scope,
                             {"document_id": identity, "document_revision": revision, **value})
            return {"document": self._public(row), "records": records, "retire": old_ids}
        return self._operation(operation_id, {"method": "knowledge_add", "attachment_id": attachment_id,
            "title": title, "document_id": document_id, "expected_revision": expected_revision}, create)

    async def _index(self, operation_id, prepared):
        result = None
        for record in prepared["records"]:
            result = await self.core.memory.ingest(self.scope, record,
                operation_id="content:" + hashlib.sha256((operation_id + ":" + record["id"]).encode()).hexdigest())
            if result["status"] != "ok":
                return {"document": prepared["document"], "indexing": result}
        for identity in prepared["retire"]:
            result = await self.core.memory.request(self.scope, "retract", {"operation_id":
                "content-retire:" + hashlib.sha256((operation_id + ":" + identity).encode()).hexdigest(),
                "id": identity, "reason": "Knowledge document replaced or deleted"})
            if result["status"] != "ok":
                return {"document": prepared["document"], "indexing": result}
        return {"document": prepared["document"], "indexing": result or {"status": "ok", "data": {"records": 0}}}

    async def knowledge_add(self, attachment_id, title, *, operation_id, document_id=None, expected_revision=0):
        _bounded(title, "document title", 512)
        if document_id is not None:
            _bounded(document_id, "document identifier", 128)
        if type(expected_revision) is not int or expected_revision < 0 or document_id is None and expected_revision != 0:
            raise CoreError("invalid_content", "Invalid document revision")
        async with self._mutations:
            prepared = await asyncio.to_thread(self._knowledge_prepare, attachment_id, title, operation_id, document_id, expected_revision)
            return await self._index(operation_id, prepared)

    async def knowledge_list(self, query="", limit=100):
        _bounded(query, "search query", 1024, empty=True); _count(limit, 1000)
        def read():
            rows = [row for row in self.journal.list("consumer.knowledge", self.scope)
                    if not row.value["deleted"] and query.casefold() in (row.value["title"] + "\n" + row.value["text"]).casefold()]
            return {"items": [{k: v for k, v in self._public(row).items() if k != "text"} for row in rows[:limit]],
                    "truncated": len(rows) > limit}
        return await asyncio.to_thread(read)

    async def knowledge_get(self, id, revision=None):
        _bounded(id, "document identifier", 128)
        if revision is not None and (type(revision) is not int or revision < 1):
            raise CoreError("invalid_content", "Invalid document revision")
        def read():
            row = self._row("knowledge", id)
            if revision is not None:
                historical = self._row("knowledge_revisions", f"{id}:{revision}")
                return {"id": id, "revision": revision, **historical.value, "currently_deleted": row.value["deleted"]}
            return self._public(row)
        return await asyncio.to_thread(read)

    async def knowledge_delete(self, id, *, expected_revision, operation_id):
        def prepare():
            def create():
                row = self._row("knowledge", id)
                if row.value["deleted"]:
                    raise CoreError("conflict", "Knowledge document is already deleted")
                updated = self.journal.put("consumer.knowledge", id, self.scope,
                    {**row.value, "deleted": True}, expected_revision)
                self._source("content.knowledge_deleted", {"document_id": id, "revision": updated.revision})
                return {"document": self._public(updated), "records": [],
                        "retire": row.value["memory_ids"] + row.value["retired_memory_ids"]}
            return self._operation(operation_id, {"method": "knowledge_delete", "id": id,
                "expected_revision": expected_revision}, create)
        async with self._mutations:
            prepared = await asyncio.to_thread(prepare)
            return await self._index(operation_id, prepared)

    async def memory_query(self, *, text="", concepts=None, embedding=None, at=None, limit=20):
        return await self.core.memory.query(self.scope, text=text, concepts=concepts, embedding=embedding, at=at, limit=limit)

    async def memory_get(self, id, revision=None):
        return await self.core.memory.request(self.scope, "get_revision", {"id": id, **({"revision": revision} if revision is not None else {})})

    async def memory_correct(self, id, content, *, expected_revision, operation_id):
        _bounded(content, "memory correction", 32000)
        if type(expected_revision) is not int or expected_revision < 1:
            raise CoreError("invalid_content", "Correction requires a positive revision")
        _bounded(id, "memory identifier", 256); _bounded(operation_id, "operation identifier", 256)
        async with self._mutations:
            request = {"method": "memory_correct", "id": id, "content": content, "expected_revision": expected_revision}
            prior = await asyncio.to_thread(self.journal.get, "consumer.content_operations", operation_id, self.scope)
            if prior is None:
                current = await self.memory_get(id, expected_revision)
                if current["status"] != "ok":
                    return current
                if current["data"]["record"]["retracted"]:
                    raise CoreError("conflict", "Retracted memory cannot be corrected in place")
            def prepare():
                def create():
                    event, sources = self._source("content.memory_correction", request)
                    return {"id": id, "kind": "belief", "content": "User correction:\n" + content,
                        "sources": sources, "source_time": event.created_at, "observed_at": event.created_at,
                        "valid_from": event.created_at, "valid_to": None, "concepts": ["user_correction"]}
                return self._operation(operation_id, request, create)
            record = await asyncio.to_thread(prepare)
            return await self.core.memory.ingest(self.scope, record, expected_revision=expected_revision,
                operation_id="content-correct:" + hashlib.sha256(operation_id.encode()).hexdigest())

    async def memory_retract(self, id, reason, *, revision=None, operation_id):
        _bounded(id, "memory identifier", 256); _bounded(reason, "retraction reason", 1024)
        def prepare():
            request = {"method": "memory_retract", "id": id, "revision": revision, "reason": reason}
            def create():
                event, _ = self._source("content.memory_retraction", request)
                return {"source_event_id": event.id}
            return self._operation(operation_id, request, create)
        evidence = await asyncio.to_thread(prepare)
        result = await self.core.memory.request(self.scope, "retract", {"operation_id":
            "content-retract:" + hashlib.sha256(operation_id.encode()).hexdigest(), "id": id,
            "reason": reason, **({"revision": revision} if revision is not None else {})})
        return {**result, "source_event_id": evidence["source_event_id"]}
