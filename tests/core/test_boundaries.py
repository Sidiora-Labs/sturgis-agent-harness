import asyncio
from contextlib import asynccontextmanager
import json

import pytest

from agent.protocol import CoreError,ToolCall,canonical_json
from agent.state import Journal
from agent.channel_core import ChannelCore,InboundMessage
from agent.channel_adapters import AdapterRegistry,StreamChannelAdapter
from agent.delivery import Delivery
from agent.provider_catalog import ProviderCatalog
from agent.provider_adapters import HTTPProvider
from agent.acp import AcpFacade
from agent.framing import read_frame,write_frame


def setup_channels(tmp_path,capabilities=None):
    j=Journal(tmp_path/"state.db");c=ChannelCore(j);a=AdapterRegistry()
    b=c.bind("s","wire","account","conversation","user",capabilities=capabilities)
    return j,c,a,b


def test_input_admission_and_scoping(tmp_path):
    j,c,a,b=setup_channels(tmp_path)
    assert c.bind("s","wire","account","conversation","user")==b
    with pytest.raises(CoreError): c.bind("s","wire","account","conversation","user",binding_id=b.id,capabilities={"receipts":True})
    with pytest.raises(CoreError): c.bind("s","","a","c","p")
    event=c.admit(InboundMessage("s",b.id,"user","m1","hello"))
    assert c.admit(InboundMessage("s",b.id,"user","m1","hello"))==event
    for message in [InboundMessage("x",b.id,"user","m1","x"),InboundMessage("s",b.id,"intruder","m2","x"),InboundMessage("s",b.id,"user","m2",""),InboundMessage("s",b.id,"user","m2","x",kind="wrong")]:
        with pytest.raises(CoreError): c.admit(message)
    with pytest.raises(CoreError): c.admit(InboundMessage("s",b.id,"user","m1","changed"))
    c.admit(InboundMessage("s",b.id,"user","cancel","",kind="cancel"))
    assert c.disposition("s",event.id,"scheduled").revision==1
    with pytest.raises(CoreError): c.disposition("s",event.id,"incorporated")
    assert c.disposition("s",event.id,"incorporated",context_id="ctx").revision==2
    with pytest.raises(CoreError): c.disposition("s","missing","pending")
    with pytest.raises(CoreError): c.disposition("s",event.id,"invented")
    j.close()


async def test_stream_channel_and_durable_delivery(tmp_path):
    received=[];done=asyncio.Event()
    async def receiver(reader,writer):
        for _ in range(2): received.append(json.loads(await read_frame(reader)))
        await write_frame(writer,b'{"incoming":"yes"}')
        writer.close();await writer.wait_closed();done.set()
    server=await asyncio.start_server(receiver,"127.0.0.1",0)
    async with server:
        reader,writer=await asyncio.open_connection(*server.sockets[0].getsockname())
        adapter=StreamChannelAdapter(reader,writer,receipts=True)
        j,c,a,b=setup_channels(tmp_path,{"receipts":True,"replay":True})
        a.register("wire",adapter)
        with pytest.raises(CoreError): a.register("wire",adapter)
        with pytest.raises(CoreError): a.get("missing")
        d=Delivery(j,c,a)
        first=d.enqueue("s",b.id,"r",0,"first")
        assert d.enqueue("s",b.id,"r",0,"first")==first
        d.enqueue("s",b.id,"r",1,"last",terminal="completed")
        with pytest.raises(CoreError): d.enqueue("s",b.id,"r",0,"different")
        assert (await d.dispatch_one("s")).value["state"]=="sent_unconfirmed"
        assert (await d.dispatch_one("s")).value["state"]=="sent_unconfirmed"
        assert await d.dispatch_one("s") is None
        await asyncio.wait_for(done.wait(),2)
        assert [x["content"] for x in received]==["first","last"]
        assert await adapter.receive()=={"incoming":"yes"}
        with pytest.raises(CoreError): await adapter.receive()
        d.acknowledge("s",b.id,"r",1)
        assert all(r.value["state"]=="acknowledged" for r in j.list("outbox","s"))
        with pytest.raises(CoreError): d.acknowledge("s",b.id,"r",2)
        await adapter.close();a.remove("wire");j.close()


def test_delivery_claim_recovery_retry_and_replay(tmp_path):
    j,c,a,b=setup_channels(tmp_path)
    with pytest.raises(CoreError): Delivery(j,c,a,lease_seconds=0)
    d=Delivery(j,c,a,lease_seconds=2,max_attempts=2)
    with pytest.raises(CoreError): d.enqueue("s",b.id,"r",-1,"x")
    with pytest.raises(CoreError): d.enqueue("s",b.id,"r",2,"gap")
    d.enqueue("s",b.id,"r",0,"one");d.enqueue("s",b.id,"r",1,"two")
    row=d.claim("s",now=10)
    assert d.claim("s",now=11) is None
    recovered=d.claim("s",now=13)
    assert recovered.value["duplicate_possible"]
    with pytest.raises(CoreError): d.finish("s",row.id,row.value["claim"])
    failed=d.finish("s",recovered.id,recovered.value["claim"],error="offline",now=13)
    assert failed.value["state"]=="failed"
    assert d.claim("s",now=99) is None
    with pytest.raises(CoreError): d.acknowledge("s",b.id,"r",0)
    with pytest.raises(CoreError): d.replay("s",b.id,"r")
    b2=c.bind("s","wire","a","c2","u",capabilities={"receipts":True,"replay":True})
    d.enqueue("s",b2.id,"r2",0,"x")
    claim=d.claim("s",now=20)
    retry=d.finish("s",claim.id,claim.value["claim"],error="once",now=20)
    assert retry.value["state"]=="retry"
    assert d.claim("s",now=21) is None
    claim=d.claim("s",now=23)
    d.finish("s",claim.id,claim.value["claim"],receipt={"status":"sent_unconfirmed"})
    d.replay("s",b2.id,"r2")
    claim=d.claim("s",now=30)
    assert d.finish("s",claim.id,claim.value["claim"],receipt={"status":"acknowledged"}).value["state"]=="acknowledged"
    j.close()


async def test_missing_adapter_records_retry(tmp_path):
    j,c,a,b=setup_channels(tmp_path)
    d=Delivery(j,c,a);d.enqueue("s",b.id,"r",0,"x")
    assert (await d.dispatch_one("s")).value["state"]=="retry"
    j.close()


@asynccontextmanager
async def wire_provider(lines,*,status=200,stall=False):
    requests=[];connected=asyncio.Event();release=asyncio.Event();handlers=set()
    async def serve(reader,writer):
        handlers.add(asyncio.current_task())
        try:
            head=await reader.readuntil(b"\r\n\r\n")
            length=int(next(line.split(b":",1)[1] for line in head.split(b"\r\n") if line.lower().startswith(b"content-length:")))
            requests.append((head, json.loads(await reader.readexactly(length))))
            writer.write(f"HTTP/1.1 {status} Response\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n".encode());await writer.drain();connected.set()
            if stall: await release.wait()
            for line in lines:
                writer.write(line if isinstance(line,bytes) else ("data: "+json.dumps(line)+"\n\n").encode())
                await writer.drain()
        finally:
            writer.close();await writer.wait_closed();handlers.discard(asyncio.current_task())
    server=await asyncio.start_server(serve,"127.0.0.1",0)
    try:
        yield "http://127.0.0.1:"+str(server.sockets[0].getsockname()[1])+"/chat",requests,connected
    finally:
        release.set();server.close();await server.wait_closed()
        if handlers: await asyncio.gather(*handlers,return_exceptions=True)


def descriptor(endpoint,**extra):
    return {"endpoint":endpoint,"model":"test-wire-model","authentication":"none","tools":True,"context_limit":4096,"output_limit":512,**extra}


async def collect_provider(endpoint,lines=None,**kwargs):
    return [event async for event in HTTPProvider(**kwargs).stream(descriptor(endpoint),[{"role":"user","content":"hello"}])]


async def test_real_http_stream_and_tool_serialization():
    call=canonical_json(ToolCall("file.read",{"path":"a"}).as_dict())
    events=[{"choices":[{"delta":{"content":"hello"}}]}, {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call1","function":{"name":"invoke","arguments":call[:15]}}]}}]}, {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":call[15:]}}]}}],"usage":{"total_tokens":9}},b"data: [DONE]\n\n"]
    async with wire_provider(events) as (url,requests,_):
        output=[e async for e in HTTPProvider().stream(descriptor(url,authentication="bearer"),[{"role":"system","content":"rules"},{"role":"user","content":"hello"}],tools=[{"type":"function","function":{"name":"invoke"}}],credential="test-only")]
    assert [e.kind for e in output]==["text","usage","tool","terminal"]
    assert output[2].data["call"]["arguments"]=={"path":"a"}
    assert b"Authorization: Bearer test-only" in requests[0][0]
    assert requests[0][1]["messages"][0]["role"]=="system"


@pytest.mark.parametrize("lines,status,limit,code",[
    ([],429,1000,"provider_http"),
    ([{"choices":[]}],200,1000,"provider_incomplete"),
    ([b"data: []\n\n"],200,1000,"provider_protocol"),
    ([{"error":"upstream"}],200,1000,"provider_error"),
    ([b"data: {\n\n"],200,1000,"invalid_json"),
    ([b"data: "+b"x"*100+b"\n"],200,20,"provider_bounds"),
    ([b"x"*100],200,20,"provider_bounds"),
    ([{"choices":[{"delta":{"tool_calls":[{"function":{"name":"other","arguments":"{}"}}]}}]},b"data: [DONE]\n"],200,1000,"provider_protocol"),
])
async def test_provider_errors(lines,status,limit,code):
    async with wire_provider(lines,status=status) as (url,_,_):
        with pytest.raises(CoreError) as caught: await collect_provider(url,max_event_bytes=limit)
        assert caught.value.code==code


async def test_provider_cancel_and_validation():
    cancel=asyncio.Event()
    async with wire_provider([],stall=True) as (url,_,connected):
        async def run(): return [e async for e in HTTPProvider().stream(descriptor(url),[{"role":"user","content":"x"}],cancel=cancel)]
        task=asyncio.create_task(run());await connected.wait();cancel.set()
        with pytest.raises(CoreError,match="cancelled"): await asyncio.wait_for(task,2)
    async def collect(d,m,**kw): return [e async for e in HTTPProvider().stream(d,m,**kw)]
    with pytest.raises(CoreError): await collect(descriptor("http://127.0.0.1:1"),[],cancel=cancel)
    with pytest.raises(CoreError): await collect(descriptor("http://127.0.0.1:1"),[{"role":"invented"}])
    with pytest.raises(CoreError): await collect(descriptor("http://127.0.0.1:1",tools=False),[],tools=[{}])
    with pytest.raises(CoreError): await collect(descriptor("http://127.0.0.1:1",authentication="bearer"),[])
    with pytest.raises(CoreError): await collect(descriptor("http://127.0.0.1:1"),[])
    with pytest.raises(CoreError): HTTPProvider(timeout=0)


def test_catalog_freshness_and_versions(tmp_path):
    j=Journal(tmp_path/"db");c=ProviderCatalog(j)
    for endpoint in ["ftp://host", "https://user:secret@host", "https://host/#fragment"]:
        with pytest.raises(CoreError): c.register("s","bad",endpoint=endpoint)
    with pytest.raises(CoreError): c.register("s","x",endpoint="https://host",transport="made-up")
    c.register("s","p",endpoint="https://host/chat")
    with pytest.raises(CoreError): c.resolve("s","p")
    with pytest.raises(CoreError): c.observe("s","missing",model="x",context_limit=100,output_limit=10)
    with pytest.raises(CoreError): c.observe("s","p",model="x",context_limit=10,output_limit=20)
    c.observe("s","p",model="x",context_limit=100,output_limit=10,now=10,ttl=10,tools=False)
    assert c.resolve("s","p",now=15)["revision"]==1
    with pytest.raises(CoreError): c.resolve("s","p",now=15,require_tools=True)
    with pytest.raises(CoreError): c.resolve("s","p",now=21)
    assert c.observe("s","p",model="x",context_limit=100,output_limit=10,now=20).revision==2
    j.close()


async def test_acp_facade_correlation_delivery_and_cancel(tmp_path):
    j=Journal(tmp_path/"db");channels=ChannelCore(j);adapters=AdapterRegistry();emitted=[];accepted=[];cancelled=[]
    async def emit(frame): emitted.append(frame)
    async def submit(event):
        accepted.append(event)
        d.enqueue("s",event.payload["binding_id"],event.id,0,"answer")
        await d.dispatch_one("s")
        return {"stopReason":"end_turn"}
    async def cancel(event): cancelled.append(event)
    acp=AcpFacade(j,channels,adapters,scope="s",workspace=tmp_path,emit=emit,submit=submit,cancel=cancel,connection_id="connection")
    d=Delivery(j,channels,adapters)
    def req(method,params,id=1): return {"jsonrpc":"2.0","id":id,"method":method,"params":params}
    assert "error" in await acp.handle(req("session/new",{"cwd":str(tmp_path)}))
    assert "error" in await acp.handle(req("initialize",{"protocolVersion":"1"}))
    result=await acp.handle(req("initialize",{"protocolVersion":2}))
    assert result["result"]["protocolVersion"]==1
    result=await acp.handle(req("session/new",{"cwd":str(tmp_path),"mcpServers":[]}))
    session=result["result"]["sessionId"]
    prompt=req("session/prompt",{"sessionId":session,"prompt":[{"type":"text","text":"hi"}]},id=3)
    assert (await acp.handle(prompt))["result"]["stopReason"]=="end_turn"
    assert (await acp.handle(prompt))["result"]["stopReason"]=="end_turn"
    assert len(accepted)==1 and emitted[0]["params"]["update"]["sessionUpdate"]=="agent_message_chunk"
    assert j.list("outbox","s")[0].value["state"]=="sent_unconfirmed"
    assert await acp.handle({"jsonrpc":"2.0","method":"session/cancel","params":{"sessionId":session}}) is None
    assert len(cancelled)==1
    assert (await acp.handle(req("session/load",{"sessionId":session,"cwd":str(tmp_path)})))["result"]=={}
    cases=[req("session/new",{"cwd":"relative"}),req("session/new",{"cwd":"/"}),req("session/new",{"cwd":str(tmp_path),"mcpServers":[{}]}),req("session/load",{"sessionId":"missing","cwd":str(tmp_path)}),req("session/prompt",{"sessionId":"missing","prompt":[]}),req("session/prompt",{"sessionId":session,"prompt":[{"type":"image"}]}),req("other",{}),[],req("session/prompt",{"sessionId":session,"prompt":[{"type":"text","text":"different"}]},id=3)]
    for frame in cases: assert "error" in await acp.handle(frame)
    assert await acp.handle({"jsonrpc":"2.0","method":"other"}) is None
    with pytest.raises(CoreError): acp._session_for_binding("missing")
    j.close()
