"""Durable watcher observations and evidence-linked current-state projections."""

import math
import time

from agent.protocol import CoreError, canonical_json
from agent.state import Journal, Record


def timestamp(value=None):
    value = time.time() if value is None else value
    if type(value) not in (float, int) or not math.isfinite(value) or not 0 <= value <= 253402214399:
        raise CoreError("invalid_time", "Time must be finite nonnegative UTC seconds within the supported calendar")
    return float(value)


def text(value, name="value", limit=4096):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise CoreError("invalid_value", "A bounded nonempty string is required", field=name)
    return value


def required(journal, namespace, key, scope):
    text(key, "record_id", 512)
    row = journal.get(namespace, key, scope)
    if row is None:
        raise CoreError("not_found", "Scoped record was not found", namespace=namespace, id=key)
    return row


class Awareness:
    def __init__(self, journal: Journal, *, max_observation_bytes=65536):
        if type(max_observation_bytes) is not int or max_observation_bytes <= 0:
            raise CoreError("invalid_limit", "Observation size limit must be positive")
        self.journal, self.max_bytes = journal, max_observation_bytes

    def register_watcher(self, scope, watcher_id, source, *, source_identity=None):
        body = {"source": text(source, "source", 128), "source_identity": text(source_identity or watcher_id), "active": True, "generation": 1}
        with self.journal.transaction():
            row = self.journal.put("watchers", text(watcher_id, "watcher_id", 256), scope, body)
            self.journal.append(scope, "watcher.registered", {"id": row.id, **body}, source="awareness")
            return row

    def set_watcher_active(self, scope, watcher_id, active, *, expected_revision):
        if type(active) is not bool:
            raise CoreError("invalid_value", "Watcher active state must be boolean")
        with self.journal.transaction():
            row = required(self.journal, "watchers", watcher_id, scope)
            body = {**row.value, "active": active, "generation": row.value["generation"] + 1}
            updated = self.journal.put("watchers", watcher_id, scope, body, expected_revision)
            self.journal.append(scope, "watcher.changed", {"id": watcher_id, **body}, source="awareness")
            return updated

    def observe(self, scope, watcher_id, object_id, properties, *, dedupe_key, now=None, generation=None):
        text(object_id, "object_id", 512)
        text(dedupe_key, "dedupe_key", 512)
        if not isinstance(properties, dict) or len(canonical_json(properties).encode()) > self.max_bytes:
            raise CoreError("invalid_observation", "Observation must be a bounded JSON object")
        source_time = None if now is None else timestamp(now)
        with self.journal.transaction():
            watcher = required(self.journal, "watchers", watcher_id, scope)
            if not watcher.value["active"] or (generation is not None and generation != watcher.value["generation"]):
                raise CoreError("watcher_unavailable", "Watcher is inactive or its generation is stale")
            payload = {"watcher_id": watcher_id, "watcher_generation": watcher.value["generation"], "source_identity": watcher.value["source_identity"], "source_type": watcher.value["source"], "object_id": object_id, "properties": properties, "observed_at": source_time}
            event = self.journal.append(scope, "awareness.observed", payload, source="awareness", dedupe_key=f"awareness:{watcher_id}:{dedupe_key}")
            prior_observation = self.journal.get("observations", event.id, scope)
            if prior_observation:
                return prior_observation
            observed_at = event.created_at if source_time is None else source_time
            prior = self.journal.get("world_objects", object_id, scope)
            applied = prior is None or observed_at >= prior.value["observed_at"]
            if applied:
                body = {"properties": properties, "event_id": event.id, "event_seq": event.seq, "watcher_id": watcher_id, "source_identity": watcher.value["source_identity"], "observed_at": observed_at, "uncertainty": "observed"}
                self.journal.put("world_objects", object_id, scope, body, prior.revision if prior else 0)
            return self.journal.put("observations", event.id, scope, {**payload, "event_seq": event.seq, "applied": applied})

    def snapshot(self, scope):
        with self.journal.transaction():
            objects = self.journal.list("world_objects", scope)
            observations = self.journal.list("observations", scope)
            through = max((row.value["event_seq"] for row in observations), default=0)
            while page := self.journal.events(scope, after=through):
                through = page[-1].seq
            return {"objects": [{"id": row.id, "revision": row.revision, **row.value} for row in objects], "watchers": [{"id": row.id, "revision": row.revision, **row.value} for row in self.journal.list("watchers", scope)], "through_event_seq": through, "domain_references": {domain: [{"id": row.id, "revision": row.revision, "state": row.value.get("state")} for row in self.journal.list(domain, scope)] for domain in ("goals", "commitments")}}
