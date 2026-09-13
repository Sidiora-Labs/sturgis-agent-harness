"""Configuration transfer, operator ceilings and atomic admission qualification."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json

import pytest

from model_gateway.errors import GatewayError
from model_gateway.network import NetworkPolicy
from model_gateway.service import ModelGateway
from model_gateway.store import GatewayStore
from tests.gateway.test_api import installation, setup


def archive(endpoint='https://provider.example/v1'):
    return {'format':'sturgis-gateway-config/1','created_at':1000,'credentials_included':False,
            'connections':[{'id':'conn_old','name':'Provider','preset_id':'custom-openai','endpoint':endpoint,'protocol':'openai',
                            'models':[{'id':'org/model-a','name':'Model A','context_limit':8192,'output_limit':1024,'tools':True,'protocol':'openai',
                                       'limits_source':'provider_reported','tools_source':'provider_reported'}],
                            'selected_model':'org/model-a','catalog_mode':'discover'}],
            'routes':[{'id':'everyday','name':'Everyday','models':['conn_old/org/model-a'],'strategy':'priority'}],
            'settings':{'request_timeout':120,'max_concurrency':16,'requests_per_minute':120}}


def gateway(tmp_path, *, timeout=30, concurrency=16):
    value=ModelGateway(tmp_path,network_policy=NetworkPolicy(timeout=timeout),max_concurrency=concurrency)
    value.controls.clock=lambda:1000
    return value


def save(controls,tenant='alice',revision=0,**extra):
    return controls.save_settings(tenant,expected_revision=revision,request_timeout=extra.get('request_timeout',10),
                                 max_concurrency=extra.get('max_concurrency',2),requests_per_minute=extra.get('requests_per_minute',3))


def test_settings_defaults_cas_and_operator_reduction(tmp_path):
    first=gateway(tmp_path,timeout=120,concurrency=32)
    initial=first.controls.settings('alice')
    assert initial=={'revision':0,'request_timeout':120,'max_concurrency':32,'requests_per_minute':120,
                     'operator_limits':{'request_timeout':120,'max_concurrency':32}}
    saved=save(first.controls,request_timeout=60,max_concurrency=20)
    assert saved['revision']==1 and saved['request_timeout']==60
    with pytest.raises(GatewayError) as conflict:
        save(first.controls,revision=0)
    assert conflict.value.code=='conflict'
    second=gateway(tmp_path,timeout=15,concurrency=4)
    effective=second.controls.settings('alice')
    assert effective['request_timeout']==15 and effective['max_concurrency']==4 and effective['revision']==1
    assert second.store.get('alice','settings','active')['request_timeout']==60
    assert second.controls.export('alice')['settings']['request_timeout']==15
    assert first.controls.settings('bob')['revision']==0
    effective['operator_limits']['max_concurrency']=999
    assert second.controls.settings('alice')['operator_limits']['max_concurrency']==4


@pytest.mark.parametrize('extra',[{'request_timeout':0},{'request_timeout':31},{'request_timeout':True}, {'request_timeout':'5'},
    {'request_timeout':float('nan')},{'request_timeout':float('inf')},{'request_timeout':10**1000},
    {'max_concurrency':0},{'max_concurrency':17},{'max_concurrency':True},{'max_concurrency':1.1},
    {'requests_per_minute':0},{'requests_per_minute':10001},{'requests_per_minute':True},{'requests_per_minute':'100'}])
def test_settings_reject_invalid_or_excessive_authority(tmp_path,extra):
    service=gateway(tmp_path)
    with pytest.raises(GatewayError) as error:
        save(service.controls,**extra)
    assert error.value.code=='invalid_settings' and service.controls.settings('alice')['revision']==0


def test_settings_non_not_found_storage_failure_is_not_hidden(tmp_path,monkeypatch):
    service=gateway(tmp_path)
    def fail(*args): raise GatewayError('unavailable','Storage failed.',503)
    monkeypatch.setattr(service.store,'get',fail)
    with pytest.raises(GatewayError) as error:
        service.controls.settings('alice')
    assert error.value.code=='unavailable'


def test_rate_windows_settings_updates_and_clock_rollback(tmp_path):
    service=gateway(tmp_path); now=[119.9];service.controls.clock=lambda:now[0]
    settings=save(service.controls,requests_per_minute=2)
    service.controls.admit('alice');service.controls.admit('alice')
    with pytest.raises(GatewayError) as limited:
        service.controls.admit('alice')
    assert limited.value.status==429 and limited.value.retryable
    count=service.store.get('alice','rate_window','active')
    assert count['count']==2 and count['revision']==2
    now[0]=60
    with pytest.raises(GatewayError): service.controls.admit('alice')
    save(service.controls,revision=settings['revision'],requests_per_minute=3)
    service.controls.admit('alice')
    assert service.store.get('alice','rate_window','active')['count']==3
    with pytest.raises(GatewayError): service.controls.admit('alice')
    service.controls.admit('bob')
    now[0]=120
    service.controls.admit('alice')
    assert service.store.get('alice','rate_window','active')['count']==1


def test_concurrent_instances_cannot_overadmit(tmp_path):
    services=[gateway(tmp_path) for _ in range(4)]
    save(services[0].controls,requests_per_minute=7)
    def attempt(index):
        try: return services[index%4].controls.admit('alice')
        except GatewayError as error: return error.code
    with ThreadPoolExecutor(max_workers=12) as pool:
        results=list(pool.map(attempt,range(30)))
    assert sum(isinstance(item,dict) for item in results)==7
    assert results.count('rate_limited')==23
    assert services[0].store.get('alice','rate_window','active')['count']==7


@pytest.mark.parametrize('value',[{'window':1,'count':-1},{'window':-1,'count':1},{'window':1,'count':True},{'window':1,'count':10001}, {'count':1},[]])
def test_corrupt_rate_state_fails_closed(tmp_path,value):
    service=gateway(tmp_path)
    if isinstance(value,dict):
        service.store.put('alice','rate_window','active',value)
    else:
        with service.store.transaction() as db:
            db.execute("INSERT INTO objects VALUES ('alice','rate_window','active',1,'[]')")
    with pytest.raises(GatewayError) as error:
        service.controls.admit('alice')
    assert error.value.code=='invalid_rate_state'


def test_export_is_consistent_tenant_scoped_and_never_contains_credentials(tmp_path):
    service=gateway(tmp_path)
    entry=archive()['connections'][0]
    entry['models'][0]['secret_ref']='nested-secret-marker'
    service.store.put('alice','connection','conn_old',{**entry,'auth_account_id':'private-account','status':'connected','error':None},secret='upstream-credential')
    service.store.put('alice','route','everyday',archive()['routes'][0])
    service.store.put('alice','auth_account','private-account',{'provider_id':'codex','name':'Private'},secret='oauth-token')
    service.store.put('alice','auth_session','private-session',{'device_code':'private-device'},secret='verifier')
    key=service.store.create_key('alice','Private client')
    service.store.put('bob','connection','conn_bob',{**entry,'name':'Other tenant'})
    save(service.controls,requests_per_minute=20)
    exported=service.controls.export('alice')
    serialized=json.dumps(exported)
    assert exported['credentials_included'] is False and len(exported['connections'])==1 and len(exported['routes'])==1
    assert exported['settings']['requests_per_minute']==20
    for secret in ['upstream-credential','oauth-token','verifier','private-device','private-account','secret_ref','nested-secret-marker',key['key'],'Other tenant']:
        assert secret not in serialized
    imported=service.controls.import_configuration('carol',exported)
    assert imported['imported_connections']==1


def test_import_remaps_routes_and_remains_disabled_without_network_authority(tmp_path):
    service=gateway(tmp_path)
    save(service.controls,requests_per_minute=11)
    policy_before=service.network.private_origins
    value=archive('http://127.0.0.1:9999/v1/chat/completions')
    value['connections'].append({**deepcopy(value['connections'][0]),'id':'conn_second','name':'Second'})
    value['routes'][0]['models'].append('conn_second/org/model-a')
    imported=service.controls.import_configuration('alice',value)
    assert imported['imported_connections']==2 and imported['imported_routes']==1
    assert imported['settings_applied'] is False and imported['reconnection_required'] is True
    assert service.network.private_origins==policy_before and service.controls.settings('alice')['requests_per_minute']==11
    rows=service.connections('alice')
    assert all(row['status']=='error' and row['error']['code']=='reconnect_required' and row['auth_account_id'] is None and not row['credential_configured'] for row in rows)
    assert all(row['endpoint']=='http://127.0.0.1:9999/v1' for row in rows)
    assert service.models('alice')==[] and service.store.keys('alice')==[]
    route=service.store.list('alice','route')[0]
    assert route['models']==[imported['connection_ids'][old]+'/org/model-a' for old in ['conn_old','conn_second']]
    assert route['id']!='everyday'
    assert value['connections'][0]['endpoint'].endswith('/chat/completions')
    again=service.controls.import_configuration('alice',value)
    assert again['connection_ids']!=imported['connection_ids'] and len(service.connections('alice'))==4


@pytest.mark.parametrize('provider,protocol,model_protocol',[('gemini-cli','gemini-cli','gemini-cli'),('kiro','kiro','kiro'),
    ('github-copilot','openai','anthropic'),('codex','openai-responses','openai-responses')])
def test_native_account_metadata_import_requires_reconnection(tmp_path,provider,protocol,model_protocol):
    service=gateway(tmp_path);value=archive();row=value['connections'][0]
    row.update(preset_id=provider,protocol=protocol,endpoint=service._preset(provider)['endpoint'])
    row['models'][0]['protocol']=model_protocol
    imported=service.controls.import_configuration('alice',value)
    saved=service.store.get('alice','connection',imported['connection_ids']['conn_old'])
    assert saved['auth_account_id'] is None and saved['status']=='error'
    assert saved['models'][0]['protocol']==model_protocol


@pytest.mark.parametrize('field,value',[('format','other/1'),('credentials_included',True),('created_at',True),('created_at',-1),('created_at',10**20),
    ('connections',{}),('connections',[{}]*257),('routes',{}),('routes',[{}]*257),('settings',{}),('settings',{'request_timeout':400,'max_concurrency':1,'requests_per_minute':1})])
def test_invalid_archive_envelope_is_atomic(tmp_path,field,value):
    service=gateway(tmp_path);body=archive();body[field]=value
    with pytest.raises(GatewayError): service.controls.import_configuration('alice',body)
    assert service.store.list('alice','connection')==[] and service.store.list('alice','route')==[]


@pytest.mark.parametrize('value',[None,[],{'invalid':True},float('nan'),'x'*(2*1024*1024), {'x':'\ud800'}])
def test_nonportable_or_oversized_json_is_rejected(tmp_path,value):
    with pytest.raises(GatewayError) as error:
        gateway(tmp_path).controls.import_configuration('alice',value)
    assert error.value.code=='invalid_import'


@pytest.mark.parametrize('endpoint',['ws://example.test/v1','https://user:secret@example.test/v1','https://example.test/v1?key=secret',
    'https://example.test/v1#fragment','https://example.test\\@evil.test','not a URL'])
def test_import_rejects_ambiguous_endpoints_without_granting_network(tmp_path,endpoint):
    service=gateway(tmp_path)
    with pytest.raises(GatewayError): service.controls.import_configuration('alice',archive(endpoint))
    assert not service.connections('alice') and not service.network.private_origins


@pytest.mark.parametrize('field,value',[('id','bad/id'),('id',' has-space'),('id','has space'),('id','bad\\id'),('protocol','anthropic'),
    ('preset_id','unknown'),('selected_model','missing'),('selected_model',[]),('catalog_mode','unknown'),('api_key','secret')])
def test_invalid_connection_records_are_rejected(tmp_path,field,value):
    body=archive();body['connections'][0][field]=value
    with pytest.raises(GatewayError): gateway(tmp_path).controls.import_configuration('alice',body)


@pytest.mark.parametrize('models',[[],[{}]*257,[17],[{}],[{'id':'x','secret_ref':'stolen'}],[{'id':' x'}],[{'id':'x\x7f'}],
    [{'id':'x','context_limit':127}],[{'id':'x','context_limit':True}],[{'id':'x','output_limit':8192}],
    [{'id':'x','output_limit':0}],[{'id':'x','tools':'true'}],[{'id':'x','protocol':[]}],[{'id':'x','protocol':'unknown'}],
    [{'id':'x','limits_source':'invented'}],[{'id':'x','tools_source':'invented'}],[{'id':'x'},{'id':'x'}]])
def test_import_model_schema_rejects_ambiguity(tmp_path,models):
    body=archive();body['connections'][0]['models']=models
    with pytest.raises(GatewayError): gateway(tmp_path).controls.import_configuration('alice',body)


@pytest.mark.parametrize('mutation',[
    lambda a:a['connections'].append(deepcopy(a['connections'][0])),
    lambda a:a['connections'].__setitem__(0,17),
    lambda a:a['connections'][0].pop('protocol'),
    lambda a:a['routes'][0].update(models=['outside/model']),
    lambda a:a['routes'][0].update(models=['conn_old/org/model-a']*2),
    lambda a:a['routes'][0].update(strategy=[]),
    lambda a:a['routes'][0].update(id='bad/route'),
    lambda a:a['routes'][0].update(id='conn_forbidden'),
    lambda a:a['routes'].append(deepcopy(a['routes'][0])),
    lambda a:a['routes'].__setitem__(0,17),
])
def test_late_validation_failure_writes_no_partial_configuration(tmp_path,mutation):
    service=gateway(tmp_path);value=archive();mutation(value)
    with pytest.raises(GatewayError): service.controls.import_configuration('alice',value)
    assert not service.connections('alice') and not service.store.list('alice','route')


def test_actual_sql_collision_rolls_back_all_prior_inserts(tmp_path,monkeypatch):
    service=gateway(tmp_path)
    service.store.put('alice','connection','conn_taken',{'name':'Existing'})
    value=archive();value['routes']=[]
    value['connections'].append({**deepcopy(value['connections'][0]),'id':'second'})
    ids=iter(['new','taken'])
    monkeypatch.setattr('model_gateway.controls.secrets.token_hex',lambda _:next(ids))
    with pytest.raises(GatewayError) as error:
        service.controls.import_configuration('alice',value)
    assert error.value.code=='import_conflict'
    assert [row['id'] for row in service.store.list('alice','connection')]==['conn_taken']


def test_export_bounds_and_tenant_validation(tmp_path):
    service=gateway(tmp_path)
    for i in range(257):
        service.store.put('alice','route',str(i),{'name':'Route','models':[],'strategy':'priority'})
    with pytest.raises(GatewayError) as error: service.controls.export('alice')
    assert error.value.code=='export_bounds'
    for operation in [lambda:service.controls.export(None),lambda:service.controls.admit(None),lambda:service.controls.import_configuration(None,archive())]:
        with pytest.raises(GatewayError):operation()


async def test_http_settings_rate_limit_export_import_and_explicit_reconnect(tmp_path):
    async with installation(tmp_path) as (client,app,url,requests):
        connection,key=await setup(client,app,url)
        settings=(await client.get('/api/v1/gateway/settings')).json()['data']
        desired={'expected_revision':settings['revision'],'request_timeout':2,'max_concurrency':2,'requests_per_minute':1}
        saved=await client.post('/api/v1/gateway/settings',json=desired)
        assert saved.status_code==200 and saved.json()['data']['revision']==1
        assert (await client.post('/api/v1/gateway/settings',json=desired)).status_code==409
        management=client.headers['Authorization'];client.headers['Authorization']='Bearer '+key['key']
        body={'model':connection['id']+'/model-a','messages':[{'role':'user','content':'Hello'}]}
        assert (await client.post('/v1/chat/completions',json=body)).status_code==200
        count=len(requests)
        assert (await client.post('/v1/chat/completions',json=body)).status_code==429 and len(requests)==count
        client.headers['Authorization']=management
        exported=(await client.get('/api/v1/gateway/export')).json()['data']
        assert 'upstream-secret' not in json.dumps(exported) and key['key'] not in json.dumps(exported)
        bad=deepcopy(exported);bad['connections'][0]['models'][0]['tools']='unsafe'
        assert (await client.post('/api/v1/gateway/import',json={'archive':bad})).status_code==400
        assert len((await client.get('/api/v1/gateway/connections')).json()['data'])==1
        imported=await client.post('/api/v1/gateway/import',json={'archive':exported})
        assert imported.status_code==200,imported.text
        assert len(requests)==count
        new_id=imported.json()['data']['connection_ids'][connection['id']]
        assert (await client.post(f'/api/v1/gateway/connections/{new_id}/refresh',json={'expected_revision':1})).status_code==409
        models=(await client.get('/api/v1/gateway/models')).json()['data']
        assert all(not model['id'].startswith(new_id+'/') for model in models)
        reconnected=await client.patch('/api/v1/gateway/connections/'+new_id,json={'preset_id':'custom-openai','name':'Restored','endpoint':url+'/v1',
                    'api_key':'fresh-key','models':['model-a'],'expected_revision':1})
        assert reconnected.status_code==200,reconnected.text
        assert reconnected.json()['data']['status']=='connected'
        assert app.state.gateway.controls.settings('local')['requests_per_minute']==1


async def test_controls_http_require_management_and_reject_extra_fields(tmp_path):
    async with installation(tmp_path) as (client,app,url,requests):
        for method,path,body in [('GET','export',None),('GET','settings',None),('POST','import',{'archive':archive()})]:
            assert (await client.request(method,'/api/v1/gateway/'+path,json=body)).status_code==401
        client.headers['Authorization']='Bearer '+app.state.auth.key_path.read_text()
        response=await client.post('/api/v1/gateway/settings',json={'expected_revision':0,'request_timeout':999,'max_concurrency':1,'requests_per_minute':1})
        assert response.status_code==400
        assert (await client.post('/api/v1/gateway/import',json={'archive':archive(),'tenant':'other'})).status_code==400
        assert not requests
