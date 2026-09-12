"""Ephemeral actual consumer server and deterministic remote wire endpoint for Chromium journeys."""
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import signal
import socket
import tempfile

import uvicorn

from consumer.api import create_app
from consumer.config import Settings
from agent.protocol import canonical_json


async def main():
    peers=set()
    async def model(reader,writer):
        task=asyncio.current_task();peers.add(task)
        try:
            header=await reader.readuntil(b'\r\n\r\n')
            if header.startswith(b'GET'):
                body=b'{"message":"Connected to the real test endpoint"}'
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: '+str(len(body)).encode()+b'\r\nConnection: close\r\n\r\n'+body);await writer.drain();return
            length=next(int(line.split(b':',1)[1]) for line in header.split(b'\r\n') if line.lower().startswith(b'content-length:'))
            request=json.loads(await reader.readexactly(length))
            wire=canonical_json(request)
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n');await writer.drain()
            async def emit(body):
                writer.write(('data:'+canonical_json(body)+'\n\n').encode());await writer.drain()
            if request['messages'][0].get('content')=='Reply with OK.':
                answer='OK'
            else:
                answer='A real streamed reply. <img src=x onerror="window.injected=true">'
            await emit({'choices':[{'delta':{'content':answer}}]})
            if 'slow browser request' in wire:await asyncio.sleep(2)
            await emit({'choices':[],'usage':{'total_tokens':12}})
            writer.write(b'data:[DONE]\n\n');await writer.drain()
        except (ConnectionError,asyncio.IncompleteReadError):pass
        finally:
            writer.close()
            try: await writer.wait_closed()
            except ConnectionError: pass
            peers.discard(task)
    remote=await asyncio.start_server(model,'127.0.0.1',0)
    origin=f'http://127.0.0.1:{remote.sockets[0].getsockname()[1]}'
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen();port=sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='sturgis-web-') as directory:
        app=create_app(Settings(root=Path(directory),host='127.0.0.1',port=port,bootstrap_token='browser-installation-token',private_network_origins=(origin,)))
        server=uvicorn.Server(uvicorn.Config(app,log_level='error',access_log=False,proxy_headers=False))
        task=asyncio.create_task(server.serve(sockets=[sock]))
        while not server.started:
            if task.done():await task
            await asyncio.sleep(.01)
        print(json.dumps({'url':f'http://127.0.0.1:{port}','model':origin+'/model'}),flush=True)
        await task
    remote.close();await remote.wait_closed()
    for peer in list(peers):peer.cancel()
    await asyncio.gather(*peers,return_exceptions=True)

if __name__=='__main__':asyncio.run(main())
