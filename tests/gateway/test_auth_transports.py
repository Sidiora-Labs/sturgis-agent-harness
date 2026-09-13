"""Authenticated native catalogs and inference over controlled real HTTP sockets."""
import asyncio
from contextlib import aclosing, asynccontextmanager
import json
import struct
import zlib

import pytest

from model_gateway.auth_transports import AuthTransport, aws_events, _event_headers, kiro_request
from model_gateway.errors import GatewayError
from tests.gateway.test_transports import wire, sse, payload, native_text


def binding(provider,endpoint,**extra):
    return {'provider_id':provider,'preset_id':provider,'credential':'native-account-token','endpoint':endpoint,
            'protocol':{'codex':'openai-responses','github-copilot':'openai','gemini-cli':'gemini-cli','kiro':'kiro'}[provider],
            'headers':{'ChatGPT-Account-ID':'account'} if provider=='codex' else {},
            'project_id':'actual-project','profile_arn':'arn:aws:codewhisperer:us-east-1:123:profile/actual','region':'us-east-1',**extra}


def header(name,value,kind=7):
    return bytes([len(name)]) + name.encode() + bytes([kind]) + (struct.pack('!H',len(value)) if kind in (6,7) else b'') + value


def frame(kind,data,*,message_type='event',extra_headers=b''):
    headers=header(':message-type',message_type.encode())+header(':event-type',kind.encode())+extra_headers
    body=json.dumps(data).encode()
    prelude=struct.pack('!II',16+len(headers)+len(body),len(headers))
    value=prelude+struct.pack('!I',zlib.crc32(prelude))+headers+body
    return value+struct.pack('!I',zlib.crc32(value))


async def collect(bind,policy,body=None,**kwargs):
    return [event async for event in AuthTransport(policy).stream(bind,body or payload(),**kwargs)]


@pytest.mark.parametrize('provider,protocol,path',[
    ('codex','openai-responses','/responses'),('github-copilot','openai','/chat/completions'),
    ('github-copilot','anthropic','/v1/messages'),('github-copilot','openai-responses','/responses')])
async def test_account_inference_delegates_native_protocol(provider,protocol,path):
    async with wire([native_text(protocol)]) as (url,policy,requests):
        events=await collect(binding(provider,url,model_protocol=protocol),policy)
        assert requests[0]['path']==path
        assert requests[0]['headers']['authorization']=='bearer native-account-token'
        assert 'x-api-key' not in requests[0]['headers']
        assert any(event['choices'] for event in events)
        if provider=='codex':
            assert requests[0]['body']['store'] is False
            assert requests[0]['headers']['chatgpt-account-id']=='account'


async def test_gemini_cli_wraps_requests_and_unwraps_sse():
    response={'response':{'candidates':[{'index':0,'content':{'parts':[{'text':'Real answer'}]},'finishReason':'STOP'}],
                          'usageMetadata':{'promptTokenCount':3,'candidatesTokenCount':2,'totalTokenCount':5}}}
    async with wire([sse(response)]) as (url,policy,requests):
        events=await collect(binding('gemini-cli',url+'/v1internal'),policy)
        assert requests[0]['path']=='/v1internal:streamGenerateContent?alt=sse'
        request=requests[0]['body']
        assert request['project']=='actual-project' and request['model']=='test-model'
        assert request['request']['contents'][0]['parts'][0]['text']=='Hello'
        assert events[-1]['usage']['total_tokens']==5
        assert 'x-goog-api-key' not in requests[0]['headers']


async def test_kiro_frames_text_usage_and_factual_completion():
    content=frame('assistantResponseEvent',{'content':'Hello'})+frame('codeEvent',{'content':' world'})+frame('reasoningContentEvent',{'text':'private'})+frame('meteringEvent',{'usage':0.1})+frame('usageEvent',{'input_tokens':3,'output_tokens':2})+frame('messageStopEvent',{'stopReason':'end_turn'})
    async with wire([content]) as (url,policy,requests):
        events=await collect(binding('kiro',url),policy)
        assert requests[0]['path']=='/generateAssistantResponse'
        assert requests[0]['body']['profileArn'].endswith('/actual')
        assert ''.join(c['delta'].get('content','') for event in events for c in event['choices'])=='Hello world'
        assert events[-2]['choices'][0]['finish_reason']=='stop'
        assert events[-1]['usage']=={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}
        assert 'private' not in json.dumps(events)


async def test_kiro_clean_http_eof_and_fragmented_tools():
    content=frame('toolUseEvent',{'toolUseId':'call-1','name':'lookup','input':'{"q":'})+frame('toolUseEvent',{'toolUseId':'call-1','name':'lookup','input':'"weather"}'})
    async with wire([content]) as (url,policy,_):
        events=await collect(binding('kiro',url),policy)
        fragments=[c['delta']['tool_calls'][0]['function']['arguments'] for event in events for c in event['choices'] if c['delta'].get('tool_calls')]
        assert json.loads(''.join(fragments))=={'q':'weather'}
        assert events[-1]['choices'][0]['finish_reason']=='tool_calls'


@pytest.mark.parametrize('reason,expected',[('max_tokens','length'),('model_context_window_exceeded','length'),('content_filter','content_filter')])
async def test_kiro_non_success_completion_reasons(reason,expected):
    content=frame('assistantResponseEvent',{'content':'partial'})+frame('metadataEvent',{'stop_reason':reason})
    async with wire([content]) as (url,policy,_):
        assert (await collect(binding('kiro',url),policy))[-1]['choices'][0]['finish_reason']==expected


@pytest.mark.parametrize('content,code',[
    (b'','provider_incomplete'),(frame('assistantResponseEvent',{'content':'x'})[:-1],'provider_incomplete'),
    (frame('assistantResponseEvent',{'content':'x'})[:-1]+b'x','provider_protocol'),
    (b'x'*16,'provider_protocol'),(frame('failure',{'message':'private'},message_type='exception'),'provider_error'),
    (frame('assistantResponseEvent',{'content':'x'},message_type='wrong'),'provider_protocol'),
    (frame('toolUseEvent',{'toolUseId':'a','name':'lookup','input':'{'}),'provider_protocol'),
    (frame('toolUseEvent',{'toolUseId':'a','name':'one','input':'{'})+frame('toolUseEvent',{'toolUseId':'a','name':'two','input':'}'}),'provider_protocol'),
    (frame('messageStopEvent',{'stopReason':'invented'}),'provider_protocol'),
    (frame('messageStopEvent',{})+frame('assistantResponseEvent',{'content':'late'}),'provider_protocol'),
    (frame('messageStopEvent',{})+frame('toolUseEvent',{'toolUseId':'x','name':'x','input':{}}),'provider_protocol'),
    (frame('messageStopEvent',{})+frame('messageStopEvent',{'stopReason':'max_tokens'}),'provider_protocol'),
    (frame('toolUseEvent',[]),'provider_protocol'),
])
async def test_kiro_malformed_or_incomplete_streams_fail(content,code):
    async with wire([content]) as (url,policy,_):
        with pytest.raises(GatewayError) as error:
            await collect(binding('kiro',url),policy)
        assert error.value.code==code
        assert 'private' not in error.value.message


@pytest.mark.parametrize('provider,rows,expected',[
    ('codex',{'models':[{'slug':'gpt-test','display_name':'GPT Test','context_window':120000},{'slug':'image-only','type':'image'}]},['gpt-test']),
    ('gemini-cli',{'models':{'gemini-test':{'displayName':'Gemini Test'},'hidden':{'isInternal':True}}},['gemini-test']),
    ('gemini-cli',{'models':[{'model':'gemini-test','displayName':'Gemini Test'}]},['gemini-test']),
    ('kiro',{'models':[{'modelId':'claude-test','modelName':'Claude Test'}]},['claude-test']),
    ('github-copilot',{'data':[{'id':'gpt-test','capabilities':{'type':'chat','supports':{'tool_calls':True},'limits':{'max_context_window_tokens':120000}}},
                              {'id':'claude-test','capabilities':{'type':'chat'}},{'id':'gpt-codex','capabilities':{'type':'chat'}},
                              {'id':'disabled','capabilities':{'type':'chat'},'policy':{'state':'disabled'}},
                              {'id':'embedding','capabilities':{'type':'embeddings'}}]},['gpt-test','claude-test','gpt-codex']),
])
async def test_live_catalog_discovery(provider,rows,expected):
    async with wire([rows]) as (url,policy,requests):
        bind=binding(provider,url,models_endpoint=url+'/actual-models')
        result=await AuthTransport(policy).discover(bind)
        assert [row['id'] for row in result]==expected
        assert requests[0]['path']=='/actual-models'
        if provider=='gemini-cli':
            assert requests[0]['method']=='POST' and requests[0]['body']=={'project':'actual-project'}
        if provider=='github-copilot':
            assert [row['protocol'] for row in result]==['openai','anthropic','openai-responses']
            assert result[0]['tools'] is True and result[0]['context_limit']==120000


@pytest.mark.parametrize('rows',[[],{}, {'models':'bad'}, {'models':[17]}, {'models':[{'id':''}]}, {'models':[{'id':str(i)} for i in range(2001)]}])
async def test_native_catalog_validation(rows):
    async with wire([rows]) as (url,policy,_):
        with pytest.raises(GatewayError):
            await AuthTransport(policy).discover(binding('codex',url))


@pytest.mark.parametrize('provider',['codex','gemini-cli','kiro'])
async def test_provider_http_failures_and_catalog_errors(provider):
    async with wire([(401,{'secret':'private'})]) as (url,policy,_):
        with pytest.raises(GatewayError) as error:
            await collect(binding(provider,url+'/v1internal' if provider=='gemini-cli' else url),policy)
        assert error.value.code=='provider_auth'
    async with wire([(429,{'secret':'private'})]) as (url,policy,_):
        with pytest.raises(GatewayError) as error:
            await AuthTransport(policy).discover(binding(provider,url,models_endpoint=url+'/models'))
        assert error.value.code=='provider_rate_limit'


async def test_gemini_incomplete_invalid_and_unknown_native_provider():
    for value in [sse({'response':{}}),sse(b'invalid json')]:
        async with wire([value]) as (url,policy,_):
            with pytest.raises(GatewayError):
                await collect(binding('gemini-cli',url),policy)
    async with wire([b'']) as (url,policy,_):
        with pytest.raises(GatewayError) as error:
            await collect({**binding('kiro',url),'provider_id':'unsupported'},policy)
        assert error.value.code=='unsupported_auth_provider'


async def test_cancellation_closes_native_stream_without_waiting_for_headers():
    hold=asyncio.Event()
    async with wire([frame('assistantResponseEvent',{'content':'x'})],delay_headers=hold) as (url,policy,requests):
        cancel=asyncio.Event()
        task=asyncio.create_task(collect(binding('kiro',url),policy,cancel=cancel))
        while not requests:
            await asyncio.sleep(.005)
        cancel.set()
        with pytest.raises(GatewayError) as error:
            await asyncio.wait_for(task,.5)
        assert error.value.code=='cancelled'


async def test_early_close_propagates_to_delegated_transport():
    class Closing:
        def __init__(self): self.closed=False
        async def stream(self,*args,**kwargs):
            try:
                yield {'sent':True}
                await asyncio.Event().wait()
            finally:
                self.closed=True
    auth=AuthTransport(None); delegate=Closing(); auth.transport=delegate
    async with aclosing(auth.stream(binding('codex','https://example.test'),payload())) as source:
        assert await anext(source)=={'sent':True}
    assert delegate.closed


def test_kiro_request_preserves_tools_images_history_and_limits():
    messages=[{'role':'system','content':'Be precise'},{'role':'user','content':[{'type':'text','text':'Inspect'},{'type':'image_url','image_url':{'url':'data:image/png;base64,aGk='}}]},
              {'role':'user','content':'And report'}, {'role':'assistant','content':'Checking','tool_calls':[{'id':'call-1','function':{'name':'lookup','arguments':'{"q":"x"}'}}]},
              {'role':'assistant','content':'Now'}, {'role':'tool','tool_call_id':'call-1','content':'Found'}, {'role':'user','content':'Summarize'}]
    body=kiro_request(payload(messages=messages,tools=[{'type':'function','function':{'name':'lookup','parameters':{'type':'object'}}}],temperature=.2,top_p=.8),'actual-profile')
    state=body['conversationState']; first=state['history'][0]['userInputMessage']
    assert first['content'].startswith('<instructions>\nBe precise') and 'And report' in first['content']
    assert first['images']==[{'format':'png','source':{'bytes':'aGk='}}]
    assistant=state['history'][1]['assistantResponseMessage']
    assert assistant['toolUses'][0]['input']=={'q':'x'} and assistant['content']=='Checking\n\nNow'
    context=state['currentMessage']['userInputMessage']['userInputMessageContext']
    assert context['tools'][0]['toolSpecification']['name']=='lookup'
    assert context['toolResults'][0]['content']==[{'text':'Found'}]
    assert body['inferenceConfig']=={'maxTokens':128,'temperature':.2,'topP':.8}


@pytest.mark.parametrize('messages',[
    [{'role':'assistant','content':'Trailing'}],
    [{'role':'tool','tool_call_id':'missing','content':'result'}],
    [{'role':'user','content':'Hello'},{'role':'assistant','content':'','tool_calls':[{'id':'a','function':{'name':'tool','arguments':'{}'}}]}],
    [{'role':'system','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,aGk='}}]},{'role':'user','content':'x'}],
    [{'role':'assistant','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,aGk='}}]},{'role':'user','content':'x'}],
    [{'role':'user','content':'x'},{'role':'assistant','content':'','tool_calls':[{'id':'a','function':{'name':'tool','arguments':'{}'}},{'id':'a','function':{'name':'tool','arguments':'{}'}}]},{'role':'user','content':'x'}],
])
def test_kiro_invalid_history_never_silently_repaired(messages):
    with pytest.raises(GatewayError):
        kiro_request(payload(messages=messages),'profile')


@pytest.mark.parametrize('extra',[{'response_format':{'type':'json_object'}},{'tool_choice':'required'},{'reasoning_effort':'high'}])
def test_kiro_unsupported_options_are_explicit(extra):
    with pytest.raises(GatewayError) as error:
        kiro_request(payload(**extra),'profile')
    assert error.value.code=='unsupported_capability'


def test_kiro_tool_choice_none_omits_tools():
    result=kiro_request(payload(tool_choice='none',tools=[{'type':'function','function':{'name':'x'}}]),'profile')
    assert 'userInputMessageContext' not in result['conversationState']['currentMessage']['userInputMessage']


@pytest.mark.parametrize('data',[b'\x00',b'\x05x',header('x',b'x',10),header('x',b'x')+header('x',b'x'),header(':event-type',b'\x00',2),b'\x01x\x07\x00',b'\x01x\x07\x00\x04a'])
def test_event_header_bounds_and_types(data):
    with pytest.raises(GatewayError):
        _event_headers(data)


def test_event_header_known_scalar_types_are_skipped_safely():
    data=b''.join(header(str(t),b'\x00'*size,t) for t,size in {0:0,1:0,2:1,3:2,4:4,5:8,8:8,9:16}.items())
    assert len(_event_headers(data))==8
    assert _event_headers(header('binary',b'\x01',6))['binary']==b'\x01'


async def test_aws_frame_split_at_every_byte():
    class Response:
        async def aiter_bytes(self):
            for byte in frame('assistantResponseEvent',{'content':'split'}):
                yield bytes([byte])
    assert [item async for item in aws_events(Response())]==[('assistantResponseEvent',{'content':'split'})]


async def test_native_request_deadline_is_per_call_and_closes_stream():
    hold=asyncio.Event()
    async with wire([frame('assistantResponseEvent',{'content':'slow'})],delay_headers=hold) as (url,policy,_):
        before=policy.timeout
        with pytest.raises(GatewayError) as error:
            await collect(binding('kiro',url),policy,request_timeout=.02)
        assert error.value.code=='timeout' and policy.timeout==before


@pytest.mark.parametrize('limit',[0,-1,True,float('nan'),float('inf'),1000,'30',10**1000])
async def test_native_deadline_cannot_increase_network_authority(limit):
    async with wire([b'']) as (url,policy,requests):
        with pytest.raises(GatewayError) as error:
            await collect(binding('kiro',url),policy,request_timeout=limit)
        assert error.value.code=='invalid_limits' and not requests


@pytest.mark.parametrize('model,protocol,path',[('claude-manual','anthropic','/v1/messages'),('gpt-codex-manual','openai-responses','/responses')])
async def test_copilot_manual_import_without_catalog_metadata_uses_native_route(model,protocol,path):
    async with wire([native_text(protocol)]) as (url,policy,requests):
        await collect(binding('github-copilot',url),policy,payload(model=model))
        assert requests[0]['path']==path
