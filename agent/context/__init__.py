"""Immutable labeled model packets with exact source ranges and explicit deferrals."""
from dataclasses import dataclass,field
import hashlib
from agent.protocol import CoreError,canonical_json,new_id,strict_loads,validate_scope

KINDS={"instructions","situation","user_input","conversation","tool_observation","memory","runtime_status"}


@dataclass(frozen=True)
class ContextBlock:
    id: str
    scope: str
    kind: str
    text: str
    role: str = "user"
    required: bool = False
    start: int = 0
    revision: int = 1
    origin: str = "observed"
    provider_fields: dict = field(default_factory=dict)

    def __post_init__(self):
        validate_scope(self.scope)
        if not self.id or self.kind not in KINDS or not isinstance(self.text,str) or self.role not in {"system","developer","user","assistant","tool"}:
            raise CoreError("invalid_context","Block identity, kind, content or role invalid")
        if self.kind in {"memory","tool_observation","user_input"} and self.role in {"system","developer"}:
            raise CoreError("context_authority","Retrieved/external content cannot become controlling instructions")
        if self.kind=="instructions" and (self.role not in {"system","developer"} or not self.required):
            raise CoreError("context_authority","Controlling instructions must keep mandatory instruction roles")
        if type(self.start) is not int or not 0<=self.start<=len(self.text.encode()) or type(self.revision) is not int or self.revision<1:
            raise CoreError("invalid_context","Source byte range/revision invalid")
        try: self.text.encode()[:self.start].decode()
        except UnicodeError as exc: raise CoreError("invalid_context","Byte cursor splits a Unicode code point") from exc
        if set(self.provider_fields)-{"tool_calls","tool_call_id"}:
            raise CoreError("context_authority","Provider fields cannot override context role or content")
        if ("tool_calls" in self.provider_fields and self.role!="assistant") or ("tool_call_id" in self.provider_fields and self.role!="tool"):
            raise CoreError("context_causality","Provider call fields require their original roles")
        canonical_json(self.provider_fields)


@dataclass(frozen=True)
class ContextPacket:
    id: str
    scope: str
    work_id: str
    serialized: str

    @property
    def messages(self): return strict_loads(self.serialized)["messages"]

    @property
    def manifest(self): return strict_loads(self.serialized)["manifest"]

    @property
    def metadata(self): return strict_loads(self.serialized)["metadata"]

    def as_dict(self):
        return {"context_id":self.id,"scope":self.scope,"work_id":self.work_id,**strict_loads(self.serialized)}


class ContextCompiler:
    def __init__(self,*,token_counter=None):
        # UTF-8 byte count is the declared conservative default, not a model tokenizer claim.
        self.token_counter=token_counter or (lambda text: len(text.encode()))
        self.tokenizer="utf8_bytes_upper_bound" if token_counter is None else getattr(token_counter,"__name__","configured")

    def compile(self,scope,work_id,blocks,*,context_limit,output_reserve,selected_ids=None,metadata=None,protocol_overhead=0):
        validate_scope(scope)
        if not work_id or type(context_limit) is not int or type(output_reserve) is not int or not 0<output_reserve<context_limit or protocol_overhead<0:
            raise CoreError("context_budget","Invalid context/output allocation")
        if len({b.id for b in blocks})!=len(blocks): raise CoreError("invalid_context","Duplicate context source IDs")
        if any(b.scope!=scope for b in blocks): raise CoreError("scope_denied","Context contains another scope")
        metadata=strict_loads(canonical_json(metadata or {}))
        selected=set(selected_ids) if selected_ids is not None else {b.id for b in blocks}
        if selected-{b.id for b in blocks}: raise CoreError("invalid_context","Attention selected unknown source")
        messages=[];manifest=[];seen_calls=set()
        budget=context_limit-output_reserve-protocol_overhead
        if budget<2: raise CoreError("context_budget","No input budget remains")

        def message(block,text,start,end):
            label={"kind":block.kind,"source_id":block.id,"revision":block.revision,"origin":block.origin,"range":[start,end],"text":text}
            return {"role":block.role,"content":canonical_json(label),**strict_loads(canonical_json(block.provider_fields))}

        def fits(candidate):
            amount=self.token_counter(canonical_json(candidate))
            if type(amount) is not int or amount<0: raise CoreError("context_budget","Tokenizer returned invalid usage")
            return amount<=budget

        # Mandatory instructions are kept at the head; other source order remains stable.
        ordered=[b for b in blocks if b.kind=="instructions"]+[b for b in blocks if b.kind!="instructions"]
        reserved=[b for b in ordered if b.required and b.kind!="instructions"]
        for block in ordered:
            raw=block.text.encode();remaining=raw[block.start:].decode()
            entry={"source_id":block.id,"kind":block.kind,"revision":block.revision,"sha256":hashlib.sha256(raw).hexdigest(),"source_bytes":len(raw),"start":block.start,"end":block.start,"representation":"deferred","pending_ranges":[[block.start,len(raw)]] if remaining else []}
            if block.id not in selected and not block.required:
                manifest.append(entry);continue
            if block.role=="tool" and block.provider_fields.get("tool_call_id") not in seen_calls:
                raise CoreError("context_causality","Tool observation lacks its included assistant call")
            candidate=message(block,remaining,block.start,len(raw))
            future_required=[message(b,b.text.encode()[b.start:].decode(),b.start,len(b.text.encode())) for b in reserved if b.id!=block.id and b not in ordered[:ordered.index(block)]]
            # Optional material cannot consume the budget of mandatory inputs later in order.
            reserve=future_required if not block.required else []
            if not fits(messages+[candidate]+reserve):
                if block.kind=="instructions" or block.provider_fields:
                    if block.required: raise CoreError("context_budget","Mandatory instructions or causal message pair exceeds budget")
                    manifest.append(entry);continue
                low,high=0,len(remaining)
                while low<high:
                    mid=(low+high+1)//2
                    end=block.start+len(remaining[:mid].encode())
                    if fits(messages+[message(block,remaining[:mid],block.start,end)]+reserve): low=mid
                    else: high=mid-1
                if low==0:
                    manifest.append(entry);continue
                remaining=remaining[:low]
            end=block.start+len(remaining.encode())
            messages.append(message(block,remaining,block.start,end))
            entry.update(end=end,representation="exact" if block.start==0 and end==len(raw) else "excerpt",pending_ranges=[[end,len(raw)]] if end<len(raw) else [])
            manifest.append(entry)
            for call in block.provider_fields.get("tool_calls",[]):
                if not isinstance(call,dict) or not call.get("id"): raise CoreError("context_causality","Assistant tool call requires an ID")
                seen_calls.add(call["id"])
        if not fits(messages): raise CoreError("context_budget","Compiled packet exceeds input allocation")
        encoded=canonical_json({"messages":messages,"manifest":manifest,"metadata":{**metadata,"context_limit":context_limit,"reserved_output":output_reserve,"protocol_overhead":protocol_overhead,"input_tokens":self.token_counter(canonical_json(messages)),"tokenizer":self.tokenizer}})
        return ContextPacket(new_id("ctx"),scope,work_id,encoded)
