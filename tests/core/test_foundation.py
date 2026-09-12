import asyncio
import math
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent.protocol import CoreError, ToolCall, ToolResult, canonical_json, new_id, strict_loads, validate_scope
from agent.state import Journal
from agent.artifacts import ArtifactStore
from agent.framing import FrameCodec, encode_frame, read_frame, write_frame


def test_strict_protocol():
    assert new_id("test").startswith("test_")
    assert strict_loads(canonical_json({"é":[1,True,None]})) == {"é":[1,True,None]}
    for value in ['{"x":1,"x":2}', 'NaN', '{', b'\xff', 'Infinity', '1e999']:
        with pytest.raises(CoreError): strict_loads(value)
    for value in [math.inf,object()]:
        with pytest.raises(CoreError): canonical_json(value)
    for scope in ["",None,"x\0", "a"*257]:
        with pytest.raises(CoreError): validate_scope(scope)
    with pytest.raises(CoreError): new_id("../")
    error=CoreError("bad","bad request",field="x")
    assert error.as_dict()["details"]=={"field":"x"}


def test_tool_contracts():
    call=ToolCall.from_dict({"tool":"file.read","arguments":{"path":"a"}})
    assert call.as_dict()["schema_version"]=="1.0"
    for value in [[],{}, {"tool":"x","arguments":{},"extra":1},{"tool":"","arguments":{}},{"tool":"x","arguments":[]},{"tool":"x","arguments":{},"schema_version":"2"},{"tool":"x","arguments":{},"tool_version":None}]:
        with pytest.raises(CoreError): ToolCall.from_dict(value)
    for state,outcome in [("accepted",None),("running",None),("completed","success"),("completed","target_error"),("completed","execution_error"),("rejected","rejected"),("cancelled","cancelled"),("indeterminate","unknown")]:
        assert ToolResult("a","b","x",state,outcome).as_dict()["state"]==state
    with pytest.raises(CoreError): ToolResult("a","b","x","completed",None)
    with pytest.raises(CoreError): ToolResult("a","b","x","completed","success",effects={"certainty":"guessed"})


def test_journal_atomicity_restart_scope_and_dedupe(tmp_path):
    path=tmp_path/"sub/state.db"
    j=Journal(path)
    with j.transaction():
        row=j.put("goal","g","s",{"state":"active"})
        e=j.append("s","goal.created",row.value,dedupe_key="key")
    assert j.append("s","goal.created",row.value,dedupe_key="key")==e
    with pytest.raises(CoreError): j.append("s","other",row.value,dedupe_key="key")
    assert j.get("goal","g","other") is None
    assert j.get("goal","missing","s") is None
    with pytest.raises(CoreError): j.put("goal","g","s",{},expected_revision=0)
    with pytest.raises(CoreError): j.put("","x","s",{})
    with pytest.raises(RuntimeError):
        with j.transaction():
            j.put("goal","g","s",{"state":"done"},expected_revision=1)
            j.append("s","rollback",{})
            raise RuntimeError("rollback")
    assert j.get("goal","g","s").revision==1
    with j.transaction():
        try:
            with j.transaction():
                j.put("goal","nested","s",{})
                raise ValueError("savepoint")
        except ValueError: pass
        j.append("s","second",{})
    assert j.get("goal","nested","s") is None
    assert len(j.list("goal","s"))==1
    assert len(j.events("s",kinds=["second"]))==1
    assert j.events("s",kinds=[])==[]
    assert len(j.events("s",after=e.seq))==1
    j.advance_cursor("s","consumer",e.seq)
    for seq in [-1,0,100]:
        with pytest.raises(CoreError): j.advance_cursor("s","consumer",seq)
    with pytest.raises(CoreError): j.events("s",limit=0)
    j.close();j.close()
    with pytest.raises(CoreError):
        with j.transaction(): pass
    j=Journal(path)
    assert j.cursor("s","consumer")==e.seq
    assert j.cursor("s","new")==0
    with pytest.raises(CoreError): j.delete("goal","g","s",9)
    j.delete("goal","g","s",1)
    assert j.list("goal","s")==[]
    j.close()


def test_competing_revisions(tmp_path):
    j=Journal(tmp_path/"db")
    j.put("n","k","s",{"x":0})
    def update(value):
        try:
            return j.put("n","k","s",{"x":value},expected_revision=1).revision
        except CoreError as error: return error.code
    with ThreadPoolExecutor(2) as pool:
        result=list(pool.map(update,[1,2]))
    assert sorted(result,key=str)==[2,"conflict"]
    j.close()


def test_artifact_integrity_and_scope(tmp_path):
    store=ArtifactStore(tmp_path/"artifacts")
    item=store.put("s",b"evidence","text/plain")
    assert store.get("s",item.id)==b"evidence"
    assert store.put("s",b"evidence","text/plain")==item
    for scope,id in [("other",item.id),("s","../../secret"),("s","art_"+"z"*64)]:
        with pytest.raises(CoreError): store.get(scope,id)
    with pytest.raises(CoreError): store.put("s","text")
    body=next((tmp_path/"artifacts").glob("*/"+item.id))
    body.write_bytes(b"changed")
    with pytest.raises(CoreError,match="changed"): store.get("s",item.id)
    body.unlink();body.symlink_to(tmp_path/"outside")
    with pytest.raises(CoreError): store.get("s",item.id)
    directory=body.parent
    body.unlink();(directory/(item.id+".json")).unlink();directory.rmdir()
    directory.symlink_to(tmp_path,target_is_directory=True)
    with pytest.raises(CoreError): store.put("s",b"x")


def test_incremental_frames():
    wire=encode_frame(b"hello")+encode_frame(b"")+encode_frame(b"world")
    for size in [1,2,4,8,100]:
        codec=FrameCodec();result=[]
        for i in range(0,len(wire),size): result+=codec.feed(wire[i:i+size])
        codec.finish();assert result==[b"hello",b"",b"world"]
    for wire in [b"\x00",encode_frame(b"abc")[:-1]]:
        codec=FrameCodec();codec.feed(wire)
        with pytest.raises(CoreError): codec.finish()
    with pytest.raises(CoreError): FrameCodec(-1)
    with pytest.raises(CoreError): FrameCodec(2).feed(encode_frame(b"abc"))
    with pytest.raises(CoreError): encode_frame(b"abc",2)


async def test_async_frames_real_socket():
    async def echo(reader,writer):
        payload=await read_frame(reader)
        await write_frame(writer,payload)
        writer.close();await writer.wait_closed()
    server=await asyncio.start_server(echo,"127.0.0.1",0)
    async with server:
        reader,writer=await asyncio.open_connection(*server.sockets[0].getsockname())
        await write_frame(writer,b"hello")
        assert await read_frame(reader)==b"hello"
        assert await read_frame(reader) is None
        writer.close();await writer.wait_closed()
    for wire,limit in [(b"\0",10),(encode_frame(b"abc")[:-1],10),(encode_frame(b"abc"),2)]:
        reader=asyncio.StreamReader();reader.feed_data(wire);reader.feed_eof()
        with pytest.raises(CoreError): await read_frame(reader,limit)
