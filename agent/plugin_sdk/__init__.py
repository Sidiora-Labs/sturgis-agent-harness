"""Versioned extension declarations and a bounded one-request worker protocol."""

from dataclasses import asdict, dataclass, field
import re
import sys

from agent.framing import FrameCodec, encode_frame
from agent.protocol import CoreError, canonical_json, strict_loads


def _check_schema(schema):
    from jsonschema import Draft202012Validator, SchemaError

    pending = [schema]
    count = 0
    while pending:
        value = pending.pop()
        count += 1
        if count > 10000:
            raise CoreError("invalid_schema", "Schema exceeds its structural bound")
        if isinstance(value, dict):
            for key in ("$ref", "$dynamicRef"):
                if key in value and (not isinstance(value[key], str) or not value[key].startswith("#")):
                    raise CoreError("invalid_schema", "Only local schema references are supported")
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    try:
        Draft202012Validator.check_schema(schema)
    except (SchemaError, RecursionError) as exc:
        raise CoreError("invalid_schema", "Invalid capability schema") from exc


def validate_schema(schema: dict, value, *, label="payload"):
    from jsonschema import Draft202012Validator, ValidationError

    _check_schema(schema)
    try:
        Draft202012Validator(schema).validate(value)
    except ValidationError as exc:
        raise CoreError("validation", f"Invalid {label}", path=list(exc.path), reason=exc.message) from exc
    except RecursionError as exc:
        raise CoreError("validation", "Recursive schema could not establish validity") from exc
    except Exception as exc:
        raise CoreError("validation", "Schema reference could not establish validity") from exc


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise CoreError("invalid_manifest", "Invalid package or operation identity")
    return value


@dataclass(frozen=True)
class Capability:
    name: str
    input_schema: dict
    output_schema: dict
    required_capabilities: tuple[str, ...] = ()
    read_only: bool = False
    timeout: float = 30.0
    output_limit: int = 1048576

    def __post_init__(self):
        identifier(self.name)
        if (type(self.timeout) not in {int, float} or not 0 < self.timeout <= 3600
                or type(self.output_limit) is not int or not 1 <= self.output_limit <= 16777216
                or type(self.read_only) is not bool):
            raise CoreError("invalid_manifest", "Invalid operation limits")
        if not isinstance(self.input_schema, dict) or not isinstance(self.output_schema, dict):
            raise CoreError("invalid_manifest", "Operation schemas must be objects")
        _check_schema(self.input_schema)
        _check_schema(self.output_schema)
        if not isinstance(self.required_capabilities, (tuple, list)) or any(
                not isinstance(x, str) or not x for x in self.required_capabilities):
            raise CoreError("invalid_manifest", "Capabilities must be nonempty names")
        canonical_json(asdict(self))

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) - set(cls.__dataclass_fields__):
            raise CoreError("invalid_manifest", "Unknown operation descriptor fields")
        try:
            return cls(**value)
        except TypeError as exc:
            raise CoreError("invalid_manifest", "Missing operation descriptor fields") from exc

    def as_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class PluginManifest:
    id: str
    version: str
    entry: str
    tools: tuple[Capability, ...] = ()
    commands: tuple[Capability, ...] = ()
    state_schema: int = 1
    accepts_state_schemas: tuple[int, ...] = (1,)
    required_grants: tuple[str, ...] = ()
    protocol_version: str = "plugin/1"
    initial_state: dict = field(default_factory=dict)

    def __post_init__(self):
        from pathlib import PurePosixPath

        identifier(self.id)
        identifier(self.version)
        path = PurePosixPath(self.entry) if isinstance(self.entry, str) else None
        if (not path or "\x00" in self.entry or path.is_absolute() or ".." in path.parts or str(path) == "."
                or self.protocol_version != "plugin/1" or type(self.state_schema) is not int
                or self.state_schema < 1 or not isinstance(self.initial_state, dict)):
            raise CoreError("invalid_manifest", "Invalid entry, protocol, or state schema")
        if any(type(n) is not int or n < 1 for n in self.accepts_state_schemas):
            raise CoreError("invalid_manifest", "Invalid accepted state schemas")
        if set(self.required_grants) - {"workspace.read", "workspace.write"}:
            raise CoreError("unsupported_grant", "This backend supports rooted workspace grants only")
        for descriptors in (self.tools, self.commands):
            if any(not isinstance(x, Capability) for x in descriptors) or len({x.name for x in descriptors}) != len(descriptors):
                raise CoreError("invalid_manifest", "Duplicate or invalid operation descriptors")
            if any(set(x.required_capabilities) - set(self.required_grants) for x in descriptors):
                raise CoreError("invalid_manifest", "Operation requires undeclared grants")
        canonical_json(self.as_dict())

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) - set(cls.__dataclass_fields__):
            raise CoreError("invalid_manifest", "Unknown manifest fields")
        value = dict(value)
        try:
            for key in ("tools", "commands"):
                value[key] = tuple(Capability.from_dict(x) for x in value.get(key, []))
            return cls(**value)
        except (TypeError, ValueError) as exc:
            raise CoreError("invalid_manifest", "Malformed manifest") from exc

    def as_dict(self):
        return asdict(self)

    def operation(self, kind, name):
        descriptors = self.tools if kind == "tool" else self.commands if kind == "command" else ()
        for descriptor in descriptors:
            if descriptor.name == name:
                return descriptor
        raise CoreError("unknown_operation", "Plugin operation is not declared")


def serve(handler, *, reader=None, writer=None, max_frame=1048576):
    """Serve one framed request; plugin code owns handler, never host authority."""
    reader = reader if reader is not None else sys.stdin.buffer
    writer = writer if writer is not None else sys.stdout.buffer
    codec = FrameCodec(max_frame)
    frames = []
    while chunk := reader.read(65536):
        frames.extend(codec.feed(chunk))
        if len(frames) > 1:
            raise CoreError("invalid_request", "A worker accepts exactly one request")
    codec.finish()
    if len(frames) != 1:
        raise CoreError("invalid_request", "Missing worker request")
    request = strict_loads(frames[0])
    required = {"protocol_version", "request_id", "operation", "target", "arguments", "state", "state_schema"}
    if not isinstance(request, dict) or set(request) != required or request["protocol_version"] != "plugin/1":
        raise CoreError("invalid_request", "Invalid worker protocol")
    result = handler(request)
    if not isinstance(result, dict) or set(result) != {"data", "state", "state_schema"}:
        raise CoreError("invalid_result", "Worker must return data and explicit state")
    response = {"protocol_version": "plugin/1", "request_id": request["request_id"], **result}
    writer.write(encode_frame(canonical_json(response).encode(), max_frame))
    writer.flush()
