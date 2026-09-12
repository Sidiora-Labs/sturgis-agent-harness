"""Signed external evidence admission; webhook bodies never choose owner authority."""
from __future__ import annotations

import hashlib
import hmac
import time

from agent.protocol import CoreError, canonical_json, strict_loads
from consumer.contracts import identifier, object_body


def signing_bytes(connection_id, revision, timestamp, nonce, body):
    """Public wire contract. Sign these bytes using the connection's current secret."""
    return canonical_json({"version": "webhook/1", "connection_id": connection_id, "revision": revision,
        "timestamp": timestamp, "nonce": nonce, "sha256": hashlib.sha256(body).hexdigest()}).encode()


class WebhookIngress:
    def __init__(self, connections):
        self.connections, self.journal, self.scope = connections, connections.journal, connections.scope

    def admit(self, connection_id, body: bytes, *, timestamp, nonce, signature):
        identifier(connection_id)
        identifier(nonce)
        if (not isinstance(body, bytes) or len(body) > 1048576 or type(timestamp) is not int
                or not isinstance(signature, str) or len(signature) != 64):
            raise CoreError("invalid_webhook", "Webhook body and signature must be bounded")
        row = self.connections.get(connection_id)
        if row["kind"] != "webhook":
            raise CoreError("invalid_webhook", "Connection is not an incoming webhook")
        row = self.connections.pinned(connection_id, row["revision"], "receive")
        if abs(time.time() - timestamp) > row["options"].get("max_skew", 300):
            raise CoreError("webhook_expired", "Webhook timestamp is outside the acceptance window")
        secret = self.connections.vault.resolve(self.scope, row["credential_ref"])
        digest = hmac.new(secret.encode(), signing_bytes(connection_id, row["revision"], timestamp, nonce, body), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(digest, signature):
            raise CoreError("webhook_signature", "Webhook signature does not match the current connection")
        payload = object_body(strict_loads(body), {"event_id", "text", "data"}, required={"event_id"})
        identifier(payload["event_id"])
        if ("text" in payload and (not isinstance(payload["text"], str) or len(payload["text"]) > 65536)
                or "data" in payload and not isinstance(payload["data"], dict)):
            raise CoreError("invalid_webhook", "Webhook text or data has an invalid shape")
        fingerprint = hashlib.sha256(body).hexdigest()
        identity = connection_id + ":" + payload["event_id"]
        nonce_key = connection_id + ":" + nonce
        with self.journal.transaction():
            self.connections.pinned(connection_id, row["revision"], "receive")
            used_nonce = self.journal.get("consumer.webhook_nonces", nonce_key, self.scope)
            receipt = self.journal.get("consumer.webhooks", identity, self.scope)
            expected = {"identity": identity, "fingerprint": fingerprint, "revision": row["revision"]}
            if used_nonce and used_nonce.value != expected:
                raise CoreError("webhook_replay", "Webhook nonce was already used for another event")
            if receipt and (receipt.value["fingerprint"] != fingerprint or receipt.value["revision"] != row["revision"]):
                raise CoreError("dedupe_conflict", "Webhook event identity was reused with another payload or grant")
            if not used_nonce:
                self.journal.put("consumer.webhook_nonces", nonce_key, self.scope, expected)
            if receipt:
                return self.journal.event(self.scope, receipt.value["event_id"])
            event = self.journal.append(self.scope, "external.webhook", {
                "connection_id": connection_id, "connection_revision": row["revision"],
                "sender": row["options"].get("sender", connection_id), "trust": "external_evidence",
                "external_event_id": payload["event_id"], "text": payload.get("text", ""),
                "data": payload.get("data", {}), "sha256": fingerprint}, source="webhooks",
                dedupe_key="webhook:" + identity)
            self.journal.put("consumer.webhooks", identity, self.scope, {
                "fingerprint": fingerprint, "revision": row["revision"], "event_id": event.id})
            return event
