"""Durable ordered output independent from inference and tool completion."""
import asyncio
import time
from agent.protocol import CoreError,canonical_json,new_id


class Delivery:
    def __init__(self,journal,channels,adapters,*,lease_seconds=30,max_attempts=5):
        if lease_seconds<=0 or max_attempts<1: raise CoreError("invalid_limits","Delivery limits must be positive")
        self.journal,self.channels,self.adapters=journal,channels,adapters
        self.lease_seconds,self.max_attempts=lease_seconds,max_attempts

    def enqueue(self,scope,binding_id,stream_id,sequence,content,*,terminal=None,key=None):
        self.channels.resolve(scope,binding_id)
        if type(sequence) is not int or sequence<0 or not stream_id or terminal not in {None,"completed","interrupted","failed","superseded"}:
            raise CoreError("invalid_output","Output identity/sequence/status invalid")
        key=key or canonical_json([binding_id,stream_id,sequence])
        payload={"binding_id":binding_id,"stream_id":stream_id,"sequence":sequence,"content":content,"terminal":terminal}
        canonical_json(payload)
        with self.journal.transaction():
            prior=self.journal.get("outbox",key,scope)
            if prior:
                if prior.value["payload"]!=payload: raise CoreError("dedupe_conflict","Output sequence reused with different content")
                return prior
            stream_rows=[r for r in self.journal.list("outbox",scope) if r.value["payload"]["binding_id"]==binding_id and r.value["payload"]["stream_id"]==stream_id]
            if {r.value["payload"]["sequence"] for r in stream_rows}!=set(range(sequence)) or any(r.value["payload"]["terminal"] for r in stream_rows):
                raise CoreError("sequence_gap","Output must append contiguously before the stream terminal")
            self.journal.append(scope,"output.persisted",payload,source="delivery")
            return self.journal.put("outbox",key,scope,{"payload":payload,"state":"pending","attempts":0,"due_at":0,"lease_until":0,"claim":None,"duplicate_possible":False})

    def claim(self,scope,*,now=None):
        now=time.time() if now is None else now
        with self.journal.transaction():
            rows=self.journal.list("outbox",scope)
            for row in sorted(rows,key=lambda r:(r.value["payload"]["stream_id"],r.value["payload"]["sequence"])):
                value=row.value
                if value["state"] not in {"pending","retry","claimed"} or value["due_at"]>now: continue
                if value["state"]=="claimed" and value["lease_until"]>now: continue
                payload=value["payload"]
                blockers=[r for r in rows if r.value["payload"]["stream_id"]==payload["stream_id"] and r.value["payload"]["binding_id"]==payload["binding_id"] and r.value["payload"]["sequence"]<payload["sequence"] and r.value["state"] not in {"sent_unconfirmed","acknowledged"}]
                if blockers: continue
                value.update(duplicate_possible=value["duplicate_possible"] or value["state"]=="claimed",state="claimed",claim=new_id("send"),lease_until=now+self.lease_seconds,attempts=value["attempts"]+1)
                return self.journal.put("outbox",row.id,scope,value,expected_revision=row.revision)
        return None

    def finish(self,scope,key,claim,*,receipt=None,error=None,now=None):
        now=time.time() if now is None else now
        with self.journal.transaction():
            row=self.journal.get("outbox",key,scope)
            if row is None or row.value["state"]!="claimed" or row.value["claim"]!=claim:
                raise CoreError("stale_claim","Output claim is not current")
            value=row.value
            if error:
                value.update(state="failed" if value["attempts"]>=self.max_attempts else "retry",error=str(error),due_at=now+min(60,2**value["attempts"]))
            else:
                binding=self.channels.resolve(scope,value["payload"]["binding_id"])
                confirmed=receipt and receipt.get("status")=="acknowledged" and binding.value["capabilities"].get("receipts")
                value.update(state="acknowledged" if confirmed else "sent_unconfirmed",receipt=receipt)
            value.update(lease_until=0,claim=None)
            self.journal.append(scope,"output."+value["state"],{"outbox_id":key,"stream_id":value["payload"]["stream_id"]},source="delivery")
            return self.journal.put("outbox",key,scope,value,expected_revision=row.revision)

    async def dispatch_one(self,scope):
        row=await asyncio.to_thread(self.claim,scope)
        if row is None: return None
        try:
            route=await asyncio.to_thread(self.channels.resolve,scope,row.value["payload"]["binding_id"])
            adapter=self.adapters.get(route.value["adapter"])
            receipt=await adapter.send({"id":row.id,**row.value["payload"]})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return await asyncio.to_thread(self.finish,scope,row.id,row.value["claim"],error=exc)
        return await asyncio.to_thread(self.finish,scope,row.id,row.value["claim"],receipt=receipt)

    def acknowledge(self,scope,binding_id,stream_id,through_sequence):
        route=self.channels.resolve(scope,binding_id)
        if not route.value["capabilities"].get("receipts"): raise CoreError("unsupported","Channel does not support receipts")
        with self.journal.transaction():
            rows=[r for r in self.journal.list("outbox",scope) if r.value["payload"]["binding_id"]==binding_id and r.value["payload"]["stream_id"]==stream_id and r.value["payload"]["sequence"]<=through_sequence]
            if {r.value["payload"]["sequence"] for r in rows}!=set(range(through_sequence+1)) or any(r.value["state"] not in {"sent_unconfirmed","acknowledged"} for r in rows):
                raise CoreError("sequence_gap","Cannot acknowledge unsent or missing chunks")
            for row in rows:
                self.journal.put("outbox",row.id,scope,{**row.value,"state":"acknowledged"},expected_revision=row.revision)

    def replay(self,scope,binding_id,stream_id,after_sequence=-1):
        route=self.channels.resolve(scope,binding_id)
        if not route.value["capabilities"].get("replay"): raise CoreError("unsupported","Channel replay is not negotiated")
        with self.journal.transaction():
            for row in self.journal.list("outbox",scope):
                p=row.value["payload"]
                if p["binding_id"]==binding_id and p["stream_id"]==stream_id and p["sequence"]>after_sequence and row.value["state"]=="sent_unconfirmed":
                    self.journal.put("outbox",row.id,scope,{**row.value,"state":"pending","due_at":0},expected_revision=row.revision)
