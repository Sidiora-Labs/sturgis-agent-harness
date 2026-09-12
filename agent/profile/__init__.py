"""Revisioned profile configuration and canonical resource boundaries."""

from pathlib import Path
import hashlib

from agent.awareness import required
from agent.protocol import CoreError, canonical_json, validate_scope
from agent.state import Journal


def _config(config):
    if not isinstance(config, dict) or set(config) & {"self_evolution_authority", "installation_authority", "governing_policy"}:
        raise CoreError("invalid_profile", "Profile configuration cannot confer installation authority")
    canonical_json(config)
    return config


def _roots(workspace_root, resource_roots, *, create=True):
    try:
        workspace = Path(workspace_root).resolve(strict=True)
        if not workspace.is_dir():
            raise CoreError("invalid_root", "Workspace must be a directory")
        resources = resource_roots
        if not isinstance(resources, dict) or set(resources) != {"memory", "schedules"}:
            raise CoreError("invalid_root", "Memory and schedule roots are required")
        resolved = {}
        for name, value in resources.items():
            candidate = Path(value)
            candidate = (candidate if candidate.is_absolute() else workspace / candidate).resolve(strict=not create)
            if candidate == workspace or not candidate.is_relative_to(workspace):
                raise CoreError("scope_denied", "Resource root escapes workspace", resource=name)
            resolved[name] = candidate
        memory, schedules = resolved["memory"], resolved["schedules"]
        if memory.is_relative_to(schedules) or schedules.is_relative_to(memory):
            raise CoreError("invalid_root", "Resource roots must be distinct and non-overlapping")
        if create:
            for candidate in resolved.values():
                candidate.mkdir(parents=True, exist_ok=True)
        elif any(not candidate.is_dir() for candidate in resolved.values()):
            raise CoreError("invalid_root", "Registered resources must remain directories")
        return str(workspace), {name: str(path) for name, path in resolved.items()}
    except (OSError, TypeError, ValueError) as exc:
        raise CoreError("invalid_root", "Profile resource root is unavailable") from exc


class ProfileRegistry:
    def __init__(self, journal: Journal):
        self.journal = journal

    def register(self, scope, workspace_root, *, config=None, resource_roots=None):
        validate_scope(scope)
        config = _config({} if config is None else config)
        if resource_roots is None:
            partition = hashlib.sha256(scope.encode()).hexdigest()
            resource_roots = {name: f".sturgis/profiles/{partition}/{name}" for name in ("memory", "schedules")}
        workspace, resources = _roots(workspace_root, resource_roots)
        with self.journal.transaction():
            body = {"workspace_root": workspace, "resource_roots": resources, "configuration": config, "enabled": True, "origin": "explicit"}
            row = self.journal.put("profiles", scope, scope, body)
            self.journal.append(scope, "profile.registered", {"revision": row.revision, **body}, source="profile")
            return row

    def get(self, scope):
        return required(self.journal, "profiles", scope, scope)

    def update(self, scope, expected_revision, *, config=None, enabled=None, origin="explicit"):
        if origin != "explicit" or (enabled is not None and type(enabled) is not bool):
            raise CoreError("invalid_profile", "Profile changes require explicit configuration and boolean enablement")
        with self.journal.transaction():
            row = self.get(scope)
            body = {**row.value, "configuration": row.value["configuration"] if config is None else _config(config), "enabled": row.value["enabled"] if enabled is None else enabled}
            updated = self.journal.put("profiles", scope, scope, body, expected_revision)
            self.journal.append(scope, "profile.changed", {"revision": updated.revision, **body}, source="profile")
            return updated

    def resources(self, scope):
        row = self.get(scope)
        if not row.value["enabled"]:
            raise CoreError("profile_disabled", "Profile is disabled")
        workspace, resources = _roots(row.value["workspace_root"], row.value["resource_roots"], create=False)
        if workspace != row.value["workspace_root"] or resources != row.value["resource_roots"]:
            raise CoreError("scope_denied", "Profile roots changed after registration")
        return {"workspace": workspace, **resources}
