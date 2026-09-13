"""Real slow HTTP checks retain bounded authority and close upstream on loss of it."""
import asyncio
from contextlib import asynccontextmanager
import json
import time
from types import SimpleNamespace

import pytest

from agent.protocol import CoreError, ToolCall
from agent.runtime import CoreRuntime
from consumer.network import NetworkPolicy
from consumer.providers import ProviderSettings
from consumer.vault import CredentialVault
from tests.gateway.test_transports import oai, sse


@asynccontextmanager
async def held_provider(*, malformed=False):
    entered, release, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    requests, tasks = [], set()

    async def handle(reader, writer):
        tasks.add(asyncio.current_task())
        waits = []
        try:
            head = await reader.readuntil(b'\r\n\r\n')
            headers = dict(line.decode().split(': ', 1) for line in head.split(b'\r\n')[1:] if b': ' in line)
            length = next(value for key, value in headers.items() if key.lower() == 'content-length')
            body = json.loads(await reader.readexactly(int(length)))
            requests.append(body)
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n')
            writer.write(sse({'choices': [], 'usage': {'prompt_tokens': 20, 'completion_tokens': 17, 'total_tokens': 37}}))
            await writer.drain()
            entered.set()
            waits = [asyncio.create_task(release.wait()), asyncio.create_task(reader.read())]
            done, _ = await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
            if waits[1] in done:
                return
            if body.get('tools'):
                arguments = {} if malformed else ToolCall('consumer.probe', {}).as_dict()
                delta = {'tool_calls': [{'index': 0, 'id': 'slow-tool-call', 'type': 'function',
                    'function': {'name': 'invoke', 'arguments': json.dumps(arguments)}}]}
                finish = 'tool_calls'
            else:
                delta, finish = {'content': 'OK'}, 'stop'
            writer.write(sse(oai(delta, finish), b'[DONE]'))
            await writer.drain()
            await waits[1]
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            for task in waits:
                if not task.done(): task.cancel()
            await asyncio.gather(*waits, return_exceptions=True)
            writer.close()
            await writer.wait_closed()
            closed.set()
            tasks.discard(asyncio.current_task())

    server = await asyncio.start_server(handle, '127.0.0.1', 0)
    url = f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}'
    try:
        yield SimpleNamespace(url=url, entered=entered, release=release, closed=closed, requests=requests)
    finally:
        server.close()
        for task in list(tasks): task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await server.wait_closed()


@asynccontextmanager
async def installation(tmp_path, upstream, *, tools=True, timeout=3, lease=.3):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    core = CoreRuntime(tmp_path / 'runtime', scope='probe-agent', workspace=workspace)
    core.scheduler.lease_seconds = lease
    settings = ProviderSettings(core, CredentialVault(tmp_path / 'vault'),
        NetworkPolicy(private_origins=[upstream.url], timeout=timeout))
    configured = settings.configure('slow', endpoint=upstream.url + '/chat/completions', model='slow-model',
                                    context_limit=8192, output_limit=2048, tools=tools)
    heartbeats, usage_received = [], asyncio.Event()
    heartbeat = core.scheduler.heartbeat
    def observed_heartbeat(*args, **kwargs):
        result = heartbeat(*args, **kwargs)
        heartbeats.append(time.monotonic())
        return result
    core.scheduler.heartbeat = observed_heartbeat
    factory = settings._adapter
    class ObservingAdapter:
        async def stream(self, *args, **kwargs):
            async for event in factory(upstream.url).stream(*args, **kwargs):
                if event.kind == 'usage': usage_received.set()
                yield event
    settings._adapter = lambda _: ObservingAdapter()
    try:
        yield SimpleNamespace(core=core, settings=settings, configured=configured,
                              heartbeats=heartbeats, usage_received=usage_received)
    finally:
        await core.close()


def probe_work(env):
    return next(row for row in env.core.journal.list('work', env.core.scope)
                if row.value['kind'] == 'provider_probe')


def assert_settled(env, state):
    work = probe_work(env)
    assert work.value['state'] == state
    assert work.value['usage'] == {'tokens': 37}
    assert work.value['claim_reserved'] == {'tokens': 0}
    assert work.value['resources'] == {'tokens': 4096}
    claims = env.core.journal.list('work_claims', env.core.scope)
    assert len(claims) == 1 and claims[0].value['state'] == 'settled'
    assert not [task for task in asyncio.all_tasks() if not task.done()
                and task.get_name().startswith('consumer:provider-probe')]


@pytest.mark.parametrize('tools', [False, True])
async def test_probe_longer_than_lease_renews_then_stops_and_can_activate(tmp_path, tools):
    async with held_provider() as upstream, installation(tmp_path, upstream, tools=tools) as env:
        task = asyncio.create_task(env.settings.probe('slow', env.configured['revision']))
        await asyncio.wait_for(env.usage_received.wait(), 1)
        await asyncio.sleep(.75)  # More than two original lease durations.
        assert not task.done() and len(env.heartbeats) >= 3
        assert upstream.requests[0]['max_tokens'] == (1024 if tools else 128)
        upstream.release.set()
        result = await asyncio.wait_for(task, 1)
        assert result['ready'] and result['probe']['status'] == 'ready'
        assert env.settings.activate('slow', env.configured['revision'])['active']
        assert_settled(env, 'completed')
        await asyncio.wait_for(upstream.closed.wait(), 1)
        count = len(env.heartbeats)
        await asyncio.sleep(.35)
        assert len(env.heartbeats) == count


@pytest.mark.parametrize('cause', ['cancel', 'generation', 'heartbeat'])
async def test_authority_loss_closes_slow_wire_preserves_usage_and_never_activates(tmp_path, cause):
    async with held_provider() as upstream, installation(tmp_path, upstream) as env:
        task = asyncio.create_task(env.settings.probe('slow', env.configured['revision']))
        await asyncio.wait_for(env.usage_received.wait(), 1)
        work = probe_work(env)
        if cause == 'cancel':
            env.core.scheduler.cancel(env.core.scope, work.id, reason='user_revoked')
        elif cause == 'generation':
            env.core.journal.put('work', work.id, env.core.scope,
                {**work.value, 'generation': work.value['generation'] + 1}, work.revision)
        else:
            def reject(*args, **kwargs):
                raise CoreError('authority_revoked', 'Controlled heartbeat authority rejection')
            env.core.scheduler.heartbeat = reject
        started = time.monotonic()
        result = await asyncio.wait_for(task, .8)
        assert time.monotonic() - started < .6
        assert not result['ready'] and result['probe']['error']['code'] in {'authority_revoked', 'stale_authority'}
        await asyncio.wait_for(upstream.closed.wait(), .5)
        assert not upstream.release.is_set()
        with pytest.raises(CoreError): env.settings.activate('slow', env.configured['revision'])
        assert_settled(env, 'failed' if cause == 'heartbeat' else 'cancelled')
        count = len(env.heartbeats)
        await asyncio.sleep(.35)
        assert len(env.heartbeats) == count


async def test_external_cancellation_joins_keeper_and_retains_observed_usage(tmp_path):
    async with held_provider() as upstream, installation(tmp_path, upstream) as env:
        task = asyncio.create_task(env.settings.probe('slow', env.configured['revision']))
        await asyncio.wait_for(env.usage_received.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        await asyncio.wait_for(upstream.closed.wait(), .5)
        assert_settled(env, 'failed')
        result = env.settings.inspect('slow')
        assert not result['ready'] and result['probe']['error']['code'] == 'cancelled'
        with pytest.raises(CoreError): env.settings.activate('slow', env.configured['revision'])
        count = len(env.heartbeats)
        await asyncio.sleep(.35)
        assert len(env.heartbeats) == count


async def test_deadline_stops_silent_upstream_and_prevents_late_renewal(tmp_path):
    async with held_provider() as upstream, installation(tmp_path, upstream, timeout=.35) as env:
        started = time.monotonic()
        task = asyncio.create_task(env.settings.probe('slow', env.configured['revision']))
        await asyncio.wait_for(env.usage_received.wait(), 1)
        result = await asyncio.wait_for(task, .8)
        assert time.monotonic() - started < .7
        assert not result['ready'] and result['probe']['error']['code'] == 'timeout'
        await asyncio.wait_for(upstream.closed.wait(), .5)
        assert_settled(env, 'failed')
        count = len(env.heartbeats)
        assert all(at < started + .35 for at in env.heartbeats)
        await asyncio.sleep(.35)
        assert len(env.heartbeats) == count


async def test_revocation_overrides_concurrent_malformed_tool_failure(tmp_path):
    async with held_provider(malformed=True) as upstream, installation(tmp_path, upstream) as env:
        finish = env.settings._probe_finish
        def revoke_before_settlement(row, work, claim_id, **kwargs):
            assert kwargs['error']['code'] == 'invalid_call'
            env.core.scheduler.cancel(env.core.scope, work.id, reason='concurrent_revocation')
            return finish(row, work, claim_id, **kwargs)
        env.settings._probe_finish = revoke_before_settlement
        task = asyncio.create_task(env.settings.probe('slow', env.configured['revision']))
        await asyncio.wait_for(env.usage_received.wait(), 1)
        upstream.release.set()
        result = await asyncio.wait_for(task, 1)
        assert not result['ready'] and result['probe']['error']['code'] == 'authority_revoked'
        assert_settled(env, 'cancelled')
        with pytest.raises(CoreError): env.settings.activate('slow', env.configured['revision'])
