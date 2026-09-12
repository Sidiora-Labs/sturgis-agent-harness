"""Durable extension revisions executed in confined, disposable Python workers."""

import asyncio
import hashlib
from pathlib import Path
import shutil
import sys

from agent.framing import FrameCodec, encode_frame
from agent.plugin_sdk import PluginManifest, validate_schema
from agent.protocol import CoreError, canonical_json, new_id, strict_loads
from agent.tool_runner import ExecutorResult, ProcessRunner


def _tree(root):
    files, size, entries = {}, 0, 0
    for path in root.rglob("*"):
        entries += 1
        if entries > 1000:
            raise CoreError("package_limit", "Plugin package exceeds its entry bound")
        if path.is_symlink():
            raise CoreError("path_escape", "Plugin packages cannot contain symlinks")
        if path.is_file():
            size += path.stat().st_size
            if size > 16777216 or len(files) >= 1000:
                raise CoreError("package_limit", "Plugin package exceeds its bound")
            files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif not path.is_dir():
            raise CoreError("invalid_package", "Plugin packages contain only regular files")
    return hashlib.sha256(canonical_json(files).encode()).hexdigest()


class PluginHost:
    def __init__(self, journal, root, *, workspace_root=None):
        self.journal = journal
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.workspace_root = Path(workspace_root).resolve(strict=True) if workspace_root is not None else None
        self._locks = {}

    def _lock(self, scope, plugin_id):
        return self._locks.setdefault((scope, plugin_id), asyncio.Lock())

    def inspect(self, scope, plugin_id):
        row = self.journal.get("plugins", plugin_id, scope)
        if row is None:
            raise CoreError("not_found", "Plugin is not installed in this scope")
        return row

    def _update(self, scope, plugin_id, changes):
        with self.journal.transaction():
            row = self.inspect(scope, plugin_id)
            value = {**row.value, **changes}
            updated = self.journal.put("plugins", plugin_id, scope, value, expected_revision=row.revision)
            self.journal.append(scope, "plugin.changed", {"plugin_id": plugin_id, "status": value["status"],
                                "active_version": value["active_version"], "revision": updated.revision}, source="plugin_host")
            return updated

    def install(self, scope, package_dir, manifest, *, grants=()):
        manifest = PluginManifest.from_dict(manifest.as_dict() if isinstance(manifest, PluginManifest) else manifest)
        grants = sorted(set(grants))
        if set(grants) - {"workspace.read", "workspace.write"} or not set(manifest.required_grants) <= set(grants):
            raise CoreError("grant_denied", "Plugin requirements exceed configured grants")
        if grants and self.workspace_root is None:
            raise CoreError("grant_denied", "Workspace grants require a configured workspace")
        package = Path(package_dir).resolve(strict=True)
        package_hash = _tree(package)
        if not (package / manifest.entry).is_file():
            raise CoreError("invalid_package", "Plugin entry is missing")
        scope_hash = hashlib.sha256(scope.encode()).hexdigest()
        destination = self.root / scope_hash / manifest.id / manifest.version
        row = self.journal.get("plugins", manifest.id, scope)
        if row and manifest.version in row.value["versions"]:
            raise CoreError("conflict", "Plugin version is already installed")
        if destination.exists():
            raise CoreError("conflict", "An existing package path requires explicit recovery")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copytree(package, destination)
            if _tree(destination) != package_hash:
                raise CoreError("integrity", "Plugin changed during installation")
            version = {"manifest": manifest.as_dict(), "path": str(destination), "sha256": package_hash,
                       "grants": grants, "quarantined": False, "error": None}
            value = row.value if row else {"active_version": None, "selected_version": None, "versions": {},
                    "status": "installed", "state": {}, "state_schema": None, "previous": None, "pending": None}
            value["versions"][manifest.version] = version
            value["selected_version"] = manifest.version
            with self.journal.transaction():
                result = self.journal.put("plugins", manifest.id, scope, value, expected_revision=row.revision if row else 0)
                self.journal.append(scope, "plugin.installed", {"plugin_id": manifest.id, "version": manifest.version,
                                    "sha256": package_hash}, source="plugin_host")
            return result
        except BaseException:
            if destination.exists():
                shutil.rmtree(destination)
            raise

    async def _execute(self, version, operation, target, arguments, state, state_schema, *, cancel=None):
        manifest = PluginManifest.from_dict(version["manifest"])
        package = Path(version["path"])
        if await asyncio.to_thread(_tree, package) != version["sha256"]:
            raise CoreError("integrity", "Installed plugin bytes changed")
        descriptor = manifest.operation(operation, target) if operation in {"tool", "command"} else None
        if descriptor:
            validate_schema(descriptor.input_schema, arguments, label="plugin input")
            if not set(descriptor.required_capabilities) <= set(version["grants"]):
                raise CoreError("grant_denied", "Operation grant is unavailable")
        request_id = new_id("plugin_request")
        request = {"protocol_version": "plugin/1", "request_id": request_id, "operation": operation,
                   "target": target, "arguments": arguments, "state": state, "state_schema": state_schema}
        empty = self.root / "empty"
        empty.mkdir(exist_ok=True)
        root = self.workspace_root if version["grants"] else empty
        mounts = [(package, "/plugin", False), (Path(__file__).resolve().parents[1], "/runtime/agent", False)]
        runner = ProcessRunner(root, [sys.executable], environment_keys=("PYTHONPATH",), mounts=mounts,
                               writable="workspace.write" in version["grants"])
        output_limit = descriptor.output_limit if descriptor else 1048576
        result = await runner.run([sys.executable, "-B", "/plugin/" + manifest.entry],
                                  environment={"PYTHONPATH": "/runtime"},
                                  timeout=descriptor.timeout if descriptor else 30,
                                  output_limit=output_limit + 65536, cancel=cancel,
                                  stdin=encode_frame(canonical_json(request).encode()))
        if result["exit_code"] != 0:
            raise CoreError("plugin_failed", "Plugin worker exited unsuccessfully", exit_code=result["exit_code"],
                            stderr=result["stderr"].decode(errors="replace"), effects="unknown")
        codec = FrameCodec(output_limit + 65536)
        frames = codec.feed(result["stdout"])
        codec.finish()
        if len(frames) != 1:
            raise CoreError("invalid_result", "Plugin must emit exactly one framed result")
        response = strict_loads(frames[0])
        if (not isinstance(response, dict) or set(response) != {"protocol_version", "request_id", "data", "state", "state_schema"}
                or response["protocol_version"] != "plugin/1" or response["request_id"] != request_id
                or not isinstance(response["state"], dict) or type(response["state_schema"]) is not int
                or response["state_schema"] != manifest.state_schema):
            raise CoreError("invalid_result", "Plugin returned an incompatible response or state")
        if descriptor:
            validate_schema(descriptor.output_schema, response["data"], label="plugin output")
        return response

    async def activate(self, scope, plugin_id, version=None):
        async with self._lock(scope, plugin_id):
            row = await asyncio.to_thread(self.inspect, scope, plugin_id)
            target = version or row.value["selected_version"]
            if target not in row.value["versions"]:
                raise CoreError("not_found", "Plugin version is not installed")
            candidate = row.value["versions"][target]
            manifest = PluginManifest.from_dict(candidate["manifest"])
            if candidate["quarantined"]:
                raise CoreError("quarantined", "Quarantined version cannot activate")
            prior = {key: row.value[key] for key in ("active_version", "state", "state_schema")}
            state = prior["state"] if prior["active_version"] else manifest.initial_state
            source_schema = prior["state_schema"] if prior["active_version"] else manifest.state_schema
            if source_schema != manifest.state_schema and source_schema not in manifest.accepts_state_schemas:
                raise CoreError("incompatible_state", "Plugin has no declared state migration")
            await asyncio.to_thread(self._update, scope, plugin_id, {"status": "activating", "pending": {"target": target, "prior": prior}})
            try:
                if source_schema != manifest.state_schema:
                    response = await self._execute(candidate, "migrate", None, {}, state, source_schema)
                    state = response["state"]
                response = await self._execute(candidate, "activate", None, {}, state, manifest.state_schema)
                return await asyncio.to_thread(self._update, scope, plugin_id, {"active_version": target,
                    "status": "active", "state": response["state"], "state_schema": manifest.state_schema,
                    "previous": prior if prior["active_version"] else None, "pending": None})
            except BaseException as exc:
                versions = row.value["versions"]
                versions[target] = {**candidate, "quarantined": True, "error": str(exc)}
                await asyncio.to_thread(self._update, scope, plugin_id, {**prior, "versions": versions,
                    "status": "active" if prior["active_version"] else "quarantined", "pending": None})
                raise

    async def invoke(self, scope, plugin_id, target, arguments, *, kind="tool", version=None, cancel=None):
        async with self._lock(scope, plugin_id):
            row = await asyncio.to_thread(self.inspect, scope, plugin_id)
            selected = row.value["active_version"]
            if row.value["status"] != "active" or version is not None and version != selected:
                raise CoreError("inactive", "Plugin is unavailable at the requested version")
            package = row.value["versions"][selected]
            manifest = PluginManifest.from_dict(package["manifest"])
            descriptor = manifest.operation(kind, target)
            validate_schema(descriptor.input_schema, arguments, label="plugin input")
            await asyncio.to_thread(self._update, scope, plugin_id, {"pending": {"operation": kind, "target": target}})
            try:
                response = await self._execute(package, kind, target, arguments, row.value["state"],
                                               row.value["state_schema"], cancel=cancel)
                await asyncio.to_thread(self._update, scope, plugin_id, {"state": response["state"], "pending": None})
                return response["data"]
            except BaseException as exc:
                await asyncio.to_thread(self._update, scope, plugin_id, {"status": "quarantined", "pending": None, "error": str(exc)})
                raise

    async def health(self, scope, plugin_id):
        async with self._lock(scope, plugin_id):
            row = await asyncio.to_thread(self.inspect, scope, plugin_id)
            if row.value["status"] != "active":
                raise CoreError("inactive", "Health requires an active plugin")
            try:
                return (await self._execute(row.value["versions"][row.value["active_version"]], "health", None, {},
                                           row.value["state"], row.value["state_schema"]))["data"]
            except BaseException as exc:
                await asyncio.to_thread(self._update, scope, plugin_id, {"status": "quarantined", "error": str(exc)})
                raise

    async def disable(self, scope, plugin_id):
        await asyncio.to_thread(self._update, scope, plugin_id, {"status": "draining"})
        async with self._lock(scope, plugin_id):
            row = await asyncio.to_thread(self.inspect, scope, plugin_id)
            if row.value["active_version"] and row.value["status"] == "draining":
                try:
                    response = await self._execute(row.value["versions"][row.value["active_version"]], "deactivate", None, {},
                                                   row.value["state"], row.value["state_schema"])
                    return await asyncio.to_thread(self._update, scope, plugin_id, {"status": "disabled", "state": response["state"]})
                except BaseException as exc:
                    await asyncio.to_thread(self._update, scope, plugin_id, {"status": "quarantined", "error": str(exc)})
                    raise
            return await asyncio.to_thread(self._update, scope, plugin_id, {"status": "disabled"})

    async def drain(self, scope, plugin_id):
        return await self.disable(scope, plugin_id)

    async def rollback(self, scope, plugin_id):
        async with self._lock(scope, plugin_id):
            row = await asyncio.to_thread(self.inspect, scope, plugin_id)
            prior = row.value["previous"]
            if not prior or prior["active_version"] not in row.value["versions"]:
                raise CoreError("invalid_state", "No compatible prior plugin state is retained")
            version = row.value["versions"][prior["active_version"]]
            response = await self._execute(version, "activate", None, {}, prior["state"], prior["state_schema"])
            return await asyncio.to_thread(self._update, scope, plugin_id, {**prior, "state": response["state"],
                "status": "active", "previous": None, "pending": None})

    def uninstall(self, scope, plugin_id):
        row = self.inspect(scope, plugin_id)
        if row.value["status"] not in {"disabled", "installed", "quarantined"} or row.value["pending"]:
            raise CoreError("invalid_state", "Disable and drain plugin before uninstall")
        return self._update(scope, plugin_id, {"status": "uninstalled", "active_version": None})

    def recover(self, scope):
        recovered = []
        for row in self.journal.list("plugins", scope):
            if row.value["pending"] or row.value["status"] in {"activating", "draining"}:
                recovered.append(self._update(scope, row.id, {"status": "quarantined", "pending": None,
                                      "error": "Interrupted plugin operation requires explicit recovery"}))
        return recovered

    def register_tools(self, dispatcher, scope, plugin_id):
        row = self.inspect(scope, plugin_id)
        if row.value["status"] != "active":
            raise CoreError("inactive", "Only active plugins publish capabilities")
        version = row.value["active_version"]
        manifest = PluginManifest.from_dict(row.value["versions"][version]["manifest"])
        for kind, descriptors in (("tool", manifest.tools), ("command", manifest.commands)):
            for descriptor in descriptors:
                async def execute(arguments, context, kind=kind, descriptor=descriptor):
                    if context.scope != scope:
                        raise CoreError("scope_denied", "Plugin registration belongs to another scope", effects="none")
                    data = await self.invoke(scope, plugin_id, descriptor.name, arguments,
                                             kind=kind, version=version, cancel=context.cancel)
                    return ExecutorResult(data, effects="none" if descriptor.read_only else "unknown")
                dispatcher.register(f"plugin.{plugin_id}.{kind}.{descriptor.name}", execute,
                    input_schema=descriptor.input_schema, output_schema=descriptor.output_schema,
                    version=version, required_capabilities=descriptor.required_capabilities,
                    read_only=descriptor.read_only, timeout=descriptor.timeout + 1, output_limit=descriptor.output_limit)
