"""Canonical channel bindings and durable input admission."""
from dataclasses import dataclass, field
import hashlib
from agent.protocol import CoreError, canonical_json, new_id, validate_scope


@dataclass(frozen=True)
class InboundMessage:
    scope: str
    binding_id: str
    participant: str
    delivery_id: str
    text: str
    kind: str = "prompt"
    artifacts: tuple = ()


class ChannelCore:
    def __init__(self,journal):
        self.journal=journal

    def bind(self, scope, adapter, account, conversation, participant, *, capabilities=None, binding_id=None):
        validate_scope(scope)
        if any(not isinstance(v,str) or not v for v in (adapter,account,conversation,participant)):
            raise CoreError("invalid_binding","Channel identity fields are required")
        key=binding_id or "binding_"+hashlib.sha256(canonical_json([scope,adapter,account,conversation,participant]).encode()).hexdigest()
        value={"adapter":adapter,"account":account,"conversation":conversation,"participant":participant,"capabilities":dict(capabilities or {}),"active":True}
        with self.journal.transaction():
            prior=self.journal.get("bindings",key,scope)
            if prior:
                if prior.value!=value: raise CoreError("binding_conflict","Binding identity or capabilities differ")
                return prior
            return self.journal.put("bindings",key,scope,value)

    def resolve(self,scope,binding_id):
        row=self.journal.get("bindings",binding_id,scope)
        if row is None or not row.value["active"]:
            raise CoreError("unavailable_route","No active scoped channel binding")
        return row

    def admit(self,message: InboundMessage):
        if not isinstance(message,InboundMessage) or message.kind not in {"prompt","steer","cancel"}:
            raise CoreError("invalid_input","Unsupported channel input")
        binding=self.resolve(message.scope,message.binding_id)
        if message.participant!=binding.value["participant"] or not message.delivery_id:
            raise CoreError("unauthorized","Input identity does not match binding")
        if not isinstance(message.text,str) or (not message.text and not message.artifacts and message.kind!="cancel"):
            raise CoreError("invalid_input","Input requires content")
        body={"binding_id":message.binding_id,"participant":message.participant,"text":message.text,"artifacts":list(message.artifacts),"delivery_id":message.delivery_id,"disposition":"pending"}
        key=canonical_json(["channel",message.binding_id,message.delivery_id])
        return self.journal.append(message.scope,"user."+message.kind,body,source="channel_core",dedupe_key=key)

    def disposition(self, scope,event_id,status,*,context_id=None,reason=None):
        if status not in {"pending","scheduled","incorporated","addressed","cancelled"}:
            raise CoreError("invalid_state","Unknown input disposition")
        if self.journal.event(scope,event_id) is None:
            raise CoreError("not_found","Input event missing from scope")
        if status=="incorporated" and not context_id:
            raise CoreError("invalid_state","Incorporation requires exact context")
        with self.journal.transaction():
            prior=self.journal.get("input_dispositions",event_id,scope)
            return self.journal.put("input_dispositions",event_id,scope,{"state":status,"context_id":context_id,"reason":reason},expected_revision=prior.revision if prior else 0)
