"""Harness identity is mandatory context, independent of configured inference models."""
import hashlib
import json

import pytest

from agent.runtime.identity import AGENT_DEVELOPER, AGENT_NAME, IDENTITY_INSTRUCTIONS, IDENTITY_VERSION
from tests.core.test_runtime import ControlledProvider, accept, runtime


EXPECTED_IDENTITY = {'name': 'Sturgis', 'developer': 'Sidiora Labs', 'version': '1'}


def assert_system_identity(messages):
    assert (AGENT_NAME, AGENT_DEVELOPER, IDENTITY_VERSION) == ('Sturgis', 'Sidiora Labs', '1')
    systems = [message for message in messages if message['role'] == 'system']
    assert len(systems) == 1
    assert messages[0] == systems[0]
    label = json.loads(systems[0]['content'])
    assert label['kind'] == 'instructions' and label['source_id'] == 'instructions'
    assert label['origin'] == 'harness_configuration'
    assert label['text'].startswith(IDENTITY_INSTRUCTIONS + '\n\n')
    assert label['text'].count(IDENTITY_INSTRUCTIONS) == 1
    assert AGENT_NAME in label['text'] and AGENT_DEVELOPER in label['text']
    assert label['range'] == [0, len(label['text'].encode())]
    return label


def assert_identity_packet(packet):
    label = assert_system_identity(packet['messages'])
    assert packet['metadata']['agent_identity'] == EXPECTED_IDENTITY
    manifest = next(item for item in packet['manifest'] if item['source_id'] == 'instructions')
    assert manifest['kind'] == 'instructions' and manifest['representation'] == 'exact'
    assert manifest['start'] == 0 and manifest['end'] == manifest['source_bytes'] == len(label['text'].encode())
    assert manifest['pending_ranges'] == []
    assert manifest['sha256'] == hashlib.sha256(label['text'].encode()).hexdigest()


@pytest.mark.parametrize('custom', [None, 'Use short sentences and describe evidence carefully.'])
async def test_default_and_custom_instructions_keep_exact_mandatory_agent_identity(tmp_path, custom):
    provider = ControlledProvider()
    agent, binding = runtime(tmp_path, provider, instructions=custom)
    try:
        await agent.start()
        _, work = await accept(agent, binding, 'Identify the agent and distinguish its underlying model.')
        assert (await agent.wait(work, timeout=3)).value['state'] == 'completed'
        assert len(provider.requests) == 1
        label = assert_system_identity(provider.requests[0])
        if custom:
            assert custom in label['text']
        packets = agent.journal.list('context_packets', 's')
        assert len(packets) == 1
        assert_identity_packet(packets[0].value)
        assert packets[0].value['messages'] == provider.requests[0]
        assert 'do not claim it developed the underlying model' in IDENTITY_INSTRUCTIONS
        assert 'correct any earlier mistaken self-description' in IDENTITY_INSTRUCTIONS
    finally:
        await agent.close()


async def test_identity_cannot_be_truncated_to_fit_an_undersized_context(tmp_path):
    provider = ControlledProvider()
    agent, binding = runtime(tmp_path, provider, instructions='Be concise.')
    # There is input space, but less than the mandatory identity block requires.
    agent.catalog.observe('s', 'default', model='small-context-model', output_limit=128,
                          context_limit=128 + 258 + len(IDENTITY_INSTRUCTIONS.encode()) // 2)
    try:
        await agent.start()
        _, work = await accept(agent, binding, 'Who are you?')
        result = await agent.wait(work, timeout=3)
        assert result.value['state'] == 'failed' and result.value['error']['code'] == 'context_budget'
        assert provider.requests == []
        assert agent.journal.list('context_packets', 's') == []
        assert agent.journal.events('s', kinds=['runtime.context_sealed']) == []
    finally:
        await agent.close()
