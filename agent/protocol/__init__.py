"""Strict canonical values shared by every core module."""

from dataclasses import asdict, dataclass, field
import json
from typing import Any
from uuid import uuid4


class CoreError(Exception):
    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code, self.details = code, details

    def as_dict(self) -> dict:
        return {"code": self.code, "message": str(self), "details": self.details}


def new_id(prefix: str) -> str:
    if not isinstance(prefix, str) or not prefix or not prefix.replace("_", "").isalnum():
        raise CoreError("invalid_id", "ID prefix must be alphanumeric")
    return f"{prefix}_{uuid4().hex}"


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise CoreError("invalid_json", "Value is not finite JSON") from exc


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CoreError("invalid_json", "Duplicate JSON field", field=key)
        result[key] = value
    return result


def _constant(value):
    raise CoreError("invalid_json", "Nonfinite JSON number", value=value)


def strict_loads(text: str | bytes) -> Any:
    try:
        value = json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)
        canonical_json(value)
        return value
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise CoreError("invalid_json", "Malformed JSON") from exc


def validate_scope(scope: str) -> str:
    if not isinstance(scope, str) or not scope or len(scope) > 256 or "\x00" in scope:
        raise CoreError("invalid_scope", "A nonempty bounded scope is required")
    return scope


@dataclass(frozen=True)
class Event:
    id: str
    seq: int
    scope: str
    kind: str
    source: str
    payload: dict
    created_at: float


@dataclass(frozen=True)
class ToolCall:
    tool: str
    arguments: dict
    tool_version: str = "1"
    schema_version: str = "1.0"
    expectation: dict = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.tool, str) or not self.tool or len(self.tool) > 256:
            raise CoreError("invalid_call", "Tool name is required")
        if self.schema_version != "1.0" or not isinstance(self.tool_version, str) or not self.tool_version:
            raise CoreError("incompatible", "Unsupported tool contract version")
        if not isinstance(self.arguments, dict) or not isinstance(self.expectation, dict):
            raise CoreError("invalid_call", "Arguments and expectation must be objects")
        canonical_json(self.arguments)
        canonical_json(self.expectation)

    @classmethod
    def from_dict(cls, value: dict):
        if not isinstance(value, dict) or set(value) - {"tool", "arguments", "tool_version", "schema_version", "expectation"}:
            raise CoreError("invalid_call", "Unknown tool invocation fields")
        if not {"tool", "arguments"} <= set(value):
            raise CoreError("invalid_call", "Tool and arguments are required")
        return cls(**value)

    def as_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class ToolResult:
    action_id: str
    attempt_id: str
    tool: str
    state: str
    outcome: str | None
    data: Any = None
    error: dict | None = None
    effects: dict = field(default_factory=lambda: {"certainty": "unknown", "evidence_event_ids": []})
    completeness: dict = field(default_factory=lambda: {"observation_complete": True, "truncated": False, "missing_ranges": []})

    def __post_init__(self):
        valid = {"accepted": {None}, "running": {None}, "completed": {"success", "target_error", "execution_error"}, "rejected": {"rejected"}, "cancelled": {"cancelled"}, "indeterminate": {"unknown"}}
        if self.state not in valid or self.outcome not in valid[self.state]:
            raise CoreError("invalid_result", "Invalid lifecycle/outcome combination")
        if self.effects.get("certainty") not in {"none", "observed", "unknown"}:
            raise CoreError("invalid_result", "Invalid effect certainty")
        canonical_json(asdict(self))

    def as_dict(self):
        return asdict(self)
