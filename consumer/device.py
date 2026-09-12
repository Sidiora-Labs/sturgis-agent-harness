"""One-time device pairing, signed polling and durable scoped command receipts."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time

from agent.plugin_sdk import _check_schema, validate_schema
from agent.protocol import CoreError, canonical_json, new_id, strict_loads
from agent.tool_runner import ExecutorResult
from consumer.connections import ConnectionOperation
from consumer.contracts import identifier, object_body, public_record


def device_signing_bytes(action, connection_id, revision, timestamp, nonce, body=b""):
    return canonical_json({"version": "device/1", "action": action, "connection_id": connection_id,
        "revision": revision, "timestamp": timestamp, "nonce": nonce, "sha256": hashlib.sha256(body).hexdigest()}).encode()


def _options(value):
    identifier(value.get("device_id"))
    operations = value.get("operations")
    if not isinstance(operations, dict) or not 1 <= len(operations) <= 32:
        raise CoreError("invalid_device", "A device must declare one to 32 explicitly granted operations")
    for name, schema in operations.items():
        identifier(name)
        if not isinstance(schema, dict): raise CoreError("invalid_device", "Device operation requires an input schema")
        _check_schema(schema)
    if len(canonical_json(operations).encode()) > 32768:
        raise CoreError("invalid_device", "Device operation schemas exceed their byte bound")


class DeviceService:
    def __init__(self, connections):
        self.connections, self.core = connections, connections.core
        self.journal, self.scope, self.vault = connections.journal, connections.scope, connections.vault
        connections.register_adapter("device", {"request": ConnectionOperation(
            {"type": "object", "properties": {"operation": {"type": "string", "maxLength": 128}, "arguments": {"type": "object"}},
             "required": ["operation", "arguments"], "additionalProperties": False}, {"type": "object"}, self.execute,
            read_only=False, timeout=connections.network.timeout)}, probe=self._probe,
            option_fields={"device_id", "operations"}, validate_options=_options)

    def _device(self, connection_id):
        row = self.connections.get(connection_id)
        if row["kind"] != "device": raise CoreError("invalid_device", "Connection is not a paired device")
        return row

    def pair(self, connection_id, *, expected_revision, ttl=300):
        if type(ttl) is not int or not 1 <= ttl <= 900:
            raise CoreError("invalid_device", "Pairing lifetime must be one to 900 seconds")
        with self.journal.transaction():
            row = self._device(connection_id)
            if row["revision"] != expected_revision or not row["active"]:
                raise CoreError("stale_connection", "Pairing requires the current active connection")
            token = secrets.token_urlsafe(32)
            prior = self.journal.get("consumer.device_pairing", connection_id, self.scope)
            body = {"connection_revision": expected_revision, "device_id": row["options"]["device_id"],
                "token_sha256": hashlib.sha256(token.encode()).hexdigest(), "expires_at": time.time() + ttl, "used": False}
            self.journal.put("consumer.device_pairing", connection_id, self.scope, body, expected_revision=prior.revision if prior else 0)
            return {"connection_id": connection_id, "revision": expected_revision, "pairing_token": token, "expires_at": body["expires_at"]}

    def claim(self, connection_id, *, pairing_token, device_id):
        identifier(device_id)
        if not isinstance(pairing_token, str) or not 1 <= len(pairing_token) <= 128:
            raise CoreError("pairing_denied", "Pairing proof is invalid or expired")
        with self.journal.transaction():
            row = self._device(connection_id)
            pending = self.journal.get("consumer.device_pairing", connection_id, self.scope)
            if (pending is None or pending.value["used"] or pending.value["expires_at"] < time.time()
                    or pending.value["device_id"] != device_id or pending.value["connection_revision"] != row["revision"]
                    or not hmac.compare_digest(pending.value["token_sha256"], hashlib.sha256(pairing_token.encode()).hexdigest())):
                raise CoreError("pairing_denied", "Pairing proof is invalid or expired")
            secret = secrets.token_urlsafe(32)
            name = "device_" + connection_id
            credential = self.vault.get(self.scope, name)
            reference = self.vault.put(self.scope, name, secret, expected_revision=credential["revision"] if credential else 0)
            # A crash before this transaction commits leaves an unreachable vault
            # reference. It does not activate a device or reuse a signing secret.
            configured = self.connections.configure(connection_id, kind="device", endpoint=row["endpoint"],
                credential_ref=reference["reference"], options=row["options"], expected_revision=row["revision"])
            self.journal.put("consumer.device_pairing", connection_id, self.scope, {**pending.value, "used": True}, expected_revision=pending.revision)
            prior = self.journal.get("consumer.devices", connection_id, self.scope)
            self.journal.put("consumer.devices", connection_id, self.scope, {"device_id": device_id,
                "credential_ref": reference["reference"], "paired_at": time.time(), "last_seen": time.time()}, expected_revision=prior.revision if prior else 0)
            self.journal.append(self.scope, "external.device_paired", {"connection_id": connection_id, "device_id": device_id}, source="devices")
            return {"connection_id": connection_id, "revision": configured["revision"], "device_id": device_id, "secret": secret}

    def get(self, connection_id):
        connection = self._device(connection_id)
        row = self.journal.get("consumer.devices", connection_id, self.scope)
        return {"connection_id": connection_id, "revision": connection["revision"], "status": connection["status"],
            "device_id": connection["options"]["device_id"], "paired": bool(row and row.value["credential_ref"] == connection["credential_ref"]),
            "last_seen": row.value["last_seen"] if row else None}

    def list(self):
        return [self.get(row["id"]) for row in self.connections.list() if row["kind"] == "device"]

    def revoke(self, connection_id, *, expected_revision):
        self._device(connection_id)
        return self.connections.revoke(connection_id, expected_revision=expected_revision)

    async def _probe(self, record):
        row = await asyncio.to_thread(self.get, record["id"])
        return {"ok": row["paired"], "kind": "authenticated_device_pairing", "last_seen": row["last_seen"], "actuator_verified": False}

    def _authenticate(self, action, connection_id, body, timestamp, nonce, signature):
        identifier(nonce)
        if type(timestamp) is not int or abs(time.time() - timestamp) > 300 or not isinstance(signature, str) or len(signature) != 64:
            raise CoreError("device_signature", "Device signature is invalid or expired")
        row = self._device(connection_id)
        if action != "sync": self.connections.pinned(connection_id, row["revision"], "request")
        secret = self.vault.resolve(self.scope, row["credential_ref"])
        signed_revision = 0 if action == "sync" else row["revision"]
        expected = hmac.new(secret.encode(), device_signing_bytes(action, connection_id, signed_revision, timestamp, nonce, body), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise CoreError("device_signature", "Device signature does not match the current pairing")
        key = connection_id + ":" + nonce
        fingerprint = hashlib.sha256(device_signing_bytes(action, connection_id, signed_revision, timestamp, nonce, body)).hexdigest()
        prior = self.journal.get("consumer.device_nonces", key, self.scope)
        if prior and prior.value["fingerprint"] != fingerprint:
            raise CoreError("device_replay", "Device nonce was reused for another request")
        if not prior:
            self.journal.put("consumer.device_nonces", key, self.scope, {"fingerprint": fingerprint})
        paired = self.journal.get("consumer.devices", connection_id, self.scope)
        self.journal.put("consumer.devices", connection_id, self.scope, {**paired.value, "last_seen": time.time()}, expected_revision=paired.revision)
        return row

    def sync(self, connection_id, *, timestamp, nonce, signature):
        with self.journal.transaction():
            row = self._authenticate("sync", connection_id, b"", timestamp, nonce, signature)
            return {"connection_id": connection_id, "device_id": row["options"]["device_id"], "revision": row["revision"], "status": row["status"]}

    def poll(self, connection_id, *, timestamp, nonce, signature):
        with self.journal.transaction():
            connection = self._authenticate("poll", connection_id, b"", timestamp, nonce, signature)
            requests = []
            for row in self.journal.list("consumer.device_requests", self.scope):
                if (row.value["connection_id"] != connection_id or row.value["connection_revision"] != connection["revision"]
                        or row.value["state"] not in {"queued", "offered", "cancel_requested"}): continue
                value = dict(row.value)
                if value["expires_at"] < time.time(): value["state"] = "cancel_requested"
                elif value["state"] == "queued": value["state"] = "offered"
                self.journal.put(row.namespace, row.id, self.scope, value, expected_revision=row.revision)
                requests.append({"request_id": row.id, "operation": value["operation"], "arguments": value["arguments"],
                    "state": value["state"], "expires_at": value["expires_at"]})
                if len(requests) == 16: break
            return {"device_id": connection["options"]["device_id"], "revision": connection["revision"], "requests": requests}

    def respond(self, connection_id, body, *, timestamp, nonce, signature):
        if not isinstance(body, bytes) or len(body) > 262144:
            raise CoreError("invalid_device", "Device result must be bounded bytes")
        value = object_body(strict_loads(body), {"request_id", "status", "data"}, ["request_id", "status", "data"])
        identifier(value["request_id"])
        if value["status"] not in {"success", "target_error", "cancelled"} or not isinstance(value["data"], dict):
            raise CoreError("invalid_device", "Device result has an invalid outcome")
        with self.journal.transaction():
            connection = self._authenticate("respond", connection_id, body, timestamp, nonce, signature)
            row = self.journal.get("consumer.device_requests", value["request_id"], self.scope)
            if row is None or row.value["connection_id"] != connection_id or row.value["connection_revision"] != connection["revision"]:
                raise CoreError("device_request_missing", "Device result does not match an authorized request")
            if row.value.get("response"):
                if row.value["response"] != value: raise CoreError("dedupe_conflict", "Device changed an existing result")
                return {"event_id": row.value["event_id"], "state": row.value["state"]}
            late = row.value["state"] == "cancel_requested" or row.value["expires_at"] < time.time()
            event = self.journal.append(self.scope, "external.device_result", {"connection_id": connection_id,
                "device_id": connection["options"]["device_id"], "request_id": row.id, "response": value,
                "late": late, "trust": "external_evidence"}, source="devices", dedupe_key="device:" + row.id)
            state = "late_response" if late else "responded"
            self.journal.put(row.namespace, row.id, self.scope, {**row.value, "state": state, "response": value, "event_id": event.id}, expected_revision=row.revision)
            return {"event_id": event.id, "state": state}

    async def execute(self, arguments, context, record, secret):
        operations = record["options"]["operations"]
        if arguments["operation"] not in operations:
            raise CoreError("device_operation_denied", "Operation is not granted to this paired device", effects="none")
        validate_schema(operations[arguments["operation"]], arguments["arguments"], label="device operation arguments")
        request_id = context.attempt_id
        await asyncio.to_thread(self.journal.put, "consumer.device_requests", request_id, self.scope, {
            "connection_id": record["id"], "connection_revision": record["revision"], "operation": arguments["operation"],
            "arguments": arguments["arguments"], "state": "queued", "expires_at": time.time() + self.connections.network.timeout,
            "work_id": context.work_id, "generation": context.generation, "action_id": context.action_id, "response": None})
        try:
            while True:
                row = await asyncio.to_thread(self.journal.get, "consumer.device_requests", request_id, self.scope)
                if row.value["response"]:
                    response = row.value["response"]
                    if row.value["state"] == "late_response" or response["status"] == "cancelled":
                        raise CoreError("cancelled", "Device reported cancellation or a late result", effects="unknown")
                    return ExecutorResult({"device_id": record["options"]["device_id"], "response": response,
                        "evidence_event_id": row.value["event_id"], "trust": "external_evidence"}, outcome=response["status"], effects="unknown")
                await asyncio.sleep(.02)
        finally:
            def cancel():
                with self.journal.transaction():
                    row = self.journal.get("consumer.device_requests", request_id, self.scope)
                    if row.value["state"] in {"queued", "offered"}:
                        self.journal.put(row.namespace, row.id, self.scope, {**row.value, "state": "cancel_requested"}, expected_revision=row.revision)
            await asyncio.to_thread(cancel)
