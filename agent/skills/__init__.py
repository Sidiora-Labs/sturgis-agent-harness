"""Scoped procedure packages, evidence-backed candidates and explicit activation."""

import hashlib
from pathlib import Path

from agent.plugin_sdk import identifier
from agent.protocol import CoreError, canonical_json, new_id, strict_loads


def _manifest(value):
    required = {"id", "version", "description", "triggers", "steps", "required_tools"}
    optional = {"platforms", "preconditions", "validation", "known_failures", "stop_conditions", "inputs"}
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - optional:
        raise CoreError("invalid_skill", "Skill fields do not match the versioned contract")
    identifier(value["id"])
    identifier(value["version"])
    if not isinstance(value["description"], str) or not value["description"].strip():
        raise CoreError("invalid_skill", "Skill purpose must be explicit")
    for key in (required | optional) - {"id", "version", "description"}:
        if key in value and (not isinstance(value[key], list) or any(not isinstance(x, str) or not x for x in value[key])):
            raise CoreError("invalid_skill", "Procedure fields must be lists of nonempty strings")
    if not value["steps"] or not value["triggers"] or len(canonical_json(value).encode()) > 65536:
        raise CoreError("invalid_skill", "Skill requires bounded steps and triggers")
    return strict_loads(canonical_json(value))


def _digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


class SkillRegistry:
    def __init__(self, journal, roots=None, *, require_review=False):
        self.journal = journal
        self.roots = {} if roots is None else {kind: Path(path).resolve(strict=True) for kind, path in roots.items()}
        if set(self.roots) - {"built_in", "global", "workspace"}:
            raise CoreError("invalid_scope", "Unknown skill discovery root")
        self.require_review = require_review

    def install(self, scope, manifest, *, source="authored", evidence=()):
        manifest = _manifest(manifest)
        with self.journal.transaction():
            prior = self.journal.get("skills", manifest["id"], scope)
            if prior and prior.value["sha256"] == _digest(manifest):
                raise CoreError("duplicate", "Identical skill revision already exists")
            value = {"manifest": manifest, "sha256": _digest(manifest), "source": source,
                     "evidence": list(evidence), "state": "available", "previous": prior.value if prior else None}
            row = self.journal.put("skills", manifest["id"], scope, value, expected_revision=prior.revision if prior else 0)
            self.journal.append(scope, "skill.published", {"skill_id": row.id, "revision": row.revision,
                                "sha256": value["sha256"]}, source="skills")
            return row

    def discover(self, scope):
        effective = {}
        for kind in ("built_in", "global", "workspace"):
            if kind not in self.roots:
                continue
            paths = sorted(self.roots[kind].glob("*.skill.json"))
            if len(paths) > 1000:
                raise CoreError("skill_limit", "Skill discovery exceeds its package limit")
            for path in paths:
                if path.is_symlink() or path.stat().st_size > 65536:
                    raise CoreError("invalid_skill", "Skill source is unsafe or oversized")
                manifest = _manifest(strict_loads(path.read_bytes()))
                effective[manifest["id"]] = {"manifest": manifest, "sha256": _digest(manifest),
                    "source": str(path), "origin": kind, "revision": None, "state": "available"}
        for row in self.journal.list("skills", scope):
            effective[row.id] = {**row.value, "origin": "profile", "revision": row.revision}
        return [value for _, value in sorted(effective.items())]

    def select(self, scope, task, ready_tools, *, platform="", max_skills=4, max_bytes=8192):
        if not isinstance(task, str) or not task.strip() or max_skills < 1 or max_bytes < 1:
            raise CoreError("invalid_selection", "Task and positive selection budgets are required")
        selected, excluded, candidates, used = [], [], [], 0
        for package in self.discover(scope):
            manifest = package["manifest"]
            score = sum(trigger.casefold() in task.casefold() for trigger in manifest["triggers"])
            reason = ("inactive" if package["state"] != "available" else
                      "platform" if manifest.get("platforms") and platform not in manifest["platforms"] else
                      "unavailable_tools" if set(manifest["required_tools"]) - set(ready_tools) else
                      "irrelevant" if not score else None)
            if reason:
                excluded.append({"id": manifest["id"], "reason": reason})
            else:
                candidates.append((score, package))
        for score, package in sorted(candidates, key=lambda x: (-x[0], x[1]["manifest"]["id"])):
            size = len(canonical_json(package["manifest"]).encode())
            if len(selected) >= max_skills or used + size > max_bytes:
                excluded.append({"id": package["manifest"]["id"], "reason": "context_budget"})
            else:
                selected.append({**package, "score": score})
                used += size
        return {"selected": selected, "excluded": excluded, "used_bytes": used}

    def set_enabled(self, scope, skill_id, enabled):
        with self.journal.transaction():
            row = self.journal.get("skills", skill_id, scope)
            if row is None or type(enabled) is not bool:
                raise CoreError("invalid_skill", "A stored skill and boolean state are required")
            updated = self.journal.put("skills", skill_id, scope, {**row.value,
                "state": "available" if enabled else "withdrawn"}, expected_revision=row.revision)
            self.journal.append(scope, "skill.changed", {"skill_id": skill_id, "enabled": enabled}, source="skills")
            return updated

    def synthesize(self, scope, manifest, evidence_event_ids):
        manifest = _manifest(manifest)
        evidence = set(evidence_event_ids)
        events = {event.id: event for event in self.journal.events(scope, limit=100000)}
        if len(evidence) < 2 or len(evidence) != len(evidence_event_ids) or not evidence <= events.keys():
            raise CoreError("invalid_evidence", "Synthesis requires distinct scoped outcome evidence")
        for key in evidence:
            event = events[key]
            if (event.kind != "tool.result" or event.payload.get("outcome") != "success"
                    or event.payload.get("state") != "completed"
                    or event.payload.get("tool") not in manifest["required_tools"]):
                raise CoreError("invalid_evidence", "Evidence must be successful required-tool observations")
        if any(package["sha256"] == _digest(manifest) for package in self.discover(scope)):
            raise CoreError("duplicate", "Candidate duplicates an existing procedure")
        candidate = {"manifest": manifest, "sha256": _digest(manifest), "evidence": sorted(evidence),
                     "state": "awaiting_review" if self.require_review else "ready", "review": None}
        return self.journal.put("skill_candidates", new_id("candidate"), scope, candidate)

    def review(self, scope, candidate_id, *, approved, reviewer):
        with self.journal.transaction():
            row = self.journal.get("skill_candidates", candidate_id, scope)
            if row is None or row.value["state"] != "awaiting_review" or not reviewer or type(approved) is not bool:
                raise CoreError("invalid_state", "Candidate is not awaiting a valid review")
            return self.journal.put("skill_candidates", candidate_id, scope, {**row.value,
                "state": "ready" if approved else "rejected", "review": {"reviewer": reviewer, "approved": approved}}, expected_revision=row.revision)

    def activate(self, scope, candidate_id):
        with self.journal.transaction():
            row = self.journal.get("skill_candidates", candidate_id, scope)
            if row is None or row.value["state"] != "ready" or row.value["sha256"] != _digest(row.value["manifest"]):
                raise CoreError("invalid_state", "Candidate is not eligible or its content changed")
            skill = self.install(scope, row.value["manifest"], source="learned", evidence=row.value["evidence"])
            return self.journal.put("skill_candidates", candidate_id, scope, {**row.value,
                "state": "activated", "activated_revision": skill.revision}, expected_revision=row.revision)

    def rollback(self, scope, skill_id):
        with self.journal.transaction():
            row = self.journal.get("skills", skill_id, scope)
            if row is None:
                raise CoreError("not_found", "Skill is not stored in this scope")
            prior = row.value["previous"]
            value = prior if prior is not None else {**row.value, "state": "withdrawn"}
            updated = self.journal.put("skills", skill_id, scope, value, expected_revision=row.revision)
            self.journal.append(scope, "skill.rolled_back", {"skill_id": skill_id, "revision": updated.revision}, source="skills")
            return updated
