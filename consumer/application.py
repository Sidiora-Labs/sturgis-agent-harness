"""Private application composition; core owners retain inference and effect authority."""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import inspect
import time
from dataclasses import asdict

from agent.channel_core import InboundMessage
from agent.context import ContextBlock
from agent.protocol import CoreError, canonical_json, new_id
from agent.runtime import CoreRuntime, TERMINAL
from agent.tool_runner import ExecutorResult
from consumer.content import ConsumerData
from consumer.contracts import identifier, object_body, public_record
from consumer.network import NetworkPolicy
from consumer.providers import ProviderSettings
from consumer.usage import UsageView
from consumer.vault import CredentialVault
from consumer.connections import ConnectionService
from consumer.connectors import install_connectors
from consumer.device import DeviceService
from consumer.browser import BrowserBackend
from consumer.permissions import Permissions
from consumer.work import WorkViews
from consumer.webhooks import WebhookIngress


def _text(value, *, maximum=65536, empty=False):
    if not isinstance(value, str) or len(value.encode()) > maximum or (not empty and not value.strip()):
        raise CoreError("invalid_request", "Expected bounded text")
    return value


def _bytes(data_base64):
    if not isinstance(data_base64, str) or len(data_base64) > 6_000_000:
        raise CoreError("invalid_content", "Expected bounded base64 content")
    try:
        return base64.b64decode(data_base64, validate=True)
    except (ValueError, binascii.Error):
        raise CoreError("invalid_content", "Content is not valid base64") from None


class _ConsumerChannel:
    async def send(self, item):
        # A durable outbox is available for replay. This is not a browser receipt.
        return {"status": "sent_unconfirmed", "transport": "consumer_event_log"}


class AgentApplication:
    def __init__(self, config):
        self.config, self.scope = config, config.scope
        self.core = CoreRuntime(config.root, scope=config.scope, workspace=config.workspace,
            memory_binary=config.native_binary, context_sources=self._source_blocks,
            capabilities=("content.read", "workspace.read"), allow_text_only_provider=True)
        self.journal = self.core.journal
        self.network = NetworkPolicy(private_origins=config.private_network_origins)
        self.vault = CredentialVault(config.private_root / "vault")
        self.data = ConsumerData(self.core)
        self.providers = ProviderSettings(self.core, self.vault, self.network)
        if config.gateway_endpoint:
            from consumer.network import origin
            self.providers.network_policy = NetworkPolicy(private_origins=(*config.private_network_origins, origin(config.gateway_endpoint)))
        self.providers.install()
        self.core.prepare_provider = self.providers.ensure_selected_ready
        self.usage = UsageView(self.core)
        self.connections = ConnectionService(self.core, self.vault, self.network)
        self.browser = BrowserBackend(config.browser_binary) if config.browser_binary else None
        self.connectors = install_connectors(self.connections, browser_backend=self.browser)
        self.devices = DeviceService(self.connections)
        self.webhooks = WebhookIngress(self.connections)
        self.connections.register_tools()
        self.core.adapters.register("consumer", _ConsumerChannel())
        self._register_content_tools()
        self.permissions = Permissions(self.core)
        self.permissions.install()
        self.work = WorkViews(self.core)
        self._refresh_capabilities()
        self._running, self._accepting = False, True
        self._messages_lock = asyncio.Lock()
        self._plugin_changes = asyncio.Lock()
        self._gateway_binding_lock = asyncio.Lock()
        self.commands = {}
        self._register_commands()

    def bind_command(self, name, fn, allowed=(), required=()):
        if name in self.commands:
            raise CoreError("duplicate_command", "Application command is already registered")
        self.commands[name] = (fn, tuple(allowed), tuple(required))

    def _register_commands(self):
        def add(name, fn, allowed="", required=""):
            self.bind_command(name, fn, allowed.split(), required.split())
        add("overview", self.overview)
        add("conversations.create", self.conversation_create, "title")
        add("conversations.list", self.conversation_list, "query include_archived")
        add("conversations.get", self.conversation_get, "id", "id")
        add("conversations.update", self.conversation_update, "id expected_revision title archived", "id expected_revision")
        add("messages.send", self.message_send, "conversation_id message_id text attachments kind", "conversation_id message_id text")
        add("messages.cancel", self.message_cancel, "conversation_id message_id", "conversation_id message_id")
        add("messages.events", self.events, "conversation_id after limit")
        add("attachments.upload", self.upload, "name media_type data_base64 operation_id", "name media_type data_base64 operation_id")
        add("attachments.list", self.data.attachments, "limit")
        add("attachments.get", self.data.attachment, "id", "id")
        add("attachments.read", self.data.read_attachment, "id offset limit", "id")
        add("files.list", self.data.files, "path")
        add("files.read", self.data.read_file, "path", "path")
        add("files.write", self.file_write, "path data_base64 expected_sha256", "path data_base64")
        add("files.delete", self.data.delete_file, "path expected_sha256", "path expected_sha256")
        add("knowledge.add", self.data.knowledge_add, "attachment_id title operation_id document_id expected_revision", "attachment_id title operation_id")
        add("knowledge.list", self.data.knowledge_list, "query limit")
        add("knowledge.get", self.data.knowledge_get, "id revision", "id")
        add("knowledge.delete", self.data.knowledge_delete, "id expected_revision operation_id", "id expected_revision operation_id")
        add("memory.query", self.data.memory_query, "text concepts limit embedding at")
        add("memory.get", self.data.memory_get, "id revision", "id")
        add("memory.correct", self.data.memory_correct, "id content expected_revision operation_id", "id content expected_revision operation_id")
        add("memory.retract", self.data.memory_retract, "id reason revision operation_id", "id reason operation_id")
        add("providers.configure", self.providers.configure, "provider_id endpoint model context_limit output_limit tools credential_ref expected_revision", "provider_id endpoint model context_limit output_limit")
        add("providers.list", self.providers.inspect)
        add("providers.inspect", self.providers.inspect, "provider_id")
        add("providers.probe", self.providers.probe, "provider_id expected_revision", "provider_id expected_revision")
        add("providers.activate", self.providers.activate, "provider_id expected_revision", "provider_id expected_revision")
        add("providers.credentials.put", self.credential_put, "name secret expected_revision", "name secret")
        add("providers.credentials.list", lambda: self.vault.list(self.scope))
        add("providers.credentials.revoke", self.credential_revoke, "name expected_revision", "name expected_revision")
        add("gateway.bind", self.bind_gateway, "endpoint api_key model context_limit output_limit tools", "endpoint api_key model context_limit output_limit tools")
        add("connections.configure", self.connection_configure, "id kind endpoint credential_ref options expected_revision", "id kind endpoint")
        add("connections.list", self.connections.list)
        add("connections.get", lambda id: self.connections.get(id), "id", "id")
        add("connections.grant", self.connection_grant, "id operations expected_revision", "id operations expected_revision")
        add("connections.probe", self.connection_probe, "id expected_revision", "id expected_revision")
        add("connections.revoke", self.connection_revoke, "id expected_revision", "id expected_revision")
        add("skills.list", self.skill_list)
        add("skills.install", self.skill_install, "manifest", "manifest")
        add("skills.enable", self.skill_enable, "id enabled expected_revision", "id enabled expected_revision")
        add("skills.rollback", self.skill_rollback, "id expected_revision", "id expected_revision")
        add("usage.summary", self.usage.summary)
        self._register_domain_commands(add)

    def _register_domain_commands(self, add):
        # The dispatch table is explicit. Signatures provide strict field names;
        # no user-selected attribute lookup or caller-selected scope is performed.
        domain = {
            "work.overview": self.work.overview, "work.list": self.work.list_work, "work.get": self.work.get_work,
            "work.cancel": self.work.cancel_work, "goals.list": self.work.list_goals,
            "goals.create": self.work.create_goal, "goals.edit": self.work.edit_goal,
            "goals.transition": self.work.transition_goal, "goals.archive": self.work.archive_goal,
            "commitments.list": self.work.list_commitments, "commitments.create": self.work.create_commitment,
            "commitments.transition": self.work.transition_commitment, "commitments.progress": self.work.commitment_progress,
            "commitments.wait": self.work.wait_commitment, "commitments.resume": self.work.resume_commitment,
            "schedules.list": self.work.list_schedules, "schedules.create": self.work.create_schedule,
            "schedules.state": self.work.schedule_state, "workflows.list": self.work.list_workflows,
            "workflows.get": self.work.get_workflow, "workflows.create": self.work.create_workflow,
            "workflows.revise": self.work.revise_workflow, "workflows.run": self.work.run_workflow,
            "workflows.state": self.work.workflow_state, "workflows.step": self.work.workflow_step,
            "children.list": self.work.list_children, "children.get": self.work.get_child,
            "children.cancel": self.work.cancel_child, "children.delegate": self.work.delegate,
            "inbox.list": self.work.inbox, "inbox.decide": self.work.decide_inbox,
            "initiative.propose": self.work.propose_initiative, "attention.policy": self.work.attention_policy,
            "permissions.list": self.permissions.list, "permissions.pending": self.permissions.pending,
            "permissions.request": self.permissions.request, "permissions.set": self.permissions.set,
            "permissions.approve": self.permissions.approve, "permissions.deny": self.permissions.deny,
            "devices.list": self.devices.list, "devices.get": self.devices.get,
            "devices.pair": self.devices.pair, "devices.revoke": self.device_revoke,
            "ingress.device.claim": self.device_claim, "ingress.device.poll": self.devices.poll,
            "ingress.device.sync": self.devices.sync,
        }
        for name, fn in domain.items():
            signature = inspect.signature(fn)
            allowed = [p.name for p in signature.parameters.values() if p.name != "reviewer"]
            required = [p.name for p in signature.parameters.values() if p.default is inspect.Parameter.empty]
            self.bind_command(name, fn, allowed, required)
        add("ingress.webhook", self.webhook_admit, "connection_id body_base64 timestamp nonce signature", "connection_id body_base64 timestamp nonce signature")
        add("ingress.device.respond", self.device_respond, "connection_id body_base64 timestamp nonce signature", "connection_id body_base64 timestamp nonce signature")
        add("artifacts.read", self.artifact_read, "id offset limit", "id")
        add("diagnostics.get", self.diagnostics)
        add("plugins.list", self.plugin_list)
        add("plugins.inspect", self.plugin_inspect, "id", "id")
        add("plugins.health", self.plugin_health, "id", "id")
        for action in ("activate", "disable", "rollback", "uninstall"):
            async def change(id, expected_revision, operation=action):
                return await self.plugin_change(id, expected_revision, operation)
            add("plugins." + action, change, "id expected_revision", "id expected_revision")

    async def start(self):
        await self.core.start()
        self._running = True
        await self.work.start()
        # Close the consumer receipt -> core admission crash window using stable ids.
        pending = await asyncio.to_thread(self.journal.list, "consumer.messages", self.scope)
        for row in pending:
            if row.value["status"] == "accepted":
                try:
                    await self.message_send(**row.value["request"])
                except CoreError as error:
                    def failed():
                        current = self.journal.get(row.namespace, row.id, self.scope)
                        self.journal.put(row.namespace, row.id, self.scope,
                            {**current.value, "status": "failed", "error": error.as_dict()}, current.revision)
                    await asyncio.to_thread(failed)
        return self

    async def close(self):
        self._accepting = False
        await self.work.close()
        await self.core.close()
        self._running = False

    async def command(self, method, params, request_id=None):
        if request_id is not None:
            identifier(request_id)
        if not self._running or not self._accepting:
            raise CoreError("runtime_unavailable", "Private agent admission is paused")
        if not isinstance(method, str) or method not in self.commands:
            raise CoreError("unknown_command", "Unsupported consumer command")
        fn, allowed, required = self.commands[method]
        params = object_body(params, allowed, required)
        value = await fn(**params) if inspect.iscoroutinefunction(fn) else await asyncio.to_thread(fn, **params)
        canonical_json(value)
        return value

    def _conversation(self, id, *, write=False):
        row = self.journal.get("consumer.conversations", identifier(id), self.scope)
        if row is None:
            raise CoreError("not_found", "Conversation is unavailable")
        if write and row.value["archived"]:
            raise CoreError("conversation_archived", "Conversation is archived")
        return row

    def conversation_create(self, title="New conversation"):
        _text(title, maximum=240)
        with self.journal.transaction():
            id = new_id("conversation")
            binding = self.core.channels.bind(self.scope, "consumer", self.scope, id, "owner",
                capabilities={"replay": True, "receipts": False})
            return public_record(self.journal.put("consumer.conversations", id, self.scope, {
                "title": title, "binding_id": binding.id, "archived": False, "created_at": time.time()}))

    def conversation_list(self, query="", include_archived=False):
        _text(query, maximum=1024, empty=True)
        if type(include_archived) is not bool:
            raise CoreError("invalid_request", "Archive filter must be boolean")
        rows = [r for r in self.journal.list("consumer.conversations", self.scope)
                if include_archived or not r.value["archived"]]
        if query:
            matched = {r.value["conversation_id"] for r in self.journal.list("consumer.messages", self.scope)
                       if query.casefold() in r.value["request"]["text"].casefold()}
            rows = [r for r in rows if r.id in matched or query.casefold() in r.value["title"].casefold()]
        return {"items": [public_record(r) for r in reversed(rows)]}

    def conversation_get(self, id):
        row = self._conversation(id)
        turns = [public_record(r) for r in self.journal.list("runtime_turns", self.scope)
                 if r.value["binding_id"] == row.value["binding_id"]]
        messages = [public_record(r) for r in self.journal.list("consumer.messages", self.scope)
                    if r.value["conversation_id"] == id]
        return {**public_record(row), "messages": messages, "turns": turns}

    def conversation_update(self, id, expected_revision, title=None, archived=None):
        if title is None and archived is None:
            raise CoreError("invalid_request", "A conversation change is required")
        with self.journal.transaction():
            row = self._conversation(id)
            body = dict(row.value)
            if title is not None:
                body["title"] = _text(title, maximum=240)
            if archived is not None:
                if type(archived) is not bool:
                    raise CoreError("invalid_request", "Archive state must be boolean")
                body["archived"] = archived
            return public_record(self.journal.put(row.namespace, id, self.scope, body, expected_revision))

    async def message_send(self, conversation_id, message_id, text, attachments=None, kind="prompt"):
        identifier(conversation_id); identifier(message_id)
        _text(text, empty=True)
        if kind not in {"prompt", "steer", "cancel"}:
            raise CoreError("invalid_request", "Unsupported message kind")
        attachments = [] if attachments is None else attachments
        sources = await self.data.attachment_context(attachments)
        if not text and not attachments and kind != "cancel":
            raise CoreError("invalid_request", "A message requires text or an attachment")
        request = {"conversation_id": conversation_id, "message_id": message_id, "text": text,
                   "attachments": attachments, "kind": kind}
        digest = hashlib.sha256(canonical_json(request).encode()).hexdigest()
        key = hashlib.sha256(canonical_json([conversation_id, message_id]).encode()).hexdigest()
        async with self._messages_lock:
            def reserve():
                with self.journal.transaction():
                    conversation = self._conversation(conversation_id, write=True)
                    row = self.journal.get("consumer.messages", key, self.scope)
                    if row:
                        if row.value["digest"] != digest:
                            raise CoreError("conflict", "Message identity was reused with different content")
                        return row, conversation
                    row = self.journal.put("consumer.messages", key, self.scope, {
                        "message_id": message_id, "conversation_id": conversation_id, "digest": digest,
                        "request": request, "status": "accepted", "event_id": None, "work_id": None,
                        "accepted_at": time.time()})
                    self.journal.put("consumer.input_sources", key, self.scope, sources)
                    return row, conversation
            row, conversation = await asyncio.to_thread(reserve)
            if row.value["status"] == "admitted":
                return public_record(row)
            if row.value["status"] == "historical":
                raise CoreError("historical_message", "Imported messages are historical evidence and cannot be replayed")
            event = await self.core.accept(InboundMessage(self.scope, conversation.value["binding_id"],
                "owner", key, text, kind, tuple(attachments)))
            def finish():
                with self.journal.transaction():
                    current = self.journal.get("consumer.messages", key, self.scope)
                    routed = self.journal.get("runtime_inputs", event.id, self.scope)
                    return public_record(self.journal.put(current.namespace, key, self.scope, {
                        **current.value, "status": "admitted", "event_id": event.id,
                        "work_id": routed.value["work_id"]}, current.revision))
            return await asyncio.to_thread(finish)

    async def message_cancel(self, conversation_id, message_id):
        return await self.message_send(conversation_id, message_id, "", kind="cancel")

    def _source_blocks(self, event):
        row = self.journal.get("consumer.input_sources", event.payload.get("delivery_id", ""), self.scope)
        result = []
        for source in row.value["sources"] if row else []:
            result.append(ContextBlock("attachment:" + source["id"], self.scope, "tool_observation",
                canonical_json(source), origin="uploaded_document"))
        selected = self.core.skills.select(self.scope, event.payload.get("text") or "attachments",
            [d["name"] for d in self.core.tools.descriptors()])
        for skill in selected["selected"]:
            result.append(ContextBlock("skill:" + skill["manifest"]["id"], self.scope, "conversation",
                canonical_json(skill["manifest"]), revision=skill["revision"] or 1, origin="reusable_procedure"))
        return result

    def events(self, conversation_id=None, after=0, limit=100):
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise CoreError("invalid_cursor", "Event cursor and page limit must be bounded integers")
        binding = self._conversation(conversation_id).value["binding_id"] if conversation_id else None
        records = self.journal.events(self.scope, after=after, limit=limit)
        public = []
        for event in records:
            if event.kind not in {"user.prompt", "user.steer", "user.cancel", "output.persisted", "runtime.turn_completed", "runtime.turn_failed", "runtime.turn_interrupted"}:
                continue
            event_binding = event.payload.get("binding_id")
            if event_binding is None and event.payload.get("work_id"):
                row = self.journal.get("runtime_turns", event.payload["work_id"], self.scope)
                event_binding = row.value["binding_id"] if row else None
            if binding and event_binding != binding:
                continue
            public.append({"id": event.id, "cursor": event.seq, "kind": event.kind, "origin": event.source, "payload": event.payload})
        return {"items": public, "cursor": records[-1].seq if records else after, "has_more": len(records) == limit}

    def overview(self):
        return {"agent_id": self.config.agent_id, "mode": self.config.mode,
                "provider_settings": self.providers.inspect(), "usage": self.usage.summary(),
                "conversations": len(self.journal.list("consumer.conversations", self.scope)),
                "memory": {"available": self.core.memory.available}, "accepting": self._accepting,
                "tools": self.core.tools.descriptors()}

    async def upload(self, name, media_type, data_base64, operation_id):
        return await self.data.upload(name, media_type, _bytes(data_base64), operation_id=operation_id)

    async def file_write(self, path, data_base64, expected_sha256=None):
        return await self.data.write_file(path, _bytes(data_base64), expected_sha256=expected_sha256)

    async def bind_gateway(self, endpoint, api_key, model, context_limit, output_limit, tools):
        """Select a model through the operator gateway without upstream credentials."""
        if endpoint != self.config.gateway_endpoint or not self.config.gateway_endpoint:
            raise CoreError("forbidden", "Gateway bindings must use the operator-configured gateway")
        if not isinstance(api_key, str) or not api_key.startswith("stg_") or len(api_key) > 256:
            raise CoreError("invalid_credential", "A restricted gateway client key is required")
        async with self._gateway_binding_lock:
            provider_id = "gateway_" + hashlib.sha256((api_key + model).encode()).hexdigest()[:24]
            def prepare():
                credential = self.vault.get(self.scope, provider_id)
                if credential is None or not credential["active"]:
                    credential = self.vault.put(self.scope, provider_id, api_key, credential["revision"] if credential else 0)
                existing = self.journal.get("consumer.providers", provider_id, self.scope)
                if existing:
                    return self.providers.inspect(provider_id)
                return self.providers.configure(provider_id, endpoint=endpoint, model=model,
                    context_limit=context_limit, output_limit=output_limit, tools=tools,
                    credential_ref=credential["reference"])
            configured = await asyncio.to_thread(prepare)
            if not configured["ready"]:
                configured = await self.providers.probe(provider_id, configured["revision"])
            if not configured["ready"]:
                raise CoreError("provider_unavailable", "The gateway could not complete a model check. Open Model Gateway to check the connection and retry.")
            return await asyncio.to_thread(self.providers.activate, provider_id, configured["revision"])

    def credential_put(self, name, secret, expected_revision=0):
        result = self.vault.put(self.scope, name, secret, expected_revision)
        self.connections.register_tools()
        self._refresh_capabilities()
        return result

    def credential_revoke(self, name, expected_revision):
        result = self.vault.revoke(self.scope, name, expected_revision)
        self.connections.register_tools()
        self._refresh_capabilities()
        return result

    def connection_configure(self, id, **params):
        result = self.connections.configure(id, **params)
        self._refresh_capabilities()
        return result

    def connection_grant(self, id, operations, expected_revision):
        result = self.connections.grant(id, operations, expected_revision=expected_revision)
        self._refresh_capabilities()
        return result

    def connection_revoke(self, id, expected_revision):
        result = self.connections.revoke(id, expected_revision=expected_revision)
        self._refresh_capabilities()
        return result

    async def connection_probe(self, id, expected_revision):
        result = await self.connections.probe(id, expected_revision=expected_revision)
        self._refresh_capabilities()
        return result

    def _refresh_capabilities(self):
        if hasattr(self, "permissions"):
            self.permissions.refresh_capabilities()
        else:
            self.core.capabilities = tuple(sorted({"content.read", "workspace.read", *self.connections.capabilities()}))

    def skill_install(self, manifest):
        return public_record(self.core.skills.install(self.scope, manifest))

    def skill_list(self):
        return [{**{key: value for key, value in row.items() if key != "source"},
                 "id": row["manifest"]["id"]} for row in self.core.skills.discover(self.scope)]

    def _skill_revision(self, id, expected_revision):
        row = self.journal.get("skills", identifier(id), self.scope)
        if row is None:
            raise CoreError("not_found", "Skill is unavailable")
        if type(expected_revision) is not int or row.revision != expected_revision:
            raise CoreError("conflict", "Skill revision changed")

    def skill_enable(self, id, enabled, expected_revision):
        with self.journal.transaction():
            self._skill_revision(id, expected_revision)
            return public_record(self.core.skills.set_enabled(self.scope, id, enabled))

    def skill_rollback(self, id, expected_revision):
        with self.journal.transaction():
            self._skill_revision(id, expected_revision)
            return public_record(self.core.skills.rollback(self.scope, id))

    def _register_content_tools(self):
        async def read_content(arguments, context):
            if context.constraints.get("delegated"):
                raise CoreError("scope_denied", "Parent content access is unavailable in delegated work", effects="none")
            if arguments["kind"] == "attachment":
                result = await self.data.read_attachment(arguments["id"], offset=arguments.get("offset", 0), limit=arguments.get("limit", 16384))
            else:
                document = await self.data.knowledge_get(arguments["id"])
                if document["deleted"]:
                    raise CoreError("content_retracted", "Knowledge document was retracted", effects="none")
                raw = document["text"].encode(); offset = arguments.get("offset", 0)
                end = min(offset + arguments.get("limit", 16384), len(raw))
                if offset > len(raw):
                    raise CoreError("invalid_content", "Content offset exceeds its length", effects="none")
                result = {"id": document["id"], "revision": document["revision"], "data_base64": base64.b64encode(raw[offset:end]).decode(), "start": offset, "end": end, "total_bytes": len(raw)}
            return ExecutorResult(result, effects="none")
        async def read_workspace(arguments, context):
            if context.constraints.get("delegated"):
                raise CoreError("scope_denied", "Parent workspace access is unavailable in delegated work", effects="none")
            result = await self.data.files(arguments["path"]) if arguments["operation"] == "list" else await self.data.read_file(arguments["path"])
            return ExecutorResult(result, effects="none")
        async def write_workspace(arguments, context):
            if context.constraints.get("delegated"):
                raise CoreError("scope_denied", "Parent workspace access is unavailable in delegated work", effects="none")
            if arguments["operation"] == "delete":
                result = await self.data.delete_file(arguments["path"], expected_sha256=arguments.get("expected_sha256"))
            else:
                result = await self.file_write(arguments["path"], arguments["data_base64"], arguments.get("expected_sha256"))
            return ExecutorResult(result, effects="observed")
        common = {"type": "object", "additionalProperties": False}
        self.core.tools.register("content.read", read_content, input_schema={**common, "required": ["kind", "id"], "properties": {"kind": {"enum": ["attachment", "knowledge"]}, "id": {"type": "string"}, "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 32768}}}, output_schema={"type": "object"}, required_capabilities=["content.read"], read_only=True, output_limit=65536)
        self.core.tools.register("workspace.read", read_workspace, input_schema={**common, "required": ["operation", "path"], "properties": {"operation": {"enum": ["list", "read"]}, "path": {"type": "string"}}}, output_schema={"type": "object"}, required_capabilities=["workspace.read"], read_only=True)
        self.core.tools.register("workspace.write", write_workspace, input_schema={**common, "required": ["operation", "path"], "properties": {"operation": {"enum": ["write", "delete"]}, "path": {"type": "string"}, "data_base64": {"type": "string"}, "expected_sha256": {"type": ["string", "null"]}}}, output_schema={"type": "object"}, required_capabilities=["workspace.write"])

    async def prepare_suspend(self):
        self._accepting = False
        await self.work.pause()
        await self.core.pause_admission()

    async def resume_admission(self):
        await self.core.resume_admission()
        await self.work.resume()
        self._accepting = True

    def webhook_admit(self, connection_id, body_base64, timestamp, nonce, signature):
        return asdict(self.webhooks.admit(connection_id, _bytes(body_base64), timestamp=timestamp, nonce=nonce, signature=signature))

    def device_respond(self, connection_id, body_base64, timestamp, nonce, signature):
        return self.devices.respond(connection_id, _bytes(body_base64), timestamp=timestamp, nonce=nonce, signature=signature)

    def artifact_read(self, id, offset=0, limit=65536):
        identifier(id)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 4 * 1024 * 1024:
            raise CoreError("invalid_content", "Artifact ranges must be bounded")
        data = self.core.artifacts.get(self.scope, id)
        if offset > len(data):
            raise CoreError("invalid_content", "Artifact offset exceeds its length")
        end = min(offset + limit, len(data))
        return {"id": id, "data_base64": base64.b64encode(data[offset:end]).decode(), "start": offset,
                "end": end, "total_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                "complete": offset == 0 and end == len(data)}

    def device_revoke(self, connection_id, expected_revision):
        result = self.devices.revoke(connection_id, expected_revision=expected_revision)
        self._refresh_capabilities()
        return result

    def device_claim(self, connection_id, pairing_token, device_id):
        result = self.devices.claim(connection_id, pairing_token=pairing_token, device_id=device_id)
        self._refresh_capabilities()
        return result

    def diagnostics(self):
        return {"scope": self.scope, "paused": self.core._admission_paused,
                "native_memory_available": self.core.memory.available,
                "recent_failures": [{"id": e.id, "kind": e.kind, "payload": e.payload}
                    for e in self.journal.events(self.scope, limit=10000)
                    if e.kind in {"runtime.worker_failed", "runtime.turn_failed", "work.interrupted"}][-20:],
                "capabilities": list(self.core.capabilities), "mode": self.config.mode}

    def plugin_inspect(self, id):
        row = self.core.plugins.inspect(self.scope, identifier(id))
        body = public_record(row)
        body["versions"] = {key: {k: v for k, v in version.items() if k != "path"}
                            for key, version in body["versions"].items()}
        return body

    def plugin_list(self):
        return [self.plugin_inspect(r.id) for r in self.journal.list("plugins", self.scope)]

    async def plugin_health(self, id):
        return await self.core.plugins.health(self.scope, identifier(id))

    async def plugin_change(self, id, expected_revision, operation):
        async with self._plugin_changes:
            if type(expected_revision) is not int or expected_revision < 1:
                raise CoreError("conflict", "Plugin revision must be a positive integer")
            row = await asyncio.to_thread(self.core.plugins.inspect, self.scope, identifier(id))
            if row.revision != expected_revision:
                raise CoreError("conflict", "Plugin revision changed")
            actions = {"activate": self.core.plugins.activate, "disable": self.core.plugins.disable,
                       "rollback": self.core.plugins.rollback, "uninstall": self.core.plugins.uninstall}
            fn = actions[operation]
            await fn(self.scope, id) if inspect.iscoroutinefunction(fn) else await asyncio.to_thread(fn, self.scope, id)
            for key in list(self.core.tools.registry):
                if key[0].startswith("plugin." + id + "."):
                    self.core.tools.registry.pop(key)
            current = await asyncio.to_thread(self.core.plugins.inspect, self.scope, id)
            if current.value["status"] == "active":
                self.core.plugins.register_tools(self.core.tools, self.scope, id)
            self._refresh_capabilities()
            return self.plugin_inspect(id)


    async def lifecycle_snapshot(self):
        def read():
            with self.journal.transaction():
                rows = self.journal.list("work", self.scope)
                outbox = self.journal.list("outbox", self.scope)
                due = [r.value["next_due"] for r in self.journal.list("schedules", self.scope)
                       if r.value["state"] == "active" and r.value["next_due"] is not None]
                return {"active_work": sum(r.value["state"] == "running" for r in rows),
                    "queued_work": sum(r.value["state"] in {"queued", "waiting"} for r in rows),
                    "pending_deliveries": sum(r.value["state"] in {"pending", "retry", "claimed"} for r in outbox),
                    "next_wake_at": min(due) if due else None}
        return await asyncio.to_thread(read)
