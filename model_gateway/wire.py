"""Strict bounded JSON primitives independent of any model client."""
import json

from model_gateway.errors import GatewayError


def json_dumps(value):
    """Serialize deterministic UTF-8 JSON and reject non-finite numbers."""
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    except (ValueError, TypeError, RecursionError):
        raise GatewayError("invalid_json", "Expected a finite JSON value.") from None


def json_loads(value):
    """Reject duplicate keys, non-finite literals and malformed remote JSON."""
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError("duplicate")
            result[key] = item
        return result

    def constant(_):
        raise ValueError("nonfinite")

    try:
        return json.loads(value, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise GatewayError("invalid_json", "The response is not valid unambiguous JSON.") from None
