"""CLI argument authority and a real standalone process with agent imports prohibited."""
import asyncio
from pathlib import Path
import runpy
import socket
import sys

import httpx
import pytest

import model_gateway.__main__ as cli


@pytest.mark.parametrize('automatic',[False,True])
async def test_cli_explicit_and_packaged_web_assets(tmp_path,automatic,monkeypatch):
    captured={}
    monkeypatch.setattr('uvicorn.run',lambda app,**options:captured.update(app=app,options=options))
    monkeypatch.setattr(cli,'__file__',str(tmp_path/'package'/'__main__.py'))
    web=tmp_path/'consumer'/'web' if automatic else tmp_path/'custom-web'
    (web/'assets').mkdir(parents=True)
    (web/'gateway.html').write_text('<!doctype html><title>Gateway fixture</title>')
    args=['serve','--root',str(tmp_path/'state'),'--host','0.0.0.0','--public-origin','https://gateway.example']
    if not automatic: args+=['--web-root',str(web)]
    cli.main(args)
    app=captured['app']
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://gateway.example') as browser:
        response=await browser.get('/')
        assert response.status_code==200 and 'Gateway fixture' in response.text
    assert app.state.gateway.allow_local_servers is True
    assert app.state.auth.origin=='https://gateway.example'


def test_module_entry_invokes_argument_parser(monkeypatch,capsys):
    monkeypatch.setattr(sys,'argv',['model_gateway','serve','--port','0'])
    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(Path(cli.__file__)),run_name='__main__')
    assert error.value.code==2 and 'Choose a port' in capsys.readouterr().err


async def test_real_cli_restart_preserves_keys_and_settings_without_agent_imports(tmp_path):
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
    launcher="""import runpy, sys
class IndependentGateway:
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'agent', 'consumer'}:
            raise RuntimeError('Forbidden gateway client import: ' + fullname)
sys.meta_path.insert(0, IndependentGateway())
sys.argv = ['model_gateway', *sys.argv[1:]]
runpy.run_module('model_gateway', run_name='__main__')
"""
    state_root=tmp_path/'state';base=f'http://127.0.0.1:{port}'
    outputs=[]
    async def launch():
        return await asyncio.create_subprocess_exec(sys.executable,'-c',launcher,'serve','--root',str(state_root),
            '--port',str(port),'--deny-local-servers',cwd=tmp_path,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    async def stop(process):
        if process.returncode is None: process.terminate()
        stdout,stderr=await asyncio.wait_for(process.communicate(),5)
        outputs.append(stdout+stderr)
        # Uvicorn performs lifespan shutdown and then re-raises SIGTERM on POSIX.
        assert process.returncode in (0,-15),stderr.decode()
        assert b'Application shutdown complete.' in stderr
        assert b'Forbidden gateway client import' not in stderr
    async def ready(process,client):
        async with asyncio.timeout(8):
            while True:
                if process.returncode is not None:
                    stdout,stderr=await process.communicate()
                    pytest.fail('Gateway process exited before listening: '+stderr.decode())
                try:
                    response=await client.get('/health')
                    assert response.status_code==200 and response.json()=={'status':'ok','version':'gateway/1'}
                    return
                except httpx.ConnectError:
                    await asyncio.sleep(.03)
    process=await launch()
    try:
        async with httpx.AsyncClient(base_url=base,trust_env=False,timeout=.5,limits=httpx.Limits(max_keepalive_connections=0)) as client:
            await ready(process,client)
            assert (await client.get('/api/v1/gateway/connections')).status_code==401
            assert (await client.get('/v1/models')).status_code==401
            assert not (state_root/'runtimes').exists()
            assert (state_root/'admin.key').stat().st_mode & 0o777 == 0o600
            admin=(state_root/'admin.key').read_text()
            client.headers['Authorization']='Bearer '+admin
            app_key=(await client.post('/api/v1/gateway/keys',json={'name':'Restart client'})).json()['data']['key']
            saved=await client.post('/api/v1/gateway/settings',json={'expected_revision':0,'request_timeout':2,'max_concurrency':2,'requests_per_minute':17})
            assert saved.status_code==200
            client.headers.pop('Authorization');client.headers['Origin']=base
            login=await client.post('/api/v1/gateway/session',json={'admin_key':admin})
            assert login.status_code==200 and client.cookies.get('sturgis_gateway')
            await stop(process)
            process=await launch()
            await ready(process,client)
            assert (state_root/'admin.key').read_text()==admin
            # Browser sessions are process-local; persisted inference keys are not.
            assert (await client.get('/api/v1/gateway/connections')).status_code==401
            client.headers['Authorization']='Bearer '+app_key
            models=await client.get('/v1/models')
            assert models.status_code==200 and models.json()['data']==[]
            client.headers['Authorization']='Bearer '+admin
            settings=(await client.get('/api/v1/gateway/settings')).json()['data']
            assert settings['revision']==1 and settings['requests_per_minute']==17 and settings['request_timeout']==2
    finally:
        await stop(process)
    assert all(f'Model Gateway: {base}'.encode() in output for output in outputs)
    assert all(admin.encode() not in output and app_key.encode() not in output for output in outputs)
