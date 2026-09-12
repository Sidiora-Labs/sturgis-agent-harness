"""Revisioned connection grants and readiness within the core tool authority."""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from typing import Callable

from agent.protocol import CoreError, canonical_json
from agent.tool_runner import ExecutorResult
from consumer.contracts import identifier, object_body, public_record
from consumer.network import exact_origin, origin

NAMESPACE = "consumer.connections"


@dataclass(frozen=True)
class ConnectionOperation:
    input_schema: dict
    output_schema: dict
    execute: Callable
    read_only: bool = True
    timeout: float = 30
    output_limit: int = 1048576


class ConnectionService:
    def __init__(self, core, vault, network_policy):
        self.core, self.vault, self.network = core, vault, network_policy
        self.scope, self.journal = core.scope, core.journal
        self._adapters, self._registered, self._option_fields, self._option_checks = {}, set(), {}, {}
        previous = core.tools.authorize

        def authorize(scope, grant, call):
            if call.tool.startswith("connection."):
                parts = call.tool.split(".")
                if len(parts) != 3 or scope != self.scope:
                    raise CoreError("connection_denied", "Connection authority does not match the runtime", effects="none")
                self.pinned(parts[1], call.tool_version, parts[2])
            return previous(scope, grant, call)

        core.tools.authorize = authorize
        self.register_adapter("http", {"fetch": ConnectionOperation(
            {"type": "object", "properties": {"url": {"type": "string", "maxLength": 8192}}, "additionalProperties": False},
            {"type": "object", "properties": {"status": {"type": "integer"}, "url": {"type": "string"},
                "media_type": {"type": "string"}, "body": {"type": "string"}, "encoding": {"const": "base64"}},
             "required": ["status", "url", "media_type", "body", "encoding"], "additionalProperties": False}, self._fetch,
             timeout=network_policy.timeout)}, probe=self._probe_http)

    def register_adapter(self, kind, operations, *, probe, option_fields=(), validate_options=None):
        identifier(kind)
        if kind in self._adapters or not isinstance(operations, dict) or not operations or len(operations) > 64:
            raise CoreError("invalid_adapter", "Adapter kind and operations must be unique and bounded")
        for name, operation in operations.items():
            identifier(name)
            if not isinstance(operation, ConnectionOperation) or not callable(operation.execute):
                raise CoreError("invalid_adapter", "A concrete connection operation is required")
        if not callable(probe):
            raise CoreError("invalid_adapter", "An adapter must provide a real readiness probe")
        self._adapters[kind] = (dict(operations), probe)
        self._option_fields[kind], self._option_checks[kind] = set(option_fields), validate_options

    def _row(self, connection_id):
        row = self.journal.get(NAMESPACE, identifier(connection_id), self.scope)
        if row is None:
            raise CoreError("connection_missing", "Connection does not exist", effects="none")
        return row

    def _public(self, row):
        value = public_record(row)
        reference = value["credential_ref"]
        if not value["active"]:
            status = "revoked"
        elif reference and not self.vault.current(self.scope, reference):
            status = "stale_credential"
        elif not value["grants"]:
            status = "ungranted"
        elif not value["probe"] or value["probe"]["revision"] != row.revision:
            status = "unready"
        else:
            status = "ready" if value["probe"]["ok"] else "unavailable"
        return {**value, "status": status}

    def get(self, connection_id):
        return self._public(self._row(connection_id))

    def list(self):
        return [self._public(row) for row in self.journal.list(NAMESPACE, self.scope)]

    def configure(self, connection_id, *, kind, endpoint, credential_ref=None, options=None, expected_revision=0):
        identifier(connection_id)
        if len(connection_id) > 64:
            raise CoreError("invalid_connection", "Connection identity cannot exceed 64 characters")
        identifier(kind)
        endpoint_origin = origin(endpoint)
        if "?" in endpoint or endpoint_origin.startswith(("ws:", "wss:")):
            raise CoreError("invalid_connection", "Connection endpoints require HTTP URLs without query credentials")
        if credential_ref is not None and (not isinstance(credential_ref, str) or not credential_ref or len(credential_ref) > 256
                                            or not self.vault.current(self.scope, credential_ref)):
            raise CoreError("invalid_credential", "Connection requires a current scoped credential reference")
        options = object_body({} if options is None else options,
            {"allowed_origins", "sender", "max_skew"} | self._option_fields.get(kind, set()))
        allowed = options.get("allowed_origins", [endpoint_origin])
        if not isinstance(allowed, list) or not 1 <= len(allowed) <= 32:
            raise CoreError("invalid_connection", "Connection origins must be an explicit bounded list")
        allowed = sorted(set(exact_origin(item) for item in allowed))
        if endpoint_origin not in allowed:
            raise CoreError("invalid_connection", "Endpoint origin must be included in the connection grant")
        if "sender" in options:
            identifier(options["sender"])
        if "max_skew" in options and (type(options["max_skew"]) is not int or not 1 <= options["max_skew"] <= 900):
            raise CoreError("invalid_connection", "Webhook timestamp tolerance must be 1 to 900 seconds")
        if self._option_checks.get(kind):
            self._option_checks[kind](options)
        body = {"kind": kind, "endpoint": endpoint, "credential_ref": credential_ref,
                "options": {**options, "allowed_origins": allowed}, "grants": [], "active": True, "probe": None}
        with self.journal.transaction():
            row = self.journal.put(NAMESPACE, connection_id, self.scope, body, expected_revision=expected_revision)
            self.journal.append(self.scope, "connection.configured", {"connection_id": connection_id, "revision": row.revision}, source="connections")
        self.register_tools()
        return self._public(row)

    def _change(self, connection_id, expected_revision, **updates):
        with self.journal.transaction():
            row = self._row(connection_id)
            row = self.journal.put(NAMESPACE, connection_id, self.scope, {**row.value, **updates}, expected_revision=expected_revision)
            self.journal.append(self.scope, "connection.changed", {"connection_id": connection_id, "revision": row.revision}, source="connections")
        self.register_tools()
        return self._public(row)

    def grant(self, connection_id, operations, *, expected_revision):
        row = self._row(connection_id)
        available = {"receive"} if row.value["kind"] == "webhook" else set(self._adapters.get(row.value["kind"], ({}, None))[0])
        if (not isinstance(operations, list) or not operations or len(operations) > 64
                or any(not isinstance(item, str) for item in operations) or not set(operations) <= available):
            raise CoreError("invalid_grant", "Grant must name operations provided by this adapter")
        return self._change(connection_id, expected_revision, grants=sorted(set(operations)), active=True, probe=None)

    def revoke(self, connection_id, *, expected_revision):
        return self._change(connection_id, expected_revision, grants=[], active=False, probe=None)

    async def _probe_http(self, record):
        secret = await asyncio.to_thread(self.vault.resolve, self.scope, record["credential_ref"]) if record["credential_ref"] else None
        response = await self.network.request("GET", record["endpoint"], allowed_origins=record["options"]["allowed_origins"],
            headers={"Authorization": "Bearer " + secret} if secret else {}, max_bytes=min(self.network.max_bytes, 8192))
        return {"ok": 200 <= response.status < 300, "kind": "http_response", "status": response.status}

    async def probe(self, connection_id, *, expected_revision):
        row = await asyncio.to_thread(self._row, connection_id)
        if row.revision != expected_revision or not row.value["active"]:
            raise CoreError("stale_connection", "Probe requires the current active connection revision")
        reference = row.value["credential_ref"]
        if reference and not await asyncio.to_thread(self.vault.current, self.scope, reference):
            raise CoreError("stale_credential", "Connection credential has changed")
        record = public_record(row)
        if record["kind"] == "webhook":
            result = {"ok": bool(reference), "kind": "local_signing_configuration"}
        else:
            adapter = self._adapters.get(record["kind"])
            if adapter is None:
                raise CoreError("adapter_unavailable", "No executable adapter is installed for this connection")
            try:
                result = await adapter[1](record)
                if not isinstance(result, dict) or type(result.get("ok")) is not bool or len(canonical_json(result).encode()) > 65536:
                    raise CoreError("invalid_probe", "Adapter returned invalid readiness evidence")
            except CoreError as exc:
                result = {"ok": False, "kind": "error", "error": {"code": exc.code}}
        def persist():
            with self.journal.transaction():
                if reference and not self.vault.current(self.scope, reference):
                    raise CoreError("stale_credential", "Credential changed during the readiness probe")
                return self._change(connection_id, expected_revision, probe={**result, "revision": expected_revision + 1})
        return await asyncio.to_thread(persist)

    def pinned(self, connection_id, revision, operation):
        row = self.get(connection_id)
        if str(row["revision"]) != str(revision) or row["status"] != "ready" or operation not in row["grants"]:
            raise CoreError("stale_connection", "Execution requires the current ready connection grant", effects="none")
        return row

    def capabilities(self):
        return tuple("connection:" + row["id"] + ":" + operation for row in self.list() if row["status"] == "ready"
                     and row["kind"] != "webhook" for operation in row["grants"])

    def register_tools(self):
        # ToolDispatcher's final registry lookup is inside this same transaction.
        # A configuration update therefore cannot remove an entry during claim.
        with self.journal.transaction():
            self._register_tools()

    def _register_tools(self):
        for key in self._registered:
            self.core.tools.registry.pop(key, None)
        self._registered.clear()
        for row in self.list():
            if row["status"] != "ready" or row["kind"] not in self._adapters:
                continue
            operations = self._adapters[row["kind"]][0]
            for name in row["grants"]:
                operation = operations[name]
                async def execute(arguments, context, pinned=row, op=operation):
                    secret = await asyncio.to_thread(self.vault.resolve, self.scope, pinned["credential_ref"]) if pinned["credential_ref"] else None
                    return await op.execute(arguments, context, pinned, secret)
                tool = "connection." + row["id"] + "." + name
                version = str(row["revision"])
                self.core.tools.register(tool, execute, input_schema=operation.input_schema, output_schema=operation.output_schema,
                    version=version, required_capabilities=("connection:" + row["id"] + ":" + name,), read_only=operation.read_only,
                    timeout=operation.timeout, output_limit=operation.output_limit)
                self._registered.add((tool, version))

    async def _fetch(self, arguments, context, record, secret):
        response = await self.network.request("GET", arguments.get("url", record["endpoint"]),
            allowed_origins=record["options"]["allowed_origins"],
            headers={"Authorization": "Bearer " + secret} if secret and origin(arguments.get("url", record["endpoint"])) == origin(record["endpoint"]) else {},
            max_bytes=min(65536, self.network.max_bytes))
        return ExecutorResult({"status": response.status, "url": response.url,
            "media_type": response.headers.get("content-type", "application/octet-stream"),
            "body": base64.b64encode(response.body).decode(), "encoding": "base64"},
            outcome="success" if 200 <= response.status < 300 else "execution_error", effects="none")
