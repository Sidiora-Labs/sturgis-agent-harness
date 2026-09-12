"""Bounded logical backups: content survives; live execution authority does not."""
from __future__ import annotations

import asyncio
import base64
import binascii
from contextlib import closing
import hashlib
import io
import math
import os
from pathlib import Path, PurePosixPath
import sqlite3
import stat
import time
import zipfile

from agent.artifacts import ArtifactStore
from agent.channel_core import ChannelCore
from agent.memory import NativeMemoryClient
from agent.profile import ProfileRegistry
from agent.protocol import CoreError, canonical_json, new_id, strict_loads
from agent.skills import SkillRegistry
from agent.state import Journal
from consumer.content import _extract
from consumer.contracts import identifier, object_body
from consumer.identity import AgentDirectory
from consumer.lifecycle import CONTROL, RUNTIMES


FORMAT = "sturgis-backup/1"
RESTORES = "consumer.restore_requests"
MAX_ARCHIVE = 4 * 1024 * 1024
MAX_FILE = 8 * 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024
MAX_FILES = 512
MAX_ROWS = 10000
RELOCATION = {"scope": "fresh_agent", "workspace": "server_assigned", "profile_resources": "regenerated"}
RECONNECT = ["credentials", "providers", "connections", "devices", "permissions", "plugins", "skills",
             "schedules", "workflows", "children", "execution_grants"]


def _require(condition, message, code="invalid_backup"):
    if not condition: raise CoreError(code, message)


def _text(value, maximum=4096, *, empty=False):
    _require(isinstance(value, str) and (empty or bool(value)) and "\0" not in value and len(value.encode()) <= maximum,
             "Backup text does not match its bounded schema")
    return value


def _integer(value, minimum=0, maximum=2**63 - 1):
    _require(type(value) is int and minimum <= value <= maximum, "Backup integer is outside its schema")
    return value


async def _settled(operation):
    """Finish an in-flight local write before cancellation releases its fence."""
    task = asyncio.create_task(operation)
    try: return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


def _path(value):
    _text(value, 1024)
    path = PurePosixPath(value)
    _require(not path.is_absolute() and not any(p in {".", "..", ".sturgis"} for p in path.parts)
             and "\\" not in value and str(path) == value and not value.endswith("/"), "Unsafe archive path")
    return path


def _read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        _require(stat.S_ISREG(metadata.st_mode) and metadata.st_size <= MAX_FILE, "File is not bounded regular content", "backup_limit")
        data = stream.read(MAX_FILE + 1)
    _require(len(data) <= MAX_FILE, "File grew beyond the backup bound", "backup_limit")
    return data


def _walk(root):
    if not root.exists(): return
    pending = [root]
    visited = 0
    while pending:
        directory = pending.pop()
        for path in directory.iterdir():
            if path.name == ".sturgis": continue
            visited += 1
            _require(visited <= MAX_FILES * 2, "Filesystem exceeds the backup entry bound", "backup_limit")
            kind = path.lstat().st_mode
            _require(not stat.S_ISLNK(kind), "Backup refuses symlinks", "unsafe_backup_path")
            if stat.S_ISDIR(kind): pending.append(path)
            else:
                _require(stat.S_ISREG(kind), "Backup accepts only regular files", "unsafe_backup_path")
                yield path.relative_to(root).as_posix(), _read(path)


def _rows(db, sql, parameters=()):
    values, size = [], 0
    for row in db.execute(sql, parameters):
        size += sum(len(value.encode()) if isinstance(value, str) else 8 for value in row)
        _require(len(values) < MAX_ROWS and size <= MAX_FILE, "Database exceeds the logical record bound", "backup_limit")
        values.append(row)
    return values


def _database(path):
    _require(path.resolve() == path and path.is_file(), "Backup database path is not canonical", "unsafe_backup_path")
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def _native_snapshot(path, scope):
    if not path.exists(): return {"canonical": [], "derived": [], "retractions": [], "history": {}}
    with closing(_database(path)) as db:
        _require(db.execute("PRAGMA user_version").fetchone()[0] == 1, "Unsupported native database version")
        objects = _rows(db, "SELECT uid,payload,origin FROM objects WHERE scope=? ORDER BY watermark,revision,uid", (scope,))
        current = db.execute("SELECT current_gen FROM scopes WHERE scope=?", (scope,)).fetchone()
        members = _rows(db, "SELECT uid FROM members WHERE scope=? AND generation=?", (scope, current[0] if current else 0))
        active = {r["uid"] for r in members}
        return {"canonical": [strict_loads(r["payload"]) for r in objects if r["origin"] == 0],
            "derived": [strict_loads(r["payload"]) for r in objects if r["origin"] > 0 and r["uid"] in active],
            "retractions": [dict(r) for r in _rows(db, "SELECT id,revision,reason FROM retractions WHERE scope=?", (scope,))],
            "history": {"objects": [dict(r) for r in objects],
                "generations": [dict(r) for r in _rows(db, "SELECT * FROM generations WHERE scope=?", (scope,))],
                "members": [dict(r) for r in _rows(db, "SELECT * FROM members WHERE scope=?", (scope,))]}}


def _export(config, name):
    entries = {}
    def add(path, data):
        _require(len(entries) < MAX_FILES - 1 and len(data) <= MAX_FILE
            and sum(map(len, entries.values())) + len(data) <= MAX_TOTAL - 65536,
            "Backup exceeds its expanded size bound", "backup_limit")
        entries[path] = data
    artifacts = []
    journal = {"records": [], "events": []}
    db_path = config.root / "operations.sqlite"
    if db_path.exists():
        with closing(_database(db_path)) as db:
            journal["records"] = [{**dict(row), "value": strict_loads(row["body"])} for row in
                _rows(db, "SELECT namespace,id,scope,revision,body FROM records WHERE scope=? ORDER BY namespace,id", (config.scope,))]
            for row in journal["records"]: row.pop("body")
            journal["events"] = [{**dict(row), "payload": strict_loads(row["payload"])} for row in
                _rows(db, "SELECT seq,id,scope,kind,source,payload,created FROM events WHERE scope=? ORDER BY seq", (config.scope,))]
    partition = hashlib.sha256(config.scope.encode()).hexdigest()
    memory_root = config.workspace / ".sturgis/profiles" / partition / "memory"
    profiles = [row for row in journal["records"] if row["namespace"] == "profiles" and row["id"] == config.scope]
    if profiles:
        profile = profiles[0]["value"]
        memory_root = Path(profile["resource_roots"]["memory"])
        _require(profile["workspace_root"] == str(config.workspace) and memory_root.is_relative_to(config.workspace)
            and memory_root.resolve() == memory_root, "Profile resources escaped the snapshot boundary", "unsafe_backup_path")
    memory = _native_snapshot(memory_root / "memory.sqlite", config.scope)
    add("journal.json", canonical_json(journal).encode())
    add("memory.json", canonical_json(memory).encode())
    for relative, data in _walk(config.workspace): add("workspace/" + relative, data)
    directory = config.root / "artifacts" / partition
    store = ArtifactStore(config.root / "artifacts")
    for relative, data in _walk(directory):
        if relative.endswith(".json"): continue
        _require("/" not in relative, "Unexpected artifact directory")
        meta = strict_loads(_read(directory / (relative + ".json")))
        _require(store.get(config.scope, relative) == data, "Artifact integrity check failed")
        object_body(meta, {"id", "scope", "size", "sha256", "media_type"}, {"id", "scope", "size", "sha256", "media_type"})
        artifacts.append({"id": relative, "media_type": meta["media_type"]})
        add("artifacts/" + relative, data)
    manifest = {"format": FORMAT, "name": name, "source_scope": config.scope, "created_at": time.time(),
        "relocation": RELOCATION, "reconnect": RECONNECT, "artifacts": artifacts,
        "files": {path: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()} for path, data in entries.items()}}
    entries["manifest.json"] = canonical_json(manifest).encode()
    _require(len(entries) <= MAX_FILES and all(len(data) <= MAX_FILE for data in entries.values())
             and sum(map(len, entries.values())) <= MAX_TOTAL, "Backup exceeds its expanded size bound", "backup_limit")
    with io.BytesIO() as output:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path, data in sorted(entries.items()):
                _path(path)
                archive.writestr(path, data)
        data = output.getvalue()
    _require(len(data) <= MAX_ARCHIVE, "Backup exceeds its compressed size bound", "backup_limit")
    return {"format": FORMAT, "archive_base64": base64.b64encode(data).decode(),
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def _unpack(encoded, digest):
    _require(isinstance(encoded, str) and len(encoded) <= 4 * ((MAX_ARCHIVE + 2) // 3), "Archive exceeds its transport bound", "backup_limit")
    _require(isinstance(digest, str) and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest), "Invalid archive digest")
    try: raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error): raise CoreError("invalid_backup", "Archive is not canonical base64") from None
    _require(len(raw) <= MAX_ARCHIVE and hashlib.sha256(raw).hexdigest() == digest, "Archive integrity check failed")
    entries = {}
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            _require(1 <= len(infos) <= MAX_FILES and sum(i.file_size for i in infos) <= MAX_TOTAL, "Archive exceeds its expanded bound", "backup_limit")
            for info in infos:
                _path(info.filename)
                mode = info.external_attr >> 16
                _require(not info.is_dir() and (stat.S_IFMT(mode) in {0, stat.S_IFREG}) and not info.flag_bits & 1
                    and info.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}, "Unsupported archive entry")
                _require(info.filename not in entries and info.file_size <= MAX_FILE, "Duplicate or oversized archive entry")
                with archive.open(info) as stream: data = stream.read(MAX_FILE + 1)
                _require(len(data) == info.file_size, "Archive entry size mismatch")
                entries[info.filename] = data
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, EOFError):
        raise CoreError("invalid_backup", "Archive structure or compressed content is invalid") from None
    _require("manifest.json" in entries, "Archive manifest is missing")
    manifest = strict_loads(entries.pop("manifest.json"))
    fields = {"format", "name", "source_scope", "created_at", "relocation", "reconnect", "artifacts", "files"}
    object_body(manifest, fields, fields)
    _require(manifest["format"] == FORMAT and manifest["relocation"] == RELOCATION and manifest["reconnect"] == RECONNECT,
             "Unsupported backup or relocation contract")
    identifier(manifest["source_scope"]); _text(manifest["name"], 120)
    _require(type(manifest["created_at"]) in {int, float} and math.isfinite(manifest["created_at"]) and manifest["created_at"] >= 0,
             "Invalid backup timestamp")
    _require(isinstance(manifest["files"], dict) and set(entries) == set(manifest["files"])
             and {"journal.json", "memory.json"} <= set(entries), "Manifest does not match archive entries")
    for path, data in entries.items():
        expected = manifest["files"][path]
        object_body(expected, {"bytes", "sha256"}, {"bytes", "sha256"})
        _require(type(expected["bytes"]) is int and expected["bytes"] == len(data)
                 and expected["sha256"] == hashlib.sha256(data).hexdigest(), "Manifest content hash mismatch")
        _require(path in {"journal.json", "memory.json"} or path.startswith(("workspace/", "artifacts/")), "Unknown archive path")
    journal, memory = strict_loads(entries["journal.json"]), strict_loads(entries["memory.json"])
    object_body(journal, {"records", "events"}, {"records", "events"})
    object_body(memory, {"canonical", "derived", "retractions", "history"}, {"canonical", "derived", "retractions", "history"})
    for values in (journal["records"], journal["events"], memory["canonical"], memory["derived"], memory["retractions"], manifest["artifacts"]):
        _require(isinstance(values, list) and len(values) <= MAX_ROWS, "Invalid logical snapshot array")
    _require(isinstance(memory["history"], dict), "Native history must be an object")
    seen = set()
    for row in journal["records"]:
        object_body(row, {"namespace", "id", "scope", "revision", "value"}, {"namespace", "id", "scope", "revision", "value"})
        _text(row["namespace"], 256); _text(row["id"], 1024); _integer(row["revision"], 1)
        _require(row["scope"] == manifest["source_scope"] and isinstance(row["value"], dict), "Record scope or body mismatch")
        identity = (row["namespace"], row["id"])
        _require(identity not in seen, "Duplicate logical record"); seen.add(identity)
    seen = set()
    for event in journal["events"]:
        fields = {"seq", "id", "scope", "kind", "source", "payload", "created"}
        object_body(event, fields, fields)
        identifier(event["id"]); _integer(event["seq"], 1); _text(event["kind"], 256); _text(event["source"], 256)
        _require(event["scope"] == manifest["source_scope"] and isinstance(event["payload"], dict)
                 and type(event["created"]) in {int, float} and math.isfinite(event["created"]) and event["created"] >= 0,
                 "Invalid historical event")
        _require(event["id"] not in seen, "Duplicate historical event"); seen.add(event["id"])
    return manifest, entries, journal, memory


class Portability:
    def __init__(self, control_journal, lifecycle, manager):
        _require(lifecycle.journal is control_journal and lifecycle.manager is manager, "Portability requires shared lifecycle ownership")
        self.journal, self.lifecycle, self.manager = control_journal, lifecycle, manager
        self.agents = AgentDirectory(control_journal)
        self._restore_locks = {}

    async def recover(self):
        """Discard unpublished imports under the new owner's startup fence."""
        _require(self.lifecycle._loop is None, "Restore recovery must precede lifecycle admission")
        discarded = []
        for row in await asyncio.to_thread(self.journal.list, RUNTIMES, CONTROL):
            if row.value["desired"] != "restoring": continue
            async with self.lifecycle._locks.setdefault(row.id, asyncio.Lock()):
                current = await asyncio.to_thread(self.lifecycle._row, row.id)
                if current.value["desired"] == "restoring":
                    await _settled(self._discard(row.id))
                    discarded.append(row.id)
        return {"discarded": discarded}

    async def backup(self, principal, agent_id):
        metadata = await asyncio.to_thread(self.agents.authorize, principal, agent_id, "read")
        async with self.lifecycle.snapshot(agent_id) as config:
            copying = asyncio.create_task(asyncio.to_thread(_export, config, metadata["name"]))
            try: return await asyncio.shield(copying)
            except asyncio.CancelledError:
                await copying
                raise

    async def restore(self, principal, *, name, archive_base64, sha256, request_id=None):
        await asyncio.to_thread(self.agents.list, principal)
        request_id = identifier(new_id("restore") if request_id is None else request_id)
        bundle = await asyncio.to_thread(_unpack, archive_base64, sha256)
        digest = hashlib.sha256(canonical_json({"name": name, "sha256": sha256}).encode()).hexdigest()
        async with self._restore_locks.setdefault((principal.id, request_id), asyncio.Lock()):
            return await self._restore(principal, name, sha256, request_id, digest, bundle)

    def _receipt(self, principal, request_id, digest):
        row = self.journal.get(RESTORES, request_id, principal.id)
        if row is None: return None
        _require(row.value["digest"] == digest, "Restore request identity was reused with different parameters", "conflict")
        if row.value["state"] == "completed":
            self.agents.authorize(principal, row.value["agent_id"], "read")
            return row.value["result"]
        if row.value["state"] == "failed":
            raise CoreError("restore_interrupted", "Unpublished restore was removed; use a new request identity for a new attempt",
                request_id=request_id)
        raise CoreError("busy", "Restore is still in progress; retry the same request identity", request_id=request_id)

    async def _restore(self, principal, name, sha256, request_id, digest, bundle):
        def reserve():
            with self.journal.transaction():
                prior = self._receipt(principal, request_id, digest)
                if prior is not None: return {"result": prior}
                agent = self.agents.create(principal, name)
                self.journal.put(RUNTIMES, agent["id"], CONTROL, {"desired": "restoring", "observed": "restoring",
                    "generation": 0, "next_wake_at": None, "error": None, "updated_at": time.time(),
                    "restore_request": {"owner_id": principal.id, "request_id": request_id}})
                self.journal.put(RESTORES, request_id, principal.id, {"digest": digest, "state": "restoring",
                    "agent_id": agent["id"], "result": None})
                return {"agent": agent}
        reserving = asyncio.create_task(asyncio.to_thread(reserve))
        try: reserved = await asyncio.shield(reserving)
        except asyncio.CancelledError:
            reserved = await reserving
            if "agent" in reserved: await _settled(self._discard(reserved["agent"]["id"]))
            raise
        if "result" in reserved: return reserved["result"]
        agent = reserved["agent"]
        agent_id = agent["id"]
        async with self.lifecycle._locks.setdefault(agent_id, asyncio.Lock()):
            try:
                config = await _settled(self.manager.create(agent_id))
                summary = await _settled(asyncio.to_thread(self._restore_content, config, *bundle, sha256))
                await self._restore_memory(config, bundle[3])
                result = {**agent, "restore": {**summary, "request_id": request_id}}
                await _settled(asyncio.to_thread(self._publish, principal.id, request_id, result))
                return result
            except BaseException:
                await _settled(self._discard(agent_id))
                raise

    def _publish(self, owner_id, request_id, result):
        with self.journal.transaction():
            row = self.journal.get(RESTORES, request_id, owner_id)
            self.lifecycle._update(row.value["agent_id"], desired="stopped", observed="new", error=None)
            self.journal.put(RESTORES, request_id, owner_id, {**row.value, "state": "completed", "result": result}, row.revision)

    def _restore_receipt(self, agent_id):
        lifecycle = self.lifecycle._row(agent_id)
        identity = lifecycle.value.get("restore_request")
        return self.journal.get(RESTORES, identity["request_id"], identity["owner_id"]) if identity else None

    async def _discard(self, agent_id):
        receipt = await asyncio.to_thread(self._restore_receipt, agent_id)
        if receipt and receipt.value["state"] == "completed": return
        await self.manager.delete(agent_id)
        def discard():
            with self.journal.transaction():
                row = self.journal.get("consumer.agents", agent_id, CONTROL)
                if row is not None: self.journal.delete(row.namespace, row.id, CONTROL, row.revision)
                self.lifecycle._update(agent_id, desired="deleted", observed="deleted", error={"code": "restore_failed"})
                if receipt:
                    self.journal.put(RESTORES, receipt.id, receipt.scope, {**receipt.value, "state": "failed"}, receipt.revision)
        await asyncio.to_thread(discard)

    def _restore_content(self, config, manifest, entries, data, memory, digest):
        store = ArtifactStore(config.root / "artifacts")
        for path, body in entries.items():
            if path.startswith("workspace/"):
                target = config.workspace.joinpath(*_path(path).parts[1:])
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with target.open("xb") as stream: stream.write(body)
                target.chmod(0o600)
        artifacts = {}
        for metadata in manifest["artifacts"]:
            object_body(metadata, {"id", "media_type"}, {"id", "media_type"})
            _text(metadata["media_type"], 256); identifier(metadata["id"])
            key = "artifacts/" + metadata["id"]
            _require(key in entries and metadata["id"] not in artifacts, "Artifact manifest is incomplete or duplicated")
            result = store.put(config.scope, entries[key], metadata["media_type"])
            _require(result.id == metadata["id"], "Artifact identity does not match content and media type")
            artifacts[result.id] = result
        _require({"artifacts/" + key for key in artifacts} == {key for key in entries if key.startswith("artifacts/")}, "Unlisted artifact content")
        history = [store.put(config.scope, entries[file], "application/json").id for file in ("journal.json", "memory.json")]
        provenance = {"archive_sha256": digest, "source_scope": manifest["source_scope"], "authority": "imported_history",
                      "history_artifact_ids": history}
        with closing(Journal(config.root / "operations.sqlite")) as journal:
            ProfileRegistry(journal).register(config.scope, config.workspace)
            with journal.transaction():
                for event in data["events"]:
                    public = event["kind"] in {"user.prompt", "user.steer", "user.cancel", "output.persisted",
                        "runtime.turn_completed", "runtime.turn_failed", "runtime.turn_interrupted"}
                    kind = event["kind"] if public else "portable.history"
                    journal._db.execute("INSERT INTO events(id,scope,kind,source,payload,created,dedupe) VALUES(?,?,?,?,?,?,NULL)",
                        (event["id"], config.scope, kind, "portable.import", canonical_json(event["payload"]), event["created"]))
                rows = {(r["namespace"], r["id"]): r for r in data["records"]}
                self._read_models(journal, config.scope, rows, artifacts, store, provenance)
                journal.put("consumer.portable_history", "source", config.scope, provenance)
                events = journal.events(config.scope, limit=MAX_ROWS + 1)
                for consumer in ("memory/native-v1", "runtime.ingress"):
                    journal.advance_cursor(config.scope, consumer, events[-1].seq if events else 0)
                journal.append(config.scope, "portable.restored", {**provenance, "reconnect": RECONNECT,
                    "relocation": RELOCATION}, source="portability")
        return {**provenance, "reconnect": RECONNECT, "relocation": RELOCATION,
                "files": len([p for p in entries if p.startswith("workspace/")]), "memory_records": len(memory["canonical"]) + len(memory["derived"])}

    @staticmethod
    def _read_models(journal, scope, rows, artifacts, store, provenance):
        bindings = set()
        for (namespace, key), row in rows.items():
            body = row["value"]
            if namespace == "consumer.conversations":
                identifier(key); identifier(body.get("binding_id")); _text(body.get("title"), 240)
                _require(type(body.get("archived")) is bool and body["binding_id"] not in bindings, "Invalid conversation snapshot")
                bindings.add(body["binding_id"])
                ChannelCore(journal).bind(scope, "consumer", scope, key, "owner", binding_id=body["binding_id"],
                    capabilities={"replay": True, "receipts": False})
                journal.put(namespace, key, scope, {"title": body["title"], "binding_id": body["binding_id"],
                    "archived": body["archived"], "created_at": body.get("created_at", 0), "portable_import": provenance})
            elif namespace == "consumer.attachments":
                identifier(key); artifact = artifacts.get(body.get("artifact_id"))
                _require(artifact is not None, "Attachment artifact is missing")
                _text(body.get("name"), 255); identifier(body.get("source_event_id"))
                extraction, text = _extract(store.get(scope, artifact.id), artifact.media_type)
                journal.put(namespace, key, scope, {"name": body["name"], "media_type": artifact.media_type,
                    "bytes": artifact.size, "sha256": artifact.sha256, "artifact_id": artifact.id,
                    "source_event_id": body["source_event_id"], "extraction": extraction})
                if text is not None: journal.put("consumer.extracted", key, scope, {"text": text})
            elif namespace in {"consumer.knowledge", "consumer.knowledge_revisions"}:
                _text(body.get("title"), 240); _text(body.get("text"), 262144, empty=True)
                _require(("consumer.attachments", body.get("attachment_id")) in rows and type(body.get("deleted")) is bool,
                         "Knowledge attachment or deletion state is invalid")
                names = {"title", "text", "attachment_id", "deleted", "memory_ids", "retired_memory_ids", "source_event_ids"}
                for field in ("memory_ids", "retired_memory_ids", "source_event_ids"):
                    _require(isinstance(body.get(field), list) and len(body[field]) <= MAX_ROWS, "Invalid knowledge source array")
                    for value in body[field]: _text(value, 256)
                value = {field: body[field] for field in names}; value["origin"] = "uploaded_document"
                if namespace.endswith("_revisions"):
                    value.update(document_id=_text(body.get("document_id"), 128), document_revision=_integer(body.get("document_revision"), 1))
                journal._db.execute("INSERT INTO records VALUES(?,?,?,?,?)", (namespace, key, scope, row["revision"], canonical_json(value)))
            elif namespace == "skills":
                registry = SkillRegistry(journal)
                registry.install(scope, body.get("manifest"), source="portable_import")
                registry.set_enabled(scope, key, False)
        for (namespace, key), row in rows.items():
            body = row["value"]
            if namespace == "consumer.messages":
                request = body.get("request")
                _require(isinstance(request, dict) and ("consumer.conversations", request.get("conversation_id")) in rows
                    and request.get("conversation_id") == body.get("conversation_id"), "Invalid historical message")
                object_body(request, {"conversation_id", "message_id", "text", "attachments", "kind"}, {"conversation_id", "message_id", "text"})
                identifier(request["message_id"]); _text(request["text"], 65536, empty=True)
                _require(request.get("kind", "prompt") in {"prompt", "steer", "cancel"}
                    and isinstance(request.get("attachments", []), list) and len(request.get("attachments", [])) <= 32,
                    "Invalid historical input shape")
                journal.put(namespace, key, scope, {**body, "status": "historical", "portable_original_status": body.get("status"), "portable_import": provenance})
            elif namespace == "runtime_turns":
                _require(body.get("binding_id") in bindings and isinstance(body.get("history"), list), "Historical turn lacks conversation or transcript")
                identifier(body.get("event_id")); identifier(body.get("stream_id"))
                for field in ("source_seq", "next_sequence", "model_calls", "input_cursor"): _integer(body.get(field, 0))
                _text(body.get("input_text", ""), 65536, empty=True)
                _require(all(isinstance(item, dict) and item.get("role") in {"user", "assistant", "tool", "system"}
                    and isinstance(item.get("content"), str) for item in body["history"])
                    and (body.get("error") is None or isinstance(body["error"], dict)), "Invalid historical transcript")
                state = body.get("state") if body.get("state") in {"completed", "failed", "interrupted"} else "interrupted"
                journal.put(namespace, key, scope, {**body, "state": state, "cancel_requested": True,
                    "portable_original_state": body.get("state"), "portable_import": provenance})
                journal.put("work", key, scope, {"kind": "imported_history", "state": "cancelled", "payload": {},
                    "capabilities": [], "resources": {}, "constraints": {}, "parent_id": None, "goal_id": None,
                    "generation": 0, "revoked": True, "lease_until": None, "usage": {}, "claim_reserved": {},
                    "child_reserved": {}, "allocation_closed": True, "allocation_reserved": False, "goal_reserved": False,
                    "budget_overrun": False, "portable_import": provenance})
            elif namespace == "outbox":
                _require(isinstance(body.get("payload"), dict) and body["payload"].get("binding_id") in bindings,
                         "Historical output lacks its conversation")
                identifier(body["payload"].get("stream_id")); _integer(body["payload"].get("sequence"))
                _require(isinstance(body["payload"].get("content"), dict), "Invalid historical output content")
                journal.put(namespace, key, scope, {**body, "state": "archived", "claim": None,
                    "portable_original_state": body.get("state"), "portable_import": provenance})

    async def _restore_memory(self, config, memory):
        partition = hashlib.sha256(config.scope.encode()).hexdigest()
        path = config.workspace / ".sturgis/profiles" / partition / "memory/memory.sqlite"
        client = NativeMemoryClient(path, binary=config.native_binary)
        async def request(operation, arguments):
            response = await client.request(config.scope, operation, arguments)
            _require(response["status"] == "ok", "Native memory rejected restored data", "invalid_backup_memory")
            return response["data"]
        def record(value):
            _require(isinstance(value, dict), "Native memory record must be an object")
            result = {k: v for k, v in value.items() if k not in {"scope", "revision", "watermark", "retracted"}}
            if isinstance(result.get("embedding"), dict):
                result["embedding"] = {k: v for k, v in result["embedding"].items() if k != "dimension"}
            return result
        try:
            _require(await client.start(), "Native memory is unavailable for restore validation", "memory_unavailable")
            revisions = {}
            for index, value in enumerate(memory["canonical"]):
                body = record(value); key = body.get("id")
                _text(key, 256); expected = revisions.get(key, 0)
                _require(_integer(value.get("revision"), 1) == expected + 1, "Native canonical revisions are not contiguous")
                await request("ingest", {"operation_id": "restore-canonical:" + str(index), "expected_revision": expected, "record": body})
                revisions[key] = expected + 1
            if memory["derived"]:
                generation = (await request("begin_generation", {"operation_id": "restore-begin", "expected_parent": 0}))["generation"]
                for index, value in enumerate(memory["derived"]):
                    await request("stage", {"operation_id": "restore-derived:" + str(index), "generation": generation, "records": [record(value)]})
                await request("publish", {"operation_id": "restore-publish", "generation": generation, "expected_parent": 0})
            for index, value in enumerate(memory["retractions"]):
                object_body(value, {"id", "revision", "reason"}, {"id", "revision", "reason"})
                revision = _integer(value["revision"])
                await request("retract", {"operation_id": "restore-retract:" + str(index), "id": value["id"],
                    "reason": value["reason"], **({"revision": revision} if revision else {})})
        finally:
            await client.close()
