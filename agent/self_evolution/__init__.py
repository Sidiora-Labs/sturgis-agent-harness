"""Scoped source generations with independent evidence, CAS activation and reversal."""

import asyncio
from dataclasses import asdict, dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil

from agent.meta_harness import SourceSnapshot, copy_snapshot, source_snapshot
from agent.protocol import CoreError, canonical_json, new_id


def _path(value):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise CoreError("invalid_path", "A bounded relative source path is required")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) == "." or len(value) > 1024:
        raise CoreError("invalid_path", "Source path must remain in the granted root")
    return path.as_posix()


def _within(path, prefix):
    return path == prefix or path.startswith(prefix + "/")


@dataclass(frozen=True)
class EvolutionAuthority:
    source_root: str
    scopes: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    protected_paths: tuple[str, ...] = ()
    allow_activation: bool = False
    auto_rollback: bool = True
    state_schema: str = "1"

    def __post_init__(self):
        root = Path(self.source_root).resolve(strict=True)
        if (not root.is_dir() or any(not isinstance(values, (tuple, list)) for values in (self.scopes, self.allowed_paths, self.protected_paths))
                or any(not isinstance(scope, str) or not scope for scope in self.scopes)
                or type(self.allow_activation) is not bool or type(self.auto_rollback) is not bool
                or not isinstance(self.state_schema, str) or not self.state_schema):
            raise CoreError("invalid_authority", "Evolution requires explicit scoped installation authority")
        object.__setattr__(self, "source_root", str(root))
        object.__setattr__(self, "scopes", tuple(self.scopes))
        object.__setattr__(self, "allowed_paths", tuple(_path(path) for path in self.allowed_paths))
        object.__setattr__(self, "protected_paths", tuple(_path(path) for path in self.protected_paths))


@dataclass(frozen=True)
class SourceEdit:
    path: str
    content: bytes | None
    expected_sha256: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "path", _path(self.path))
        if (self.content is not None and (not isinstance(self.content, bytes) or len(self.content) > 1048576)
                or self.expected_sha256 is not None and (not isinstance(self.expected_sha256, str) or len(self.expected_sha256) != 64)):
            raise CoreError("invalid_edit", "Source edit needs bounded bytes and an exact optional preimage")


class SelfEvolution:
    PHASES = ("build", "shadow", "canary")

    def __init__(self, journal, root, harness, *, authority=None):
        if authority is not None and not isinstance(authority, EvolutionAuthority):
            raise CoreError("invalid_authority", "Authority must be installed by runtime code")
        self.journal, self.harness, self.authority = journal, harness, authority
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root == harness.root or self.root in harness.root.parents or harness.root in self.root.parents:
            raise CoreError("invalid_authority", "Staging and evaluator roots must be separate")
        if authority is not None:
            source = Path(authority.source_root)
            if (source == self.root or source in self.root.parents or self.root in source.parents
                    or source == harness.root or source in harness.root.parents or harness.root in source.parents):
                raise CoreError("invalid_authority", "Source, staging and evaluator roots must be separate")
        self.policy_hash = hashlib.sha256(canonical_json(asdict(authority) if authority else {}).encode()).hexdigest()
        self._locks = {}

    def _authorize(self, scope, *, activation=False):
        if self.authority is None or scope not in self.authority.scopes or not self.authority.allowed_paths:
            raise CoreError("authority_denied", "No source evolution authority is granted for this scope")
        if activation and not self.authority.allow_activation:
            raise CoreError("activation_denied", "Code activation is not enabled by installation authority")

    def _evidence(self, scope, ids):
        if not ids or len(ids) > 128 or len(set(ids)) != len(ids):
            raise CoreError("invalid_evidence", "Distinct scoped evidence references are required")
        existing = {event.id: event for event in self.journal.events(scope, limit=100000)}
        if not set(ids) <= existing.keys():
            raise CoreError("scope_denied", "Evolution evidence is outside this scope")
        return [existing[key] for key in ids]

    def active(self, scope):
        selected = self.journal.get("behavior_active", "selected", scope)
        if selected is None:
            return None
        generation = self.journal.get("behavior_generations", selected.value["generation_id"], scope)
        if generation is None:
            raise CoreError("integrity", "Selected behavior generation is missing")
        snapshot = SourceSnapshot.from_dict(generation.value["snapshot"])
        if source_snapshot(snapshot.path).sha256 != snapshot.sha256:
            raise CoreError("integrity", "Selected behavior generation was changed")
        return generation

    def _baseline(self, scope):
        active = self.active(scope)
        if active:
            if active.value["policy_hash"] != self.policy_hash:
                raise CoreError("policy_changed", "Existing generations use a different installation policy")
            return active
        snapshot = source_snapshot(self.authority.source_root)
        generation_id = new_id("generation")
        copied = copy_snapshot(snapshot, self.root / "generations" / generation_id)
        value = {"snapshot": copied.as_dict(), "parent_generation": None, "candidate_id": None,
                 "policy_hash": self.policy_hash, "state_schema": self.authority.state_schema,
                 "status": "baseline", "evaluation_ids": []}
        with self.journal.transaction():
            if self.journal.get("behavior_active", "selected", scope):
                raise CoreError("conflict", "Another baseline was published concurrently")
            row = self.journal.put("behavior_generations", generation_id, scope, value)
            self.journal.put("behavior_active", "selected", scope, {"generation_id": generation_id})
            self.journal.append(scope, "behavior.baseline", {"generation_id": generation_id,
                                "source_hash": copied.sha256}, source="self_evolution")
            return row

    def get_candidate(self, scope, candidate_id):
        row = self.journal.get("evolution_candidates", candidate_id, scope)
        if row is None:
            raise CoreError("not_found", "Candidate is not present in this scope")
        return row

    def _update(self, scope, candidate_id, changes):
        with self.journal.transaction():
            row = self.get_candidate(scope, candidate_id)
            updated = self.journal.put("evolution_candidates", candidate_id, scope, {**row.value, **changes}, expected_revision=row.revision)
            self.journal.append(scope, "evolution.changed", {"candidate_id": candidate_id, "status": updated.value["status"],
                                "revision": updated.revision}, source="self_evolution")
            return updated

    def stage(self, scope, edits, *, rationale, evidence_event_ids, expected_parent=None, state_schema=None):
        self._authorize(scope)
        self._evidence(scope, evidence_event_ids)
        if (not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 8192
                or not isinstance(edits, (tuple, list)) or not edits or len(edits) > 128
                or any(not isinstance(edit, SourceEdit) for edit in edits)
                or len({edit.path for edit in edits}) != len(edits)):
            raise CoreError("invalid_proposal", "A bounded rationale and unique typed edits are required")
        for edit in edits:
            path = _path(edit.path)
            if (not any(_within(path, allowed) for allowed in self.authority.allowed_paths)
                    or any(_within(path, protected) for protected in self.authority.protected_paths)):
                raise CoreError("protected_surface", "Edit is outside the permitted source surface", path=path)
        if state_schema is not None and state_schema != self.authority.state_schema:
            raise CoreError("incompatible_state", "This source-generation backend cannot migrate persistent state schemas")
        baseline = self._baseline(scope)
        if expected_parent is not None and baseline.id != expected_parent:
            raise CoreError("conflict", "Parent behavior generation changed")
        source = SourceSnapshot.from_dict(baseline.value["snapshot"])
        preimages = dict(source.files)
        for edit in edits:
            if preimages.get(edit.path) != edit.expected_sha256 or edit.content is None and edit.path not in preimages:
                raise CoreError("precondition", "Source edit preimage changed", path=edit.path)
        candidate_id = new_id("candidate")
        destination = self.root / "candidates" / candidate_id
        copy_snapshot(source, destination)
        try:
            for edit in edits:
                target = destination / edit.path
                if edit.content is None:
                    target.unlink()
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open("wb") as stream:
                        stream.write(edit.content)
                        stream.flush()
                        os.fsync(stream.fileno())
            snapshot = source_snapshot(destination, snapshot_id=candidate_id)
            for directory in [*(path for path in destination.rglob("*") if path.is_dir()), destination]:
                fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            if snapshot.sha256 == source.sha256:
                raise CoreError("no_change", "Candidate does not change source behavior")
            value = {"snapshot": snapshot.as_dict(), "base_generation": baseline.id, "base_hash": source.sha256,
                     "policy_hash": self.policy_hash, "harness_policy_hash": self.harness.policy_hash,
                     "rationale": rationale, "evidence_event_ids": list(evidence_event_ids),
                     "changed_paths": [edit.path for edit in edits], "state_schema": self.authority.state_schema,
                     "status": "staged", "evaluations": {}, "pending": None}
            with self.journal.transaction():
                row = self.journal.put("evolution_candidates", candidate_id, scope, value)
                self.journal.append(scope, "evolution.staged", {"candidate_id": candidate_id, "source_hash": snapshot.sha256,
                                    "base_generation": baseline.id}, source="self_evolution")
                return row
        except BaseException:
            shutil.rmtree(destination)
            raise

    def _admissible(self, row):
        if row.value["policy_hash"] != self.policy_hash or row.value["harness_policy_hash"] != self.harness.policy_hash:
            raise CoreError("policy_changed", "Candidate authority or evaluator policy changed")
        snapshot = SourceSnapshot.from_dict(row.value["snapshot"])
        if source_snapshot(snapshot.path).sha256 != snapshot.sha256:
            raise CoreError("source_changed", "Candidate source changed after staging")
        return snapshot

    async def evaluate(self, scope, candidate_id, *, phase, work_id, generation, cancel=None):
        self._authorize(scope)
        async with self._locks.setdefault((scope, candidate_id), asyncio.Lock()):
            row = await asyncio.to_thread(self.get_candidate, scope, candidate_id)
            if row.value["status"] not in {"staged", "verified", "shadowed", "interrupted"} or phase not in self.PHASES:
                raise CoreError("invalid_state", "Candidate is not eligible for this evaluation")
            for earlier in self.PHASES[:self.PHASES.index(phase)]:
                if earlier not in row.value["evaluations"]:
                    raise CoreError("invalid_state", "Earlier independent evaluation is missing")
            if phase in row.value["evaluations"]:
                raise CoreError("invalid_state", "This candidate phase already has a recorded passing evaluation")
            snapshot = await asyncio.to_thread(self._admissible, row)
            evaluation = await asyncio.to_thread(self.harness.prepare, scope, snapshot, phase)
            await asyncio.to_thread(self._update, scope, candidate_id, {"status": "evaluating",
                "pending": {"phase": phase, "evaluation_id": evaluation.id}})
            evaluated = await self.harness.run(scope, evaluation.id, work_id=work_id, generation=generation, cancel=cancel)
            evaluations = dict(row.value["evaluations"])
            if self.harness.verified(evaluated):
                evaluations[phase] = evaluated.id
                status = {"build": "verified", "shadow": "shadowed", "canary": "eligible"}[phase]
            else:
                status = "interrupted" if evaluated.value["status"] == "interrupted" else "rejected"
            return await asyncio.to_thread(self._update, scope, candidate_id, {"status": status,
                        "evaluations": evaluations, "pending": None, "last_evaluation": evaluated.id})

    def activate(self, scope, candidate_id, *, expected_generation):
        self._authorize(scope, activation=True)
        row = self.get_candidate(scope, candidate_id)
        active = self.active(scope)
        if (row.value["status"] == "activated" and active and active.value["candidate_id"] == candidate_id
                and active.value["parent_generation"] == expected_generation):
            return active
        if (row.value["status"] != "eligible" or active is None or active.id != expected_generation
                or row.value["base_generation"] != active.id):
            raise CoreError("invalid_state", "Activation requires an eligible candidate based on the current generation")
        snapshot = self._admissible(row)
        for phase in self.PHASES:
            evaluation_id = row.value["evaluations"].get(phase)
            evaluated = self.harness.get_evaluation(scope, evaluation_id)
            if (not self.harness.verified(evaluated) or evaluated.value["policy_hash"] != self.harness.policy_hash
                    or evaluated.value["phase"] != phase or evaluated.value["snapshot"]["sha256"] != snapshot.sha256):
                raise CoreError("invalid_evidence", "Activation requires exact independently evaluated source")
        if row.value["state_schema"] != active.value["state_schema"]:
            raise CoreError("incompatible_state", "Generation state schemas are incompatible")
        generation_id = new_id("generation")
        copied = copy_snapshot(snapshot, self.root / "generations" / generation_id)
        with self.journal.transaction():
            selected = self.journal.get("behavior_active", "selected", scope)
            current = self.get_candidate(scope, candidate_id)
            if selected.value["generation_id"] != expected_generation or current.revision != row.revision:
                raise CoreError("conflict", "Activation lost its parent or candidate revision")
            value = {"snapshot": copied.as_dict(), "parent_generation": expected_generation, "candidate_id": candidate_id,
                     "policy_hash": self.policy_hash, "state_schema": row.value["state_schema"], "status": "published",
                     "evaluation_ids": list(row.value["evaluations"].values())}
            published = self.journal.put("behavior_generations", generation_id, scope, value)
            self.journal.put("behavior_active", "selected", scope, {"generation_id": generation_id}, expected_revision=selected.revision)
            self._update(scope, candidate_id, {"status": "activated", "generation_id": generation_id})
            self.journal.append(scope, "behavior.activated", {"generation_id": generation_id, "candidate_id": candidate_id,
                                "source_hash": copied.sha256}, source="self_evolution")
            return published

    def _reverse(self, scope, expected_generation, target_id, reason, evidence_event_ids):
        self._authorize(scope)
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 8192:
            raise CoreError("invalid_reversal", "A bounded reversal reason is required")
        if evidence_event_ids:
            self._evidence(scope, evidence_event_ids)
        active = self.active(scope)
        target = self.journal.get("behavior_generations", target_id, scope)
        if active is None or active.id != expected_generation or target is None or active.id == target.id:
            raise CoreError("conflict", "Reversal generation changed or has no predecessor")
        snapshot = SourceSnapshot.from_dict(target.value["snapshot"])
        if target.value["state_schema"] != active.value["state_schema"] or source_snapshot(snapshot.path).sha256 != snapshot.sha256:
            raise CoreError("incompatible_state", "Prior source/state generation cannot be restored")
        with self.journal.transaction():
            selected = self.journal.get("behavior_active", "selected", scope)
            current_target = self.journal.get("behavior_generations", target_id, scope)
            if selected.value["generation_id"] != expected_generation or current_target is None or current_target.revision != target.revision:
                raise CoreError("conflict", "Reversal lost its selected or target generation")
            self.journal.put("behavior_active", "selected", scope, {"generation_id": target.id}, expected_revision=selected.revision)
            if active.value["candidate_id"]:
                self._update(scope, active.value["candidate_id"], {"status": "rolled_back"})
            self.journal.append(scope, "behavior.rolled_back", {"from_generation": active.id, "to_generation": target.id,
                "reason": reason, "evidence_event_ids": list(evidence_event_ids)}, source="self_evolution")
            return target

    def rollback(self, scope, *, expected_generation, reason="Observed regression", evidence_event_ids=()):
        active = self.active(scope)
        return self._reverse(scope, expected_generation, active.value["parent_generation"] if active else None,
                             reason, evidence_event_ids)

    def restore_baseline(self, scope, *, expected_generation, reason="Restore installation baseline"):
        active = self.active(scope)
        if active is None:
            raise CoreError("not_found", "There is no selected generation")
        target, seen = active, set()
        while target.value["parent_generation"] is not None:
            if target.id in seen or len(seen) > 1000:
                raise CoreError("integrity", "Generation ancestry is cyclic or oversized")
            seen.add(target.id)
            target = self.journal.get("behavior_generations", target.value["parent_generation"], scope)
            if target is None:
                raise CoreError("integrity", "Generation ancestor is missing")
        return self._reverse(scope, expected_generation, target.id, reason, ())

    def observe(self, scope, generation_id, evidence_event_ids):
        self._authorize(scope)
        evidence = self._evidence(scope, evidence_event_ids)
        if any(event.kind != "behavior.observed" or event.source != "runtime"
               or event.payload.get("generation_id") != generation_id
               or event.payload.get("status") not in {"healthy", "regression"} for event in evidence):
            raise CoreError("invalid_evidence", "Watchdog requires runtime observations attributed to this exact generation")
        active = self.active(scope)
        if active is None or active.id != generation_id:
            raise CoreError("conflict", "Observation belongs to a different active generation")
        regression = any(event.payload["status"] == "regression" for event in evidence)
        self.journal.append(scope, "behavior.assessed", {"generation_id": generation_id, "regression": regression,
                            "evidence_event_ids": list(evidence_event_ids)}, source="self_evolution")
        if regression and self.authority.auto_rollback:
            return self.rollback(scope, expected_generation=generation_id, evidence_event_ids=evidence_event_ids)
        return active

    def recover(self, scope):
        self._authorize(scope)
        self.harness.recover(scope)
        recovered = []
        for row in self.journal.list("evolution_candidates", scope):
            if row.value["status"] not in {"evaluating", "interrupted", "verified", "shadowed", "eligible"}:
                continue
            evaluations = {}
            for phase in self.PHASES:
                evaluation_id = row.value["evaluations"].get(phase)
                if not evaluation_id or not self.harness.verified(self.harness.get_evaluation(scope, evaluation_id)):
                    break
                evaluations[phase] = evaluation_id
            pending = row.value["pending"]
            if pending:
                evaluated = self.harness.get_evaluation(scope, pending["evaluation_id"])
                if self.harness.verified(evaluated):
                    evaluations[pending["phase"]] = evaluated.id
            status = "eligible" if "canary" in evaluations else "shadowed" if "shadow" in evaluations else "verified" if "build" in evaluations else "staged"
            changes = {"status": status, "pending": None, "evaluations": evaluations}
            if any(row.value.get(key) != value for key, value in changes.items()):
                recovered.append(self._update(scope, row.id, changes))
        return recovered
