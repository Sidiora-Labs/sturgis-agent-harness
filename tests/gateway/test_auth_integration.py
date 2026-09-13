"""User sign-in → account → model discovery → client key → inference HTTP journeys."""
import asyncio
from contextlib import asynccontextmanager
import json
import socket
import time
from urllib.parse import urlencode, urlsplit, parse_qs

import httpx
import pytest

from model_gateway.api import create_app
from tests.gateway.test_auth import auth_wire, config, device, jwt, state
from tests.gateway.test_auth_transports import frame
from tests.gateway.test_transports import sse, native_text


def callback_config():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return 'http://127.0.0.1:'+str(sock.getsockname()[1])+'/auth/callback'


def provider_server(provider):
    def reply(request,count):
        path=request['path']
        now=time.time()
        if path=='/device': return device()
        if path=='/client/register': return {'clientId':'registered','clientSecret':'registration-secret','clientSecretExpiresAt':now+90000}
        if path=='/device_authorization': return {'deviceCode':'device-secret','userCode':'AWS-CODE','verificationUri':'https://device.awsapps.com','expiresIn':600,'interval':5}
        if path=='/token':
            if provider=='codex': return {'access_token':jwt({'account_id':'native-codex-account'}),'refresh_token':'rotating-secret','expires_in':3600}
            if provider=='kiro': return {'accessToken':'kiro-secret','refreshToken':'refresh-secret','expiresIn':3600,'profileArn':'arn:aws:codewhisperer:us-east-1:123:profile/native'}
            return {'access_token':'provider-access-secret','refresh_token':'provider-refresh-secret','expires_in':3600}
        if path=='/copilot': return {'token':'copilot-secret','expires_at':now+7200}
        if path=='/v1internal:loadCodeAssist': return {'cloudaicompanionProject':'actual-project'}
        if path=='/models':
            if provider=='codex': return {'models':[{'slug':'gpt-native','display_name':'Native GPT'}]}
            if provider=='github-copilot': return {'data':[{'id':'claude-native','capabilities':{'type':'chat','supports':{'tool_calls':True}}}]}
            if provider=='kiro': return {'models':[{'modelId':'claude-native','modelName':'Native Claude'}]}
            return {'models':{'gemini-native':{'displayName':'Native Gemini'}}}
        if path=='/responses': return native_text('openai-responses')
        if path=='/v1/messages': return native_text('anthropic')
        if path=='/v1internal:streamGenerateContent?alt=sse': return sse({'response':{'candidates':[{'index':0,'content':{'parts':[{'text':'Native account answer'}]},'finishReason':'STOP'}]}})
        if path=='/generateAssistantResponse': return frame('assistantResponseEvent',{'content':'Native account answer'})+frame('messageStopEvent',{})
        return (404,{'error':'unexpected_test_endpoint'})
    return reply


@asynccontextmanager
async def authenticated_gateway(tmp_path,provider):
    async with auth_wire(provider_server(provider)) as (url,policy,requests):
        configs=config(url)
        callback=callback_config()
        configs[provider]['redirect_uri']=callback
        app=create_app(tmp_path,public_origin='http://gateway.test',network_policy=policy,auth_configs=configs)
        gateway=app.state.gateway
        now=[time.time()]
        gateway.auth.clock=lambda:now[0]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://gateway.test') as client:
            client.headers['Authorization']='Bearer '+app.state.auth.key_path.read_text()
            try:
                yield client,gateway,now,url,requests
            finally:
                await gateway.close()


@pytest.mark.parametrize('provider',['github-copilot','codex','gemini-cli','kiro'])
async def test_full_native_account_http_journey(tmp_path,provider):
    async with authenticated_gateway(tmp_path,provider) as (client,gateway,now,url,requests):
        descriptors=(await client.get('/api/v1/gateway/auth/providers')).json()['data']
        assert any(item['id']==provider for item in descriptors)
        started=await client.post('/api/v1/gateway/auth/start',json={'provider_id':provider,'name':'My account'})
        assert started.status_code==200,started.text
        session=started.json()['data']
        if session['flow']=='device_code':
            now[0]+=5
        elif provider=='codex':
            assert session['callback_mode']=='automatic'
            async with httpx.AsyncClient(trust_env=False) as browser:
                wrong=await browser.get(session['redirect_uri']+'?'+urlencode({'state':'wrong','code':'secret-code'}))
                assert wrong.status_code==400 and not requests
                valid=await browser.get(session['redirect_uri']+'?'+urlencode({'state':state(session),'code':'secret-code'}))
                assert valid.status_code==200
                assert 'secret-code' not in valid.text and state(session) not in valid.text
        else:
            completed=await client.post(f"/api/v1/gateway/auth/{session['id']}/complete",json={'state':state(session),'code':'secret-code'})
            assert completed.status_code==200,completed.text
        completed=await client.post(f"/api/v1/gateway/auth/{session['id']}/poll",json={})
        assert completed.status_code==200,completed.text
        connected=completed.json()['data']
        assert connected['status']=='connected'
        account_id=connected['account_id']
        accounts=(await client.get('/api/v1/gateway/auth/accounts')).json()['data']
        assert accounts[0]['id']==account_id and accounts[0]['name']=='My account'
        assert all(secret not in json.dumps(accounts) for secret in ['secret_ref','access_token','refresh_token','provider-access-secret'])
        imported=await client.post('/api/v1/gateway/discover',json={'preset_id':provider,'auth_account_id':account_id})
        assert imported.status_code==200,imported.text
        catalog=imported.json()['data']; assert catalog['source']=='live' and len(catalog['models'])==1
        upstream_model=catalog['models'][0]['id']
        connected_response=await client.post('/api/v1/gateway/connections',json={'preset_id':provider,'name':'Account models','auth_account_id':account_id,'models':[upstream_model]})
        assert connected_response.status_code==200,connected_response.text
        connection=connected_response.json()['data']
        assert connection['auth_account_id']==account_id and 'secret_ref' not in connection
        model=connection['id']+'/'+upstream_model
        key_response=await client.post('/api/v1/gateway/keys',json={'name':'External app','models':[model]})
        key=key_response.json()['data']['key']
        management=client.headers['Authorization']; client.headers['Authorization']='Bearer '+key
        models=await client.get('/v1/models')
        assert models.status_code==200 and models.json()['data'][0]['id']==model
        response=await client.post('/v1/chat/completions',json={'model':model,'messages':[{'role':'user','content':'Hello'}]})
        assert response.status_code==200,response.text
        assert response.json()['choices'][0]['message']['content'] in {'Hi there','Native account answer'}
        stream=await client.post('/v1/chat/completions',json={'model':model,'messages':[{'role':'user','content':'Again'}],'stream':True})
        assert stream.status_code==200 and 'data: [DONE]' in stream.text
        assert not any(secret in response.text+stream.text for secret in ['provider-access-secret','kiro-secret','copilot-secret','rotating-secret'])
        client.headers['Authorization']=management
        usage=(await client.get('/api/v1/gateway/usage')).json()['data']
        assert usage['requests']==2
        assert not (tmp_path/'runtimes').exists()
        if provider in {'codex','gemini-cli','kiro'}:
            now[0]+=3601
            client.headers['Authorization']='Bearer '+key
            refreshed=await client.post('/v1/chat/completions',json={'model':model,'messages':[{'role':'user','content':'After refresh'}]})
            assert refreshed.status_code==200,refreshed.text
            assert len([r for r in requests if r['path']=='/token'])==2
        client.headers['Authorization']=management
        latest=(await client.get('/api/v1/gateway/auth/accounts')).json()['data'][0]
        stale=await client.request('DELETE','/api/v1/gateway/auth/accounts/'+account_id,json={'expected_revision':0})
        assert stale.status_code==409
        disconnected=await client.request('DELETE','/api/v1/gateway/auth/accounts/'+account_id,json={'expected_revision':latest['revision']})
        assert disconnected.status_code==200,disconnected.text
        assert (await client.get('/api/v1/gateway/auth/accounts')).json()['data']==[]
        count=len(requests)
        client.headers['Authorization']='Bearer '+key
        stopped=await client.post('/v1/chat/completions',json={'model':model,'messages':[{'role':'user','content':'After disconnect'}]})
        assert stopped.status_code==503 and len(requests)==count


async def test_account_session_cannot_be_used_for_different_provider_or_endpoint(tmp_path):
    async with authenticated_gateway(tmp_path,'github-copilot') as (client,gateway,now,url,requests):
        started=(await client.post('/api/v1/gateway/auth/start',json={'provider_id':'github-copilot'})).json()['data'];now[0]+=5
        connected=(await client.post(f"/api/v1/gateway/auth/{started['id']}/poll",json={})).json()['data']
        for body in [{'preset_id':'codex','auth_account_id':connected['account_id']},
                     {'preset_id':'github-copilot','auth_account_id':connected['account_id'],'endpoint':'https://untrusted.test'},
                     {'preset_id':'github-copilot','auth_account_id':'another-tenant-account'}]:
            count=len(requests)
            response=await client.post('/api/v1/gateway/discover',json=body)
            assert response.status_code in {400,404},response.text
            assert len(requests)==count
        assert (await client.post('/api/v1/gateway/auth/start',json={'provider_id':'codex','tenant':'other'})).status_code==400


async def test_cancelled_http_signin_cannot_exchange_code(tmp_path):
    async with authenticated_gateway(tmp_path,'codex') as (client,gateway,now,url,requests):
        started=(await client.post('/api/v1/gateway/auth/start',json={'provider_id':'codex'})).json()['data']
        cancelled=await client.delete('/api/v1/gateway/auth/'+started['id'])
        assert cancelled.status_code==200 and cancelled.json()['data']['status']=='cancelled'
        complete=await client.post('/api/v1/gateway/auth/'+started['id']+'/complete',json={'state':state(started),'code':'cancelled-code'})
        assert complete.status_code==409 and not requests
        poll=await client.post('/api/v1/gateway/auth/'+started['id']+'/poll',json={})
        assert poll.json()['data']['status']=='cancelled'
        assert not gateway.callbacks.sessions
