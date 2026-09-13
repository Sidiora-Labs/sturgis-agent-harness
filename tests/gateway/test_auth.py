"""Native sign-in wire qualification with tenant, restart and refresh race checks."""
import asyncio
import base64
from contextlib import asynccontextmanager
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from model_gateway.auth import GatewayAuth, _claims, _number, _token
from model_gateway.auth_state import claim_refresh, finish_refresh, finish_session
from model_gateway.errors import GatewayError
from model_gateway.network import NetworkPolicy
from model_gateway.store import GatewayStore


@asynccontextmanager
async def auth_wire(replies):
    requests, tasks = [], set()
    async def handle(reader, writer):
        tasks.add(asyncio.current_task())
        try:
            raw = await reader.readuntil(b'\r\n\r\n')
            first, *lines = raw.decode().split('\r\n')
            headers = dict((line.split(': ', 1)[0].lower(), line.split(': ', 1)[1]) for line in lines if ': ' in line)
            raw_body = await reader.readexactly(int(headers.get('content-length', 0)))
            body = (parse_qs(raw_body.decode()) if 'application/x-www-form-urlencoded' in headers.get('content-type', '') else json.loads(raw_body)) if raw_body else None
            request = {'method': first.split()[0], 'path': first.split()[1], 'headers': headers, 'body': body}
            requests.append(request)
            reply = replies(request, len(requests)) if callable(replies) else replies[len(requests)-1]
            if asyncio.iscoroutine(reply):
                reply = await reply
            status, content = reply if isinstance(reply, tuple) else (200, reply)
            encoded = content if isinstance(content, bytes) else json.dumps(content).encode()
            writer.write(f'HTTP/1.1 {status} Test\r\nContent-Type: application/json\r\nContent-Length: {len(encoded)}\r\nConnection: close\r\n\r\n'.encode() + encoded)
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            tasks.discard(asyncio.current_task())
    server = await asyncio.start_server(handle, '127.0.0.1', 0)
    endpoint = f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}'
    try:
        yield endpoint, NetworkPolicy(private_origins=[endpoint], timeout=2), requests
    finally:
        server.close()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await server.wait_closed()


def jwt(claims):
    encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
    return 'header.' + encoded + '.signature'


def config(url):
    return {p: {'device_url': url+'/device', 'token_url': url+'/token', 'account_token_url': url+'/copilot',
                'endpoint': url+'/v1internal' if p == 'gemini-cli' else url, 'profile_url': url+'/profiles',
                'oidc_endpoint': url, 'models_endpoint': url+'/models'} for p in ('github-copilot','codex','gemini-cli','kiro')}


def device(**extra):
    return {'device_code':'device-private', 'user_code':'ABCD-EFGH', 'verification_uri':'https://github.com/login/device', 'expires_in':600, 'interval':5, **extra}


def state(session):
    return parse_qs(urlsplit(session['authorization_url']).query)['state'][0]


def seed(store, provider='codex', **tokens):
    value = {'access_token':'old-access', 'refresh_token':'old-refresh', 'expires_at':900,
             'chatgpt_account_id':'chatgpt-account', **tokens}
    return store.put('tenant','auth_account','account-a', {'provider_id':provider,'name':'Account','status':'connected','expires_at':value['expires_at']}, secret=json.dumps(value))


async def test_codex_pkce_restart_state_and_atomic_completion(tmp_path):
    replies = [{'access_token':jwt({'https://api.openai.com/auth':{'chatgpt_account_id':'acct-1'}}), 'id_token':jwt({'email':'private@example.test'}), 'refresh_token':'refresh-secret','expires_in':3600}]
    async with auth_wire(replies) as (url, policy, requests):
        store = GatewayStore(tmp_path)
        auth = GatewayAuth(store, policy, configs=config(url), clock=lambda:1000)
        session = await auth.start('tenant','codex')
        params = parse_qs(urlsplit(session['authorization_url']).query)
        assert not requests and params['code_challenge_method'] == ['S256']
        auth = GatewayAuth(GatewayStore(tmp_path), policy, configs=config(url), clock=lambda:1000)
        with pytest.raises(GatewayError, match='does not match'):
            await auth.complete('tenant',session['id'],'wrong','private-code')
        with pytest.raises(GatewayError) as cross:
            await auth.complete('other',session['id'],state(session),'private-code')
        assert cross.value.status == 404
        complete = await auth.complete('tenant',session['id'],state(session),'private-code')
        assert complete['status'] == 'connected'
        assert await auth.complete('tenant',session['id'],state(session),'ignored') == complete
        verifier = requests[0]['body']['code_verifier'][0]
        assert base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=') == params['code_challenge'][0]
        assert requests[0]['body']['code'] == ['private-code']
        assert store.get('tenant','auth_session',session['id'])['secret_ref'] is None
        binding = await auth.credential('tenant',complete['account_id'])
        assert binding['headers']['ChatGPT-Account-ID'] == 'acct-1'
        public = json.dumps([complete, auth.accounts('tenant'),auth.providers()])
        assert all(secret not in public for secret in ['refresh-secret','private-code','private@example.test',verifier,'secret_ref'])
        assert b'refresh-secret' not in store.path.read_bytes()


async def test_device_poll_interval_slowdown_and_copilot_exchange(tmp_path):
    now = [1000]
    replies = [device(), {'error':'authorization_pending'}, {'error':'slow_down'},
               {'access_token':'github-secret'}, {'token':'copilot-secret','expires_at':5000}]
    async with auth_wire(replies) as (url, policy, requests):
        auth = GatewayAuth(GatewayStore(tmp_path),policy,configs=config(url),clock=lambda:now[0])
        session = await auth.start('tenant','github-copilot')
        assert session['poll_after'] == 5
        assert (await auth.poll('tenant',session['id']))['status'] == 'pending' and len(requests)==1
        now[0] += 5
        await auth.poll('tenant',session['id'])
        now[0] += 5
        slower = await auth.poll('tenant',session['id'])
        assert slower['poll_after'] == 10
        now[0] += 10
        result = await auth.poll('tenant',session['id'])
        binding = await auth.credential('tenant',result['account_id'])
        assert binding['credential'] == 'copilot-secret'
        assert requests[-1]['headers']['authorization'] == 'Bearer github-secret'
        assert requests[-2]['body']['device_code'] == ['device-private']
        assert 'device-private' not in json.dumps(result)
        assert (await auth.poll('tenant',session['id']))['status'] == 'connected'


async def test_gemini_onboarding_saved_between_polls_then_refresh(tmp_path):
    now = [1000]
    replies = [{'access_token':'google-access','refresh_token':'google-refresh','expires_in':3600},
               {'allowedTiers':[{'id':'confirmed-tier','isDefault':True}]}, {'done':False},
               {'done':True,'response':{'cloudaicompanionProject':{'id':'confirmed-project'}}},
               {'access_token':'google-new','expires_in':3600}]
    async with auth_wire(replies) as (url,policy,requests):
        store=GatewayStore(tmp_path)
        auth=GatewayAuth(store,policy,configs=config(url),clock=lambda:now[0])
        session=await auth.start('tenant','gemini-cli',options={'project_id':'requested-project'})
        assert (await auth.poll('tenant',session['id']))['status']=='pending'
        pending=await auth.complete('tenant',session['id'],state(session),'google-code')
        assert pending['status']=='pending'
        now[0]+=5
        auth=GatewayAuth(store,policy,configs=config(url),clock=lambda:now[0])
        assert (await auth.poll('tenant',session['id']))['status']=='pending'
        now[0]+=5
        connected=await auth.poll('tenant',session['id'])
        assert connected['status']=='connected'
        assert requests[2]['body']['tierId']=='confirmed-tier'
        assert requests[1]['body']['cloudaicompanionProject']=='requested-project'
        now[0]+=3600
        binding=await auth.credential('tenant',connected['account_id'])
        assert binding['project_id']=='confirmed-project' and binding['credential']=='google-new'
        assert requests[-1]['body']['refresh_token']==['google-refresh']
        assert requests[-1]['body']['client_secret']


async def test_kiro_registration_profile_and_refresh(tmp_path):
    now=[1000]
    replies=[{'clientId':'registered-id','clientSecret':'registered-secret','clientSecretExpiresAt':9000},
             {'deviceCode':'kiro-device','userCode':'AWS-CODE','verificationUri':'https://device.sso.us-east-1.amazonaws.com/',
              'verificationUriComplete':'https://device.sso.us-east-1.amazonaws.com/?code=AWS-CODE','expiresIn':600,'interval':5},
             {'accessToken':'kiro-access','refreshToken':'kiro-refresh','expiresIn':3600},
             {'profiles':[{'arn':'arn:aws:codewhisperer:us-east-1:123:profile/real'}]},
             {'accessToken':'kiro-new','refreshToken':'kiro-rotated','expiresIn':3600}]
    async with auth_wire(replies) as (url,policy,requests):
        auth=GatewayAuth(GatewayStore(tmp_path),policy,configs=config(url),clock=lambda:now[0])
        session=await auth.start('tenant','kiro')
        assert requests[0]['body']['clientType']=='public'
        now[0]+=5
        connected=await auth.poll('tenant',session['id'])
        assert connected['status']=='connected'
        binding=await auth.credential('tenant',connected['account_id'])
        assert binding['profile_arn'].endswith('/real') and binding['region']=='us-east-1'
        now[0]+=3600
        refreshed=await auth.credential('tenant',connected['account_id'])
        assert refreshed['credential']=='kiro-new'
        assert requests[-1]['body']['grantType']=='refresh_token'
        assert requests[-1]['body']['clientSecret']=='registered-secret'


async def test_refresh_singleflight_and_cross_instance_fence(tmp_path):
    entered,release=asyncio.Event(),asyncio.Event()
    async def reply(request,count):
        entered.set()
        await release.wait()
        return {'access_token':'rotated-access','refresh_token':'rotated-refresh','expires_in':3600}
    async with auth_wire(reply) as (url,policy,requests):
        store=GatewayStore(tmp_path); seed(store)
        auth=GatewayAuth(store,policy,configs=config(url),clock=lambda:1000)
        other=GatewayAuth(GatewayStore(tmp_path),policy,configs=config(url),clock=lambda:1000)
        first=asyncio.create_task(auth.credential('tenant','account-a'))
        await entered.wait()
        second=asyncio.create_task(auth.credential('tenant','account-a'))
        with pytest.raises(GatewayError) as busy:
            await other.credential('tenant','account-a')
        assert busy.value.code=='auth_refresh_busy'
        release.set()
        assert (await first)['credential']==(await second)['credential']=='rotated-access'
        assert len(requests)==1
        assert 'old-refresh' not in store.resolve('tenant',store.get('tenant','auth_account','account-a')['secret_ref'])


async def test_cancel_during_exchange_never_creates_account(tmp_path):
    entered,release=asyncio.Event(),asyncio.Event()
    async def reply(request,count):
        entered.set(); await release.wait()
        return {'access_token':jwt({'account_id':'codex-id'}),'refresh_token':'refresh','expires_in':3600}
    async with auth_wire(reply) as (url,policy,_):
        auth=GatewayAuth(GatewayStore(tmp_path),policy,configs=config(url),clock=lambda:1000)
        session=await auth.start('tenant','codex')
        pending=asyncio.create_task(auth.complete('tenant',session['id'],state(session),'code'))
        await entered.wait()
        assert auth.cancel('tenant',session['id'])['status']=='cancelled'
        release.set()
        assert (await pending)['status']=='cancelled'
        assert not auth.accounts('tenant')
        with pytest.raises(GatewayError):
            await auth.complete('tenant',session['id'],state(session),'code')


async def test_disconnect_during_refresh_cannot_restore_secret(tmp_path):
    entered,release=asyncio.Event(),asyncio.Event()
    async def reply(request,count):
        entered.set(); await release.wait()
        return {'access_token':'new','refresh_token':'rotated','expires_in':3600}
    async with auth_wire(reply) as (url,policy,_):
        store=GatewayStore(tmp_path); seed(store)
        auth=GatewayAuth(store,policy,configs=config(url),clock=lambda:1000)
        task=asyncio.create_task(auth.credential('tenant','account-a'))
        await entered.wait(); auth.disconnect('tenant','account-a'); release.set()
        with pytest.raises(GatewayError) as error:
            await task
        assert error.value.code=='auth_refresh_superseded'
        assert store.get('tenant','auth_account','account-a')['secret_ref'] is None
        with pytest.raises(GatewayError):
            await auth.credential('tenant','account-a')


@pytest.mark.parametrize('error,status',[('access_denied','failed'),('authorization_declined','failed'),('AccessDeniedException','failed'),('expired_token','expired'),('ExpiredTokenException','expired')])
async def test_device_terminal_errors_destroy_tokens(tmp_path,error,status):
    now=[1000]
    async with auth_wire([device(),(400,{'error':error})]) as (url,policy,_):
        auth=GatewayAuth(GatewayStore(tmp_path),policy,configs=config(url),clock=lambda:now[0])
        session=await auth.start('tenant','github-copilot'); now[0]+=5
        assert (await auth.poll('tenant',session['id']))['status']==status
        assert auth.store.get('tenant','auth_session',session['id'])['secret_ref'] is None


@pytest.mark.parametrize('result',[{'error':{'detail':'secret'}}, {'access_token':'bad token'}, {'access_token':'ok','expires_in':-1}])
async def test_malformed_token_response_is_safe(tmp_path,result):
    now=[1000]
    async with auth_wire([device(),(400 if 'error' in result else 200,result)]) as (url,policy,_):
        auth=GatewayAuth(GatewayStore(tmp_path),policy,configs=config(url),clock=lambda:now[0])
        session=await auth.start('tenant','github-copilot'); now[0]+=5
        with pytest.raises(GatewayError) as error:
            await auth.poll('tenant',session['id'])
        assert 'secret' not in error.value.message


async def test_expiry_and_registered_redirect_validation(tmp_path):
    now=[1000]
    auth=GatewayAuth(GatewayStore(tmp_path),NetworkPolicy(),clock=lambda:now[0])
    for redirect in ['https://evil.test/callback','http://localhost:1455/auth/callback?x=1',17]:
        with pytest.raises(GatewayError):
            await auth.start('tenant','codex',redirect_uri=redirect)
    session=await auth.start('tenant','codex')
    now[0]+=901
    assert auth.inspect('tenant',session['id'])['status']=='expired'
    assert auth.store.get('tenant','auth_session',session['id'])['secret_ref'] is None


@pytest.mark.parametrize('provider,options',[('missing',None),('kiro',[]),('codex',{'secret':'x'}),('kiro',{'region':'us-east-1.evil'}),('kiro',{'start_url':'https://evil.test'}),('kiro',{'start_url':17})])
async def test_invalid_options_do_not_contact_remote(tmp_path,provider,options):
    auth=GatewayAuth(GatewayStore(tmp_path),NetworkPolicy())
    with pytest.raises(GatewayError):
        await auth.start('tenant',provider,options=options)


async def test_transient_project_failure_keeps_exchanged_tokens_for_poll(tmp_path):
    replies=[{'access_token':'google','refresh_token':'refresh','expires_in':3600}, (503,{'error':'unavailable'}), {'cloudaicompanionProject':'actual-project'}]
    async with auth_wire(replies) as (url,policy,requests):
        auth=GatewayAuth(GatewayStore(tmp_path),policy,configs=config(url),clock=lambda:1000)
        session=await auth.start('tenant','gemini-cli')
        with pytest.raises(GatewayError):
            await auth.complete('tenant',session['id'],state(session),'code')
        assert auth.inspect('tenant',session['id'])['status']=='pending'
        assert (await auth.poll('tenant',session['id']))['status']=='connected'
        assert sum(r['path']=='/token' for r in requests)==1


async def test_failed_exchange_requires_new_signin(tmp_path):
    async with auth_wire([(400,{'error':'invalid_grant','secret':'redacted'})]) as (url,policy,_):
        auth=GatewayAuth(GatewayStore(tmp_path),policy,configs=config(url),clock=lambda:1000)
        session=await auth.start('tenant','codex')
        with pytest.raises(GatewayError):
            await auth.complete('tenant',session['id'],state(session),'code')
        assert auth.inspect('tenant',session['id'])['status']=='failed'


async def test_uncertain_refresh_is_not_replayed(tmp_path):
    async with auth_wire([(503,{'error':'upstream'})]) as (url,policy,requests):
        store=GatewayStore(tmp_path); seed(store)
        auth=GatewayAuth(store,policy,configs=config(url),clock=lambda:1000)
        with pytest.raises(GatewayError):
            await auth.credential('tenant','account-a')
        with pytest.raises(GatewayError) as error:
            await auth.credential('tenant','account-a')
        assert error.value.code=='account_reauth_required' and len(requests)==1


def test_durable_lease_expiry_and_atomic_cancel_fence(tmp_path):
    store=GatewayStore(tmp_path); row=seed(store)
    lease=claim_refresh(store,'tenant',row['id'],row['revision'],1000,60)
    with pytest.raises(GatewayError) as error:
        claim_refresh(GatewayStore(tmp_path),'tenant',row['id'],lease['revision'],1061,60)
    assert error.value.code=='account_reauth_required'
    finish_refresh(store,'tenant',lease,uncertain=True)
    session=store.put('tenant','auth_session','signin-cancel',{'status':'cancelled','expires_at':2000})
    assert finish_session(store,'tenant',session['id'],'orphan',{},'secret',1000)['status']=='cancelled'
    assert len(store.list('tenant','auth_account'))==1
    session=store.put('tenant','auth_session','signin-expire',{'status':'pending','expires_at':999},secret='temporary')
    assert finish_session(store,'tenant',session['id'],'orphan',{},'secret',1000)['status']=='expired'


@pytest.mark.parametrize('value',[None,'', 'has space','\nsecret',42,'a'*32769])
def test_token_boundaries(value):
    with pytest.raises(GatewayError):
        _token(value)


@pytest.mark.parametrize('value',[True,-1,0,float('inf'),float('nan'),'3600',10**1000])
def test_expiry_boundaries(value):
    with pytest.raises(GatewayError):
        _number(value,3600)


def test_claim_parsing_and_configuration(tmp_path):
    for token in [None,'x','x.%%%','x.W10.z',jwt([])]:
        assert _claims(token)=={}
    with pytest.raises(GatewayError):
        GatewayAuth(GatewayStore(tmp_path),NetworkPolicy(),configs={'fake':{}})


async def test_device_exchange_fenced_across_instances(tmp_path):
    now=[1000]; entered,release=asyncio.Event(),asyncio.Event()
    async def reply(request,count):
        if request['path']=='/device': return device()
        entered.set(); await release.wait()
        return {'error':'authorization_pending'}
    async with auth_wire(reply) as (url,policy,requests):
        store=GatewayStore(tmp_path)
        auth=GatewayAuth(store,policy,configs=config(url),clock=lambda:now[0])
        session=await auth.start('tenant','github-copilot'); now[0]+=5
        first=asyncio.create_task(auth.poll('tenant',session['id']))
        await entered.wait(); now[0]+=10
        other=GatewayAuth(GatewayStore(tmp_path),policy,configs=config(url),clock=lambda:now[0])
        assert (await other.poll('tenant',session['id']))['status']=='exchanging'
        assert len(requests)==2
        release.set(); assert (await first)['status']=='pending'


def test_account_disconnect_revision_checks(tmp_path):
    store=GatewayStore(tmp_path); row=seed(store)
    auth=GatewayAuth(store,NetworkPolicy())
    with pytest.raises(GatewayError) as error:
        auth.disconnect('tenant','account-a',expected_revision=0)
    assert error.value.code=='conflict' and store.resolve('tenant',row['secret_ref'])
    with pytest.raises(GatewayError):
        auth.disconnect('tenant','account-a',expected_revision=True)
    assert auth.disconnect('tenant','account-a',expected_revision=row['revision'])['status']=='disconnected'


async def test_kiro_token_profile_skips_unnecessary_profile_fetch(tmp_path):
    now=[1000]
    replies=[{'clientId':'id','clientSecret':'secret','clientSecretExpiresAt':9000},
             {'deviceCode':'device','userCode':'CODE','verificationUri':'https://device.awsapps.com','expiresIn':600},
             {'accessToken':'access','refreshToken':'refresh','expiresIn':3600,'profileArn':'arn:aws:codewhisperer:us-east-1:123:profile/from-token'}]
    async with auth_wire(replies) as (url,policy,requests):
        auth=GatewayAuth(GatewayStore(tmp_path),policy,configs=config(url),clock=lambda:now[0])
        session=await auth.start('tenant','kiro'); now[0]+=5
        connected=await auth.poll('tenant',session['id'])
        assert (await auth.credential('tenant',connected['account_id']))['profile_arn'].endswith('/from-token')
        assert len(requests)==3


async def test_forced_refresh_from_stale_failed_binding_does_not_rotate_twice(tmp_path):
    async with auth_wire([{'access_token':'fresh-access','refresh_token':'fresh-refresh','expires_in':3600}]) as (url,policy,requests):
        store=GatewayStore(tmp_path); row=seed(store,expires_at=8000)
        auth=GatewayAuth(store,policy,configs=config(url),clock=lambda:1000)
        failed=await auth.credential('tenant','account-a')
        first=await auth.credential('tenant','account-a',force_refresh=True,expected_revision=failed['account_revision'])
        second=await GatewayAuth(GatewayStore(tmp_path),policy,configs=config(url),clock=lambda:1000).credential('tenant','account-a',force_refresh=True,expected_revision=failed['account_revision'])
        assert first['credential']==second['credential']=='fresh-access'
        assert first['account_revision']==second['account_revision'] and len(requests)==1


async def test_durable_auth_storage_does_not_block_event_loop(tmp_path):
    import threading
    store=GatewayStore(tmp_path)
    auth=GatewayAuth(store,NetworkPolicy(),clock=lambda:1000)
    entered,release=threading.Event(),threading.Event()
    def hold_transaction():
        with store.transaction():
            entered.set(); release.wait(2)
    worker=asyncio.create_task(asyncio.to_thread(hold_transaction))
    await asyncio.to_thread(entered.wait,1)
    pending=asyncio.create_task(auth.start('tenant','codex'))
    try:
        await asyncio.wait_for(asyncio.sleep(.01),.2)
        assert not pending.done()
    finally:
        release.set()
        await worker
    assert (await pending)['status']=='pending'


@pytest.mark.parametrize('bad_state',[None,42,'\ud800','state with spaces','x'*257])
async def test_malformed_callback_state_is_sanitized_before_hashing(tmp_path,bad_state):
    auth=GatewayAuth(GatewayStore(tmp_path),NetworkPolicy(),clock=lambda:1000)
    session=await auth.start('tenant','codex')
    with pytest.raises(GatewayError) as error:
        await auth.complete('tenant',session['id'],bad_state,'unused-code')
    assert error.value.code=='invalid_auth_state'
