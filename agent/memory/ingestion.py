"""Replay selected immutable journal events through idempotent native ingestion."""
from __future__ import annotations

import asyncio

from agent.protocol import CoreError, Event, canonical_json


DEFAULT_EVENT_KINDS = frozenset({"user.prompt", "user.steer", "awareness.observed", "tool.result",
                                "action.reconciled", "goal.changed", "commitment.changed", "output.persisted"})


class MemoryIngestor:
    consumer = "memory/native-v1"

    def __init__(self, journal, client, *, allowed_kinds=DEFAULT_EVENT_KINDS):
        if (not isinstance(allowed_kinds, (set, frozenset, list, tuple))
                or any(not isinstance(kind, str) or not kind or "*" in kind or kind.startswith("memory.") for kind in allowed_kinds)):
            raise CoreError("memory_config", "Memory ingestion requires explicit nonrecursive event kinds")
        self.journal, self.client, self.allowed_kinds = journal, client, frozenset(allowed_kinds)
        self._locks = {}

    @staticmethod
    def source_text(event):
        return canonical_json(event.payload)

    def process_event(self, event):
        if not isinstance(event, Event):
            raise CoreError("memory_event", "Expected a durable Journal Event")
        if event.kind not in self.allowed_kinds or event.source in {"memory", "memory_sleep", "memory_ingestion"}:
            return None
        source = self.source_text(event)
        content = f"Journal event {event.kind} from {event.source}:\n" + source
        if len(content.encode()) > 32768:
            raise CoreError("memory_event_size", "Event exceeds native memory content bound; exact journal source retained")
        return {"id": "event:" + event.id, "kind": "fact" if event.kind.startswith("user.") or event.kind == "awareness.observed" else "experience",
                "content": content, "sources": [{"id": event.id, "revision": 1, "start": 0, "end": len(source.encode())}],
                "source_time": event.created_at, "observed_at": event.created_at, "valid_from": event.created_at,
                "valid_to": None, "concepts": [event.kind]}

    def _acknowledge(self, event, rejection=None):
        with self.journal.transaction():
            if rejection is not None:
                prior = self.journal.get("memory.ingestion_errors", event.id, event.scope)
                if prior is None:
                    self.journal.put("memory.ingestion_errors", event.id, event.scope, rejection)
                    self.journal.append(event.scope, "memory.ingestion_rejected", {"source_event_id": event.id, "error": rejection},
                                        source="memory", dedupe_key="memory-rejected:" + event.id)
            if event.seq > self.journal.cursor(event.scope, self.consumer):
                self.journal.advance_cursor(event.scope, self.consumer, event.seq)

    async def replay(self, scope, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise CoreError("memory_limit", "Replay bound must be 1..1000")
        async with self._locks.setdefault(scope, asyncio.Lock()):
            cursor = await asyncio.to_thread(self.journal.cursor, scope, self.consumer)
            events = await asyncio.to_thread(self.journal.events, scope, after=cursor, limit=limit)
            result = {"status": "ok", "ingested": 0, "skipped": 0, "rejected": [], "cursor": cursor, "watermark": None}
            for event in events:
                rejection = None
                try:
                    record = self.process_event(event)
                except CoreError as exc:
                    record, rejection = None, exc.as_dict()
                if record is not None:
                    response = await self.client.ingest(scope, record, operation_id="journal:" + event.id)
                    result["watermark"] = response.get("watermark")
                    if response["status"] == "invalid_request":
                        rejection = response["error"]
                    elif response["status"] != "ok":
                        result.update(status=response["status"], error=response["error"])
                        break
                    else:
                        result["ingested"] += 1
                elif rejection is None:
                    result["skipped"] += 1
                if rejection:
                    result["status"] = "partial"
                    result["rejected"].append({"event_id": event.id, "error": rejection})
                await asyncio.to_thread(self._acknowledge, event, rejection)
                result["cursor"] = event.seq
            result["has_more"] = bool(await asyncio.to_thread(self.journal.events, scope, after=result["cursor"], limit=1))
            return result
