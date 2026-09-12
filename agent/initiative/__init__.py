"""Evidence-linked proactive candidates; scoring never dispatches work."""

import hashlib
import math

from agent.awareness import required, text, timestamp
from agent.awareness.evidence import resolve_evidence
from agent.protocol import CoreError, canonical_json, new_id
from agent.state import Journal


SIGNALS = {"urgency", "expected_value", "confidence", "interruption_cost", "resource_cost", "duplication_penalty"}


def score(signals):
    if not isinstance(signals, dict) or set(signals) != SIGNALS or any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1 for value in signals.values()):
        raise CoreError("invalid_candidate", "All candidate signals must be finite numbers in 0..1")
    return (3 * signals["urgency"] + 3 * signals["expected_value"] + 2 * signals["confidence"] - 2 * signals["interruption_cost"] - signals["resource_cost"] - 2 * signals["duplication_penalty"]) / 8


class Initiative:
    def __init__(self, journal: Journal):
        self.journal = journal

    def get(self, scope, candidate_id):
        return required(self.journal, "initiative", candidate_id, scope)

    def propose(self, scope, source_event_id, action, *, signals, expires_at, dedupe_key, now=None, channel="default"):
        value = score(signals)
        text(dedupe_key, "dedupe_key", 512)
        text(channel, "channel", 256)
        if not isinstance(action, (str, dict)) or not action:
            raise CoreError("invalid_candidate", "A concrete action description or object is required")
        fingerprint = hashlib.sha256(canonical_json({"source": source_event_id, "action": action, "signals": signals, "expires_at": expires_at, "channel": channel}).encode()).hexdigest()
        with self.journal.transaction():
            prior = self.journal.get("initiative_keys", dedupe_key, scope)
            if prior:
                if prior.value["fingerprint"] != fingerprint:
                    raise CoreError("dedupe_conflict", "Initiative deduplication key was reused")
                return self.get(scope, prior.value["candidate_id"])
            created = timestamp(now)
            if timestamp(expires_at) <= created:
                raise CoreError("invalid_candidate", "Candidate must expire after creation")
            resolve_evidence(self.journal, scope, [source_event_id])
            body = {"source_event_id": source_event_id, "action": action, "signals": signals, "score": value, "channel": channel, "expires_at": expires_at, "created_at": created, "action_key": hashlib.sha256(canonical_json(action).encode()).hexdigest()}
            row = self.journal.put("initiative", new_id("candidate"), scope, body)
            self.journal.put("initiative_keys", dedupe_key, scope, {"candidate_id": row.id, "fingerprint": fingerprint})
            self.journal.append(scope, "initiative.proposed", {"id": row.id, **body}, source="initiative")
            return row
