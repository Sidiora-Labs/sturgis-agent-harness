"""Durable IANA-day consolidation with resumable native operation receipts."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone as utc_timezone
import hashlib
import inspect
import math
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jsonschema import Draft202012Validator

from agent.protocol import CoreError, canonical_json, new_id, strict_loads, validate_scope


_ID = {"type": "string", "minLength": 1, "maxLength": 256}
_TIME = {"type": "number", "minimum": 0}
_SOURCE = {"type": "object", "additionalProperties": False, "required": ["id", "revision", "start", "end"],
           "properties": {"id": _ID, "revision": {"type": "integer", "minimum": 1},
                          "start": {"type": "integer", "minimum": 0}, "end": {"type": "integer", "minimum": 1}}}
_RECORD = {"type": "object", "additionalProperties": False,
           "required": ["id", "kind", "content", "sources", "source_time", "observed_at", "valid_from", "concepts"],
           "properties": {"id": _ID, "kind": {"enum": ["observation", "belief", "procedure"]},
               "content": {"type": "string", "minLength": 1, "maxLength": 32768},
               "sources": {"type": "array", "items": _SOURCE, "minItems": 1, "maxItems": 64},
               "source_time": _TIME, "observed_at": _TIME, "valid_from": _TIME,
               "valid_to": {"anyOf": [_TIME, {"type": "null"}]},
               "concepts": {"type": "array", "items": _ID, "maxItems": 64, "uniqueItems": True},
               "embedding": {"type": "object", "additionalProperties": False, "required": ["model", "vector"],
                   "properties": {"model": _ID, "vector": {"type": "array", "minItems": 1, "maxItems": 2048,
                                                              "items": {"type": "number"}}}}}}
_VALIDATOR = Draft202012Validator(_RECORD)


def validate_candidate(candidate, evidence):
    encoded = canonical_json(candidate)
    if len(encoded.encode()) > 1048000:
        raise CoreError("memory_candidate", "Candidate exceeds bounded native frame size")
    value = strict_loads(encoded)
    errors = list(_VALIDATOR.iter_errors(value))
    if errors:
        raise CoreError("memory_candidate", errors[0].message)
    strings = [(value["id"], 256), (value["content"], 32768)] + [(x, 256) for x in value["concepts"]]
    for source in value["sources"]:
        strings.append((source["id"], 256))
        if source["end"] <= source["start"] or not any(
                source["id"] == known["id"] and source["revision"] == known["revision"]
                and known["start"] <= source["start"] < source["end"] <= known["end"] for known in evidence):
            raise CoreError("memory_evidence", "Candidate cites unavailable or out-of-range source evidence")
    if value.get("valid_to") is not None and value["valid_to"] <= value["valid_from"]:
        raise CoreError("memory_candidate", "Invalid candidate validity interval")
    if "embedding" in value:
        strings.append((value["embedding"]["model"], 256))
        if not any(value["embedding"]["vector"]):
            raise CoreError("memory_candidate", "Candidate embedding has zero norm")
    if any("\x00" in text or len(text.encode()) > bound for text, bound in strings):
        raise CoreError("memory_candidate", "Candidate exceeds UTF-8 byte/string constraints")
    return value


def consolidate_duplicates(scope, records, cycle):
    """Consolidate exact repeated source content; do not infer unobserved causal outcomes."""
    groups = {}
    for record in records:
        if record["kind"] in {"fact", "experience"}:
            groups.setdefault(record["content"], []).append(record)
    results = []
    for content, group in groups.items():
        if len(group) < 2:
            continue
        sources = {canonical_json(source): source for record in group for source in record["sources"]}
        summary = f"Repeated recorded evidence ({len(group)} records):\n{content}"
        if len(summary.encode()) > 32768 or len(sources) > 64:
            continue
        identity = hashlib.sha256(canonical_json([scope, content]).encode()).hexdigest()
        results.append({"id": "summary:" + identity, "kind": "observation", "content": summary,
                        "sources": list(sources.values()), "source_time": min(r["source_time"] for r in group),
                        "observed_at": cycle["started_at"], "valid_from": cycle["started_at"], "valid_to": None,
                        "concepts": sorted({c for record in group for c in record["concepts"]})[:64]})
    return results


async def _invoke(function, *args):
    result = await asyncio.to_thread(function, *args)
    return await result if inspect.isawaitable(result) else result


class MemorySleep:
    namespace = "memory.sleep"

    def __init__(self, journal, client, ingestor, *, timezone="UTC", consolidator=None,
                 admission=None, max_events=100, max_candidates=20, cycle_timeout=30.0):
        try:
            self.timezone = ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
            raise CoreError("memory_timezone", "A valid IANA timezone is required") from exc
        if type(max_events) is not int or not 1 <= max_events <= 1000 or type(max_candidates) is not int or not 1 <= max_candidates <= 100:
            raise CoreError("memory_limit", "Sleep event/candidate bounds are invalid")
        if type(cycle_timeout) not in (int, float) or not math.isfinite(cycle_timeout) or not 0 < cycle_timeout <= 3600:
            raise CoreError("memory_limit", "Cycle timeout must be finite and bounded")
        self.journal, self.client, self.ingestor = journal, client, ingestor
        self.consolidator, self.admission = consolidator or consolidate_duplicates, admission
        self.max_events, self.max_candidates, self.cycle_timeout = max_events, max_candidates, cycle_timeout
        self.owner = new_id("sleepworker")
        self._locks = {}

    def _claim(self, scope, key, timestamp):
        with self.journal.transaction():
            row = self.journal.get(self.namespace, key, scope)
            if row and row.value["phase"] == "complete":
                return row.value, False
            if row and row.value.get("owner") not in (None, self.owner) and row.value.get("owner_until", 0) > time.time():
                return {**row.value, "state": "busy", "status": "partial"}, False
            if row:
                value = dict(row.value)
                if value["state"] == "conflict":
                    value.update(attempt=value["attempt"] + 1, phase="replaying_ingestion", candidates=[], stage_index=0)
                    if value.get("generation"):
                        value["prune_ids"] = sorted(set(value["prune_ids"] + [value["generation"]]))
                    for field in ("generation", "parent", "source_watermark", "source_records"):
                        value.pop(field, None)
            else:
                queue = self.journal.get("memory.prune", "pending", scope)
                value = {"cycle_id": key, "timezone": self.timezone.key, "started_at": timestamp,
                         "phase": "replaying_ingestion", "attempt": 1, "cursor": 0, "candidates": [], "stage_index": 0,
                         "prune_ids": queue.value["generations"] if queue else [], "rejected": []}
            value.update(owner=self.owner, owner_until=time.time() + self.cycle_timeout + 10,
                         status="running", state=value["phase"])
            value.pop("error", None)
            self.journal.put(self.namespace, key, scope, value, row.revision if row else 0)
            return value, True

    def _save(self, scope, value, **changes):
        with self.journal.transaction():
            row = self.journal.get(self.namespace, value["cycle_id"], scope)
            if row is None or row.value.get("owner") != self.owner:
                raise CoreError("memory_cycle_owner", "Cycle ownership changed")
            result = {**value, **changes, "owner_until": time.time() + self.cycle_timeout + 10}
            self.journal.put(self.namespace, value["cycle_id"], scope, result, row.revision)
            return result

    def _release(self, scope, key):
        with self.journal.transaction():
            row = self.journal.get(self.namespace, key, scope)
            if row and row.value.get("owner") == self.owner:
                self.journal.put(self.namespace, key, scope, {**row.value, "owner": None, "owner_until": 0}, row.revision)

    def _finish(self, scope, value, protected):
        with self.journal.transaction():
            queue = self.journal.get("memory.prune", "pending", scope)
            processed = set(value["prune_ids"][:100])
            concurrent = [generation for generation in (queue.value["generations"] if queue else []) if generation not in processed]
            pending = sorted(set(protected + value["prune_ids"][100:] + concurrent))
            self.journal.put("memory.prune", "pending", scope, {"generations": pending}, queue.revision if queue else 0)
            value = self._save(scope, value, phase="complete", state="complete",
                               status="partial" if value["rejected"] else "ok", pending_prune=pending)
            self.journal.append(scope, "memory.sleep_completed", {"cycle_id": value["cycle_id"], "generation": value["generation"],
                                "source_watermark": value["source_watermark"], "rejected": value["rejected"]},
                                source="memory_sleep", dedupe_key="sleep:" + value["cycle_id"])
            return value

    @staticmethod
    def _result(value):
        keys = {"cycle_id", "status", "state", "phase", "cursor", "generation", "source_watermark", "attempt", "rejected", "pending_prune", "error"}
        return {key: value[key] for key in keys if key in value}

    def _cycle_identity(self, now=None):
        timestamp = time.time() if now is None else now
        if type(timestamp) not in (int, float) or not math.isfinite(timestamp) or timestamp < 0:
            raise CoreError("memory_time", "Cycle time must be finite UTC seconds")
        try:
            day = datetime.fromtimestamp(timestamp, utc_timezone.utc).astimezone(self.timezone).date().isoformat()
        except (OverflowError, OSError, ValueError) as exc:
            raise CoreError("memory_time", "Cycle timestamp is outside the supported calendar") from exc
        return timestamp, self.timezone.key + "|" + day

    def due(self, scope, now=None):
        """Check durable day/lease state before allocating background work."""
        validate_scope(scope)
        _, key = self._cycle_identity(now)
        row = self.journal.get(self.namespace, key, scope)
        if row is None:
            return True
        if row.value["phase"] == "complete":
            return False
        return not (row.value.get("owner") not in (None, self.owner)
                    and row.value.get("owner_until", 0) > time.time())

    async def run_cycle(self, scope, now=None):
        validate_scope(scope)
        timestamp, key = self._cycle_identity(now)
        async with self._locks.setdefault(scope, asyncio.Lock()):
            value, claimed = await asyncio.to_thread(self._claim, scope, key, timestamp)
            if not claimed:
                return self._result(value)
            try:
                async with asyncio.timeout(self.cycle_timeout):
                    if self.admission is not None and await _invoke(self.admission, scope, {"kind": "memory.sleep", "priority": "background", "cycle_id": key}) is not True:
                        value = await asyncio.to_thread(self._save, scope, value, state="deferred", status="partial")
                        return self._result(value)
                    prefix = "sleep:" + hashlib.sha256(canonical_json([scope, key, value["attempt"]]).encode()).hexdigest()
                    while value["phase"] != "complete":
                        phase = value["phase"]
                        response = None
                        if phase == "replaying_ingestion":
                            replay = await self.ingestor.replay(scope, self.max_events)
                            value = await asyncio.to_thread(self._save, scope, value, cursor=replay["cursor"], rejected=value["rejected"] + replay["rejected"])
                            if replay["status"] not in {"ok", "partial"} or replay["has_more"]:
                                value = await asyncio.to_thread(self._save, scope, value, status=replay["status"] if replay["status"] not in {"ok", "partial"} else "partial",
                                                               state=phase, error=replay.get("error"))
                                return self._result(value)
                            value = await asyncio.to_thread(self._save, scope, value, phase="preparing", state="preparing")
                        elif phase == "preparing":
                            response = await self.client.query(scope, limit=self.max_candidates)
                            if response["status"] == "ok":
                                data = response["data"]
                                value = await asyncio.to_thread(self._save, scope, value, phase="beginning", state="beginning",
                                                               parent=response["generation"], source_watermark=data["source_watermark"],
                                                               source_records=[item["record"] for item in data["records"]])
                        elif phase == "beginning":
                            response = await self.client.request(scope, "begin_generation", {"operation_id": prefix + ":begin",
                                "expected_parent": value["parent"], "source_watermark": value["source_watermark"]})
                            if response["status"] == "ok":
                                value = await asyncio.to_thread(self._save, scope, value, generation=response["data"]["generation"],
                                                               phase="consolidating", state="consolidating")
                        elif phase == "consolidating":
                            candidates = await _invoke(self.consolidator, scope, strict_loads(canonical_json(value["source_records"])), strict_loads(canonical_json(value)))
                            if not isinstance(candidates, list) or len(candidates) > self.max_candidates:
                                raise CoreError("memory_candidate", "Consolidator must return a bounded candidate list")
                            evidence = [source for record in value["source_records"] for source in record["sources"]]
                            accepted, rejected, seen = [], list(value["rejected"]), set()
                            for candidate in candidates:
                                try:
                                    record = await asyncio.to_thread(validate_candidate, candidate, evidence)
                                    if record["id"] in seen:
                                        raise CoreError("memory_candidate", "Duplicate candidate identity")
                                    seen.add(record["id"])
                                    accepted.append(record)
                                except CoreError as exc:
                                    rejected.append({"error": exc.as_dict()})
                            value = await asyncio.to_thread(self._save, scope, value, candidates=accepted, rejected=rejected,
                                                           phase="staging", state="staging", stage_index=0)
                        elif phase == "staging":
                            index = value["stage_index"]
                            if index == len(value["candidates"]):
                                value = await asyncio.to_thread(self._save, scope, value, phase="publishing", state="publishing")
                            else:
                                response = await self.client.request(scope, "stage", {"operation_id": prefix + f":stage:{index}",
                                    "generation": value["generation"], "records": [value["candidates"][index]]})
                                if response["status"] == "ok":
                                    value = await asyncio.to_thread(self._save, scope, value, stage_index=index + 1)
                        elif phase == "publishing":
                            response = await self.client.request(scope, "publish", {"operation_id": prefix + ":publish",
                                "generation": value["generation"], "expected_parent": value["parent"]})
                            if response["status"] == "ok":
                                pruning = sorted(set(value["prune_ids"] + ([value["parent"]] if value["parent"] else [])))
                                value = await asyncio.to_thread(self._save, scope, value, phase="pruning", state="pruning", prune_ids=pruning)
                        elif phase == "pruning":
                            protected = []
                            if value["prune_ids"]:
                                response = await self.client.request(scope, "prune", {"operation_id": prefix + ":prune", "generations": value["prune_ids"][:100]})
                                if response["status"] == "ok":
                                    protected = [item["generation"] for item in response["data"]["protected"] if item["reason"] != "not_found"]
                            if response is None or response["status"] == "ok":
                                value = await asyncio.to_thread(self._finish, scope, value, protected)
                        else:
                            raise CoreError("memory_cycle", "Unknown persisted cycle phase")
                        if response is not None and response["status"] != "ok":
                            value = await asyncio.to_thread(self._save, scope, value, state=response["status"], status=response["status"], error=response["error"])
                            return self._result(value)
                    return self._result(value)
            except asyncio.TimeoutError:
                value = await asyncio.to_thread(self._save, scope, value, state="unavailable", status="unavailable", error={"code": "deadline", "message": "Sleep cycle deadline exceeded"})
                return self._result(value)
            except CoreError as exc:
                if exc.code == "memory_cycle_owner":
                    return {"cycle_id": key, "status": "conflict", "state": "busy", "error": exc.as_dict()}
                value = await asyncio.to_thread(self._save, scope, value, state="partial", status="partial", error=exc.as_dict())
                return self._result(value)
            except Exception as exc:
                value = await asyncio.to_thread(self._save, scope, value, state="partial", status="partial",
                                               error={"code": "memory_cycle_error", "message": str(exc)[:4096]})
                return self._result(value)
            finally:
                await asyncio.to_thread(self._release, scope, key)
