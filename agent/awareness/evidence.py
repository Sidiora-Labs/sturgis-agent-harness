"""Scoped event checks shared by outcome-owning domains; narratives are not receipts."""

from agent.protocol import CoreError, canonical_json
from . import text


def validate_checks(checks):
    if not isinstance(checks, list) or len(checks) > 128:
        raise CoreError("invalid_checks", "Checks must be a bounded list")
    ids = set()
    for check in checks:
        if not isinstance(check, dict) or set(check) != {"id", "event_kind", "source", "matches"}:
            raise CoreError("invalid_checks", "Check requires id, event_kind, source and matches")
        for name in ("id", "event_kind", "source"):
            text(check[name], name, 256)
        if check["id"] in ids or check["source"] in {"model", "memory", "subagents"} or check["event_kind"].startswith(("model.", "memory.", "child.")):
            raise CoreError("invalid_checks", "Check identity must be unique and source must be operational evidence")
        if not isinstance(check["matches"], dict) or not check["matches"]:
            raise CoreError("invalid_checks", "Checks need explicit expected payload fields")
        for path in check["matches"]:
            text(path, "payload path", 256)
        canonical_json(check["matches"])
        ids.add(check["id"])
    return checks


def resolve_evidence(journal, scope, evidence):
    if not isinstance(evidence, list) or not evidence or len(evidence) > 256:
        raise CoreError("missing_evidence", "Scoped event IDs are required")
    for event_id in evidence:
        text(event_id, "event_id", 256)
    wanted, found, cursor = set(evidence), {}, 0
    while wanted:
        page = journal.events(scope, after=cursor)
        if not page:
            raise CoreError("missing_evidence", "Evidence does not exist in this scope", ids=sorted(wanted))
        for event in page:
            if event.id in wanted:
                found[event.id] = event
                wanted.remove(event.id)
        cursor = page[-1].seq
    return list(found.values())


def _matches(payload, path, expected):
    value = payload
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return False
        value = value[part]
    return canonical_json(value) == canonical_json(expected)


def verify_checks(journal, scope, checks, evidence):
    validate_checks(checks)
    if not checks:
        raise CoreError("missing_checks", "Completion requires explicit outcome checks")
    events = resolve_evidence(journal, scope, evidence)
    results = {}
    for check in checks:
        matches = [event.id for event in events if event.kind == check["event_kind"] and event.source == check["source"] and all(_matches(event.payload, path, expected) for path, expected in check["matches"].items())]
        if not matches:
            raise CoreError("check_failed", "Evidence does not satisfy an outcome check", check=check["id"])
        results[check["id"]] = matches
    return results
