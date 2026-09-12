"""Strict, bounded consumer contracts shared by HTTP, CLI and private IPC."""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from agent.protocol import CoreError, canonical_json

VERSION = "consumer/1"
MAX_BODY = 8_388_608
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}\Z", re.ASCII)


def identifier(value: Any) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise CoreError("invalid_identifier", "Expected a bounded ASCII identifier")
    return value


def object_body(value: Any, allowed, required=()) -> dict:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise CoreError("invalid_request", "Expected a JSON object")
    if set(value) - set(allowed) or set(required) - set(value):
        raise CoreError("invalid_request", "Request contains unknown or missing fields")
    if len(canonical_json(value).encode()) > MAX_BODY:
        raise CoreError("body_too_large", "Request exceeds the byte limit")
    return dict(value)


def public_record(record) -> dict:
    """Project a non-secret record; authoritative metadata cannot be overwritten."""
    if not isinstance(record.value, Mapping):
        raise CoreError("invalid_record", "Record body must be an object")
    return {**record.value, "id": record.id, "revision": record.revision}


def success(data: Any, request_id: str | None = None) -> dict:
    if request_id is not None:
        identifier(request_id)
    canonical_json(data)
    return {"version": VERSION, "request_id": request_id,
            "data": {} if data is None else data, "error": None}


def failure(error: Exception, request_id: str | None = None) -> dict:
    if request_id is not None:
        identifier(request_id)
    # Unhandled exceptions may contain credentials, paths or response bodies.
    safe = error.as_dict() if isinstance(error, CoreError) else {
        "code": "internal_error", "message": "The request could not be completed", "details": {}}
    safe = {"code": str(safe["code"])[:128], "message": str(safe["message"])[:512],
            "details": safe.get("details", {})}
    if len(canonical_json(safe).encode()) > 4096:
        safe["details"] = {"truncated": True}
    return {"version": VERSION, "request_id": request_id, "data": None, "error": safe}
