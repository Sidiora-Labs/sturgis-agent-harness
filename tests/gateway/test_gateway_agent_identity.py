"""Actual consumer/worker/gateway requests carry identity; fixtures do not prove vendor compliance."""
import asyncio
import base64
import hashlib
import json
import sqlite3

from tests.consumer.test_api import PASSWORD, TOKEN, agent, command
from tests.consumer.test_probe_tool_schema import answer, declared_call
from tests.consumer_integration.test_http import http_installation
from tests.core.test_agent_identity import assert_identity_packet, assert_system_identity
from tests.gateway.test_transports import wire


WRONG = 'I am Fugu-Max by Sakana AI, not Sturgis.'
CORRECT = 'I am Sturgis, the agent developed by Sidiora Labs.'
PROOF = base64.b64encode(b'Known workspace evidence.').decode()
MODELS = ['vendor/model-a', 'vendor/model-b']


def assert_wrong_history_is_untrusted(messages):
    matching = [message for message in messages if WRONG in message['content']]
    assert matching
    labels = []
    for message in matching:
        label = json.loads(message['content'])
        labels.append(label)
        assert message['role'] == 'user'
        assert (label['kind'], label['origin']) in {
            ('conversation', 'journal_transcript'), ('memory', 'retrieved')}
    assert any(label['kind'] == 'conversation' for label in labels)


async def send_and_finish(client, owner, room, identity, text):
    receipt = await command(client, owner, 'messages.send', {
        'conversation_id': room, 'message_id': identity, 'text': text})
    async def wait():
        while True:
            history = await command(client, owner, 'conversations.get', {'id': room})
            turn = next((turn for turn in history['turns'] if turn['id'] == receipt['work_id']), None)
            if turn and turn['state'] in {'completed', 'failed', 'interrupted'}:
                assert turn['state'] == 'completed', turn
                return history, turn
            await asyncio.sleep(.025)
    return await asyncio.wait_for(wait(), 10)


def sealed_records(path):
    with sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True) as db:
        packets = [json.loads(row[0]) for row in db.execute("SELECT body FROM records WHERE namespace='context_packets'")]
        turns = {row[0]: hashlib.sha256(row[1].encode()).hexdigest() for row in db.execute(
            "SELECT id,body FROM records WHERE namespace='runtime_turns'")}
    return packets, turns


async def test_identity_survives_real_tool_roundtrip_provider_change_and_worker_restart(tmp_path):
    observed = {'probes': [], 'chat': []}

    def upstream(request, _):
        if request['method'] == 'GET':
            return {'data': [{'id': model, 'context_length': 64000, 'max_output_tokens': 2048,
                              'supported_parameters': ['tools']} for model in MODELS]}
        body = request['body']
        prompt = body['messages'][0]['content']
        if prompt.startswith('Call invoke exactly once'):
            observed['probes'].append(body)
            return declared_call(body, 'consumer.probe', {})
        assert_system_identity(body['messages'])
        observed['chat'].append(body)
        step = len(observed['chat'])
        assert body['model'] == MODELS[0 if step <= 3 else 1]
        if step == 1:
            # Preserve an actual wrong upstream reply, rather than rewriting the
            # historical transcript to make the next request appear correct.
            return answer(WRONG)
        assert_wrong_history_is_untrusted(body['messages'])
        if step == 2:
            return declared_call(body, 'workspace.read', {'operation': 'read', 'path': 'identity-proof.txt'})
        if step == 3:
            tools = [message for message in body['messages'] if message['role'] == 'tool']
            assert len(tools) == 1
            result = json.loads(json.loads(tools[0]['content'])['text'])
            assert result['tool'] == 'workspace.read' and result['outcome'] == 'success'
            assert result['data']['data_base64'] == PROOF
        return answer(CORRECT)

    async with wire(upstream) as (url, _, _):
        async with http_installation(tmp_path, url) as (client, app):
            response = await client.post('/api/v1/bootstrap', json={
                'email': 'owner@example.com', 'password': PASSWORD, 'token': TOKEN})
            assert response.status_code == 200
            client.headers['Authorization'] = 'Bearer ' + response.json()['data']['token']
            connection = await client.post('/api/v1/gateway/connections', json={
                'preset_id': 'custom-openai', 'name': 'Identity wire', 'endpoint': url + '/v1',
                'api_key': 'controlled-upstream-key', 'models': MODELS})
            assert connection.status_code == 200, connection.text
            connection_id = connection.json()['data']['id']
            owner = await agent(client, 'Personal display name')
            agent_id = owner['id']

            async def choose(model):
                selected = await client.post(f'/api/v1/agents/{agent_id}/gateway-model',
                                             json={'model': connection_id + '/' + model})
                assert selected.status_code == 200, selected.text
                assert selected.json()['data']['active'] and selected.json()['data']['tools']

            await choose(MODELS[0])
            await command(client, agent_id, 'files.write', {'path': 'identity-proof.txt', 'data_base64': PROOF})
            room = await command(client, agent_id, 'conversations.create', {'title': 'Identity continuity'})
            history, first = await send_and_finish(client, agent_id, room['id'], 'wrong-identity', 'Who are you?')
            assert WRONG in json.dumps(history)
            path = app.state.control.manager.config(agent_id).root / 'operations.sqlite'
            _, originals = sealed_records(path)
            original_hash = originals[first['id']]
            await send_and_finish(client, agent_id, room['id'], 'tool-identity', 'Read identity-proof.txt, then identify the agent.')
            await choose(MODELS[1])
            await send_and_finish(client, agent_id, room['id'], 'new-provider', 'Identify the agent using the selected provider.')

            before = await client.post(f'/api/v1/agents/{agent_id}/lifecycle/status', json={})
            assert before.status_code == 200
            old_pid = before.json()['data']['process']['pid']
            for _ in range(40):
                suspended = await client.post(f'/api/v1/agents/{agent_id}/lifecycle/suspend', json={})
                if suspended.status_code == 200: break
                assert suspended.json()['error']['code'] == 'runtime_busy', suspended.text
                await asyncio.sleep(.05)
            assert suspended.status_code == 200, suspended.text
            restarted = await client.post(f'/api/v1/agents/{agent_id}/lifecycle/start', json={})
            assert restarted.status_code == 200, restarted.text
            assert restarted.json()['data']['process']['pid'] != old_pid
            history, _ = await send_and_finish(client, agent_id, room['id'], 'after-restart', 'Identify the agent after the restart.')
            assert WRONG in json.dumps(history) and CORRECT in json.dumps(history)
            assert len(observed['chat']) == 5
            assert [body['model'] for body in observed['probes']] == MODELS
            packets, final_turns = sealed_records(path)
            assert len(packets) == 5
            for packet in packets:
                assert_identity_packet(packet)
            assert final_turns[first['id']] == original_hash, 'A previous wrong answer remains an unchanged factual transcript'
