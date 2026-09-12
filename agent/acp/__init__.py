"""Agent Client Protocol v1 facade over the core; transports belong to callers."""
import asyncio
from pathlib import Path
from agent.channel_core import InboundMessage
from agent.protocol import CoreError,canonical_json,new_id,strict_loads


class ACPAdapter:
    def __init__(self,emit,session_for_binding):
        self.emit,self.session_for_binding=emit,session_for_binding

    async def send(self,item):
        session=self.session_for_binding(item["binding_id"])
        content=item["content"]
        if isinstance(content,dict) and content.get("type") in {"tool", "tool_result"}:
            result=content["result"]
            status="completed" if result["state"]=="completed" and result["outcome"]=="success" else "in_progress" if result["state"] in {"accepted","running"} else "failed"
            update={"sessionUpdate":"tool_call","toolCallId":result["action_id"],"title":result["tool"],"kind":"other","status":status,"content":[{"type":"content","content":{"type":"text","text":canonical_json(result)}}]}
        elif isinstance(content,dict) and content.get("type")=="status":
            if content.get("state")=="completed": return {"status":"sent_unconfirmed"}
            update={"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":
                "Work " + content.get("state", "stopped") + "."}}
        else:
            text=content if isinstance(content,str) else content["text"] if isinstance(content,dict) and content.get("type")=="text" else canonical_json(content)
            update={"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":text}}
        await self.emit({"jsonrpc":"2.0","method":"session/update","params":{"sessionId":session,"update":update}})
        return {"status":"sent_unconfirmed"}


class AcpFacade:
    """Caller binds authenticated scope and real runtime submit/cancel callbacks."""
    def __init__(self,journal,channels,adapters,*,scope,workspace,emit,submit,cancel,connection_id=None):
        self.journal,self.channels,self.scope=journal,channels,scope
        self.workspace=Path(workspace).resolve()
        self.submit,self.cancel=submit,cancel
        self.connection_id=connection_id or new_id("acp")
        self.adapter_name="acp:"+self.connection_id
        self.initialized=False
        self.client_capabilities={}
        adapters.register(self.adapter_name,ACPAdapter(emit,self._session_for_binding))

    def _session_for_binding(self,binding_id):
        for row in self.journal.list("acp_sessions",self.scope):
            if row.value["binding_id"]==binding_id: return row.id
        raise CoreError("not_found","ACP session for output not found")

    def _cwd(self,raw):
        if not isinstance(raw,str) or not Path(raw).is_absolute(): raise CoreError("invalid_params","ACP cwd must be absolute")
        path=Path(raw).resolve()
        if not path.is_relative_to(self.workspace) or not path.is_dir(): raise CoreError("unauthorized","ACP cwd is outside authorized workspace")
        return str(path)

    async def handle(self,frame):
        request_id=frame.get("id") if isinstance(frame,dict) else None
        try:
            if not isinstance(frame,dict) or frame.get("jsonrpc")!="2.0" or not isinstance(frame.get("method"),str) or not isinstance(frame.get("params",{}),dict):
                raise CoreError("invalid_request","Malformed JSON-RPC request")
            method,params=frame["method"],frame.get("params",{})
            if method!="session/cancel" and ("id" not in frame or type(request_id) not in {int,str}):
                raise CoreError("invalid_request","This ACP method requires a request ID")
            if method=="initialize":
                if type(params.get("protocolVersion")) is not int or not isinstance(params.get("clientCapabilities",{}),dict):
                    raise CoreError("invalid_params","Protocol version and capability object required")
                self.initialized=True
                self.client_capabilities=strict_loads(canonical_json(params.get("clientCapabilities",{})))
                result={"protocolVersion":1,"agentCapabilities":{"loadSession":True,"promptCapabilities":{"image":False,"audio":False,"embeddedContext":False}},"agentInfo":{"name":"sturgis-core","version":"0.1.0"},"authMethods":[]}
            elif not self.initialized:
                raise CoreError("not_initialized","Initialize before session operations")
            elif method=="session/new":
                cwd=self._cwd(params.get("cwd"))
                if params.get("mcpServers"): raise CoreError("unsupported","This facade does not expose client MCP servers")
                session_id=new_id("session")
                binding=await asyncio.to_thread(self.channels.bind,self.scope,self.adapter_name,self.connection_id,session_id,"client",capabilities={"receipts":False,"replay":False})
                await asyncio.to_thread(self.journal.put,"acp_sessions",session_id,self.scope,{"binding_id":binding.id,"cwd":cwd,"protocol":1,"active_turn":None})
                result={"sessionId":session_id}
            elif method=="session/load":
                session_id=params.get("sessionId")
                row=self.journal.get("acp_sessions",session_id,self.scope)
                if row is None: raise CoreError("not_found","Unknown ACP session")
                if self._cwd(params.get("cwd"))!=row.value["cwd"]: raise CoreError("unauthorized","Workspace changed on session load")
                if params.get("mcpServers"): raise CoreError("unsupported","Client MCP servers unsupported")
                binding=await asyncio.to_thread(self.channels.bind,self.scope,self.adapter_name,self.connection_id,session_id,"client",capabilities={"receipts":False,"replay":False})
                await asyncio.to_thread(self.journal.put,"acp_sessions",row.id,self.scope,{**row.value,"binding_id":binding.id},expected_revision=row.revision)
                result={}
            elif method in {"session/prompt","session/cancel"}:
                session_id=params.get("sessionId")
                row=self.journal.get("acp_sessions",session_id,self.scope)
                if row is None: raise CoreError("not_found","Unknown ACP session")
                if method=="session/cancel":
                    event=await asyncio.to_thread(self.channels.admit,InboundMessage(self.scope,row.value["binding_id"],"client",new_id("cancel"),"",kind="cancel"))
                    await self.cancel(event)
                    return None
                blocks=params.get("prompt")
                if not isinstance(blocks,list) or not blocks or any(not isinstance(b,dict) or b.get("type")!="text" or not isinstance(b.get("text"),str) for b in blocks):
                    raise CoreError("unsupported","Only text prompt blocks are supported")
                turn_key=canonical_json([self.connection_id,request_id])
                prior=self.journal.get("acp_turns",turn_key,self.scope)
                prompt_text="\n".join(b["text"] for b in blocks)
                if prior:
                    if prior.value["text"]!=prompt_text or prior.value["session_id"]!=session_id: raise CoreError("dedupe_conflict","Prompt request ID reused")
                    if prior.value["state"]=="completed": return {"jsonrpc":"2.0","id":request_id,"result":prior.value["result"]}
                    raise CoreError("in_progress","Prompt is already accepted; outcome remains pending")
                if row.value["active_turn"] is not None: raise CoreError("in_progress","Session has an active prompt")
                with self.journal.transaction():
                    row=self.journal.get("acp_sessions",session_id,self.scope)
                    self.journal.put("acp_sessions",row.id,self.scope,{**row.value,"active_turn":turn_key},expected_revision=row.revision)
                    event=self.channels.admit(InboundMessage(self.scope,row.value["binding_id"],"client",turn_key,prompt_text))
                    turn=self.journal.put("acp_turns",turn_key,self.scope,{"text":prompt_text,"session_id":session_id,"state":"accepted","event_id":event.id})
                try:
                    result=await self.submit(event)
                    if not isinstance(result,dict) or result.get("stopReason") not in {"end_turn","max_tokens","max_turn_requests","refusal","cancelled"}:
                        raise CoreError("invalid_result","Runtime returned invalid ACP stop reason")
                except (Exception,asyncio.CancelledError) as exc:
                    with self.journal.transaction():
                        self.journal.put("acp_turns",turn_key,self.scope,{**turn.value,"state":"failed","error":type(exc).__name__},expected_revision=turn.revision)
                        row=self.journal.get("acp_sessions",session_id,self.scope)
                        self.journal.put("acp_sessions",session_id,self.scope,{**row.value,"active_turn":None},expected_revision=row.revision)
                    if isinstance(exc,asyncio.CancelledError): raise
                    raise CoreError("runtime_failure","Prompt failed; accepted input and effects remain recorded") from exc
                with self.journal.transaction():
                    self.journal.put("acp_turns",turn_key,self.scope,{**turn.value,"state":"completed","result":result},expected_revision=turn.revision)
                    row=self.journal.get("acp_sessions",session_id,self.scope)
                    self.journal.put("acp_sessions",session_id,self.scope,{**row.value,"active_turn":None},expected_revision=row.revision)
            else:
                raise CoreError("method_not_found","Unsupported ACP method")
            return {"jsonrpc":"2.0","id":request_id,"result":result}
        except CoreError as exc:
            if isinstance(frame,dict) and "id" not in frame: return None
            code=-32601 if exc.code=="method_not_found" else -32600 if exc.code=="invalid_request" else -32602
            return {"jsonrpc":"2.0","id":request_id,"error":{"code":code,"message":str(exc),"data":exc.as_dict()}}
