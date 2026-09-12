"""Reusable asynchronous channel transports; no application listener."""
import asyncio
from agent.framing import read_frame,write_frame
from agent.protocol import CoreError,canonical_json,strict_loads


class AdapterRegistry:
    def __init__(self): self._adapters={}

    def register(self,name,adapter):
        if not name or name in self._adapters or not callable(getattr(adapter,"send",None)):
            raise CoreError("invalid_adapter","Adapter is missing send or already registered")
        self._adapters[name]=adapter

    def get(self,name):
        try: return self._adapters[name]
        except KeyError as exc: raise CoreError("unavailable_adapter","Channel adapter is unavailable",adapter=name) from exc

    def remove(self,name):
        self.get(name)
        return self._adapters.pop(name)


class StreamChannelAdapter:
    """Framed JSON over caller-owned asyncio streams, with optional explicit receipts."""
    def __init__(self,reader,writer,*,receipts=False,max_frame=1048576):
        self.reader,self.writer=reader,writer
        self.receipts,self.max_frame=receipts,max_frame
        self._send_lock=asyncio.Lock()

    async def receive(self):
        data=await read_frame(self.reader,self.max_frame)
        if data is None: raise CoreError("disconnected","Channel reached EOF")
        value=strict_loads(data)
        if not isinstance(value,dict): raise CoreError("invalid_input","Channel frame must be an object")
        return value

    async def send(self, item):
        async with self._send_lock:
            await write_frame(self.writer,canonical_json(item).encode(),self.max_frame)
        return {"status":"sent_unconfirmed","receipt_expected":self.receipts}

    async def close(self):
        self.writer.close()
        await self.writer.wait_closed()
