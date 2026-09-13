"""Daily durable sleep admission stays idle after completion and resumes unfinished work."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

import agent.runtime as runtime_module
from agent.memory import MemorySleep, NativeMemoryClient, MemoryIngestor
from agent.protocol import CoreError
from agent.runtime import CoreRuntime
from agent.state import Journal


BINARY = Path('.build/native/sturgis-memory').resolve()
BEFORE_MIDNIGHT = datetime(2026, 9, 12, 21, 59, tzinfo=timezone.utc).timestamp()


async def eventually(condition):
    async def wait():
        while not condition():
            await asyncio.sleep(.01)
    await asyncio.wait_for(wait(), 5)


def sleep_jobs(runtime):
    return [row for row in runtime.journal.list('work', 's') if row.value['kind'] == 'memory_sleep']


def create_runtime(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir(exist_ok=True)
    runtime = CoreRuntime(tmp_path / 'runtime', scope='s', workspace=workspace,
                          memory_binary=BINARY, memory_interval=.02)
    runtime.memory_sleep = MemorySleep(runtime.journal, runtime.memory, runtime.memory_ingestor,
                                      timezone='Europe/Berlin')
    return runtime


async def observe_idle_ticks(runtime, count=5):
    latest = runtime.journal.get('runtime_memory', 'latest', 's')
    revision = latest.revision if latest else 0
    await eventually(lambda: (row := runtime.journal.get('runtime_memory', 'latest', 's'))
                     is not None and row.revision >= revision + count)


async def wait_completed_day(runtime, day):
    key = 'Europe/Berlin|' + day
    await eventually(lambda: (row := runtime.journal.get('memory.sleep', key, 's')) is not None
                     and row.value['phase'] == 'complete' and runtime._sleep_work is None)


async def test_real_runtime_idle_restart_and_local_midnight_do_not_accumulate_noop_work(tmp_path, monkeypatch):
    current = [BEFORE_MIDNIGHT]
    monkeypatch.setattr(runtime_module, 'time', SimpleNamespace(time=lambda: current[0], monotonic=time.monotonic))
    runtime = create_runtime(tmp_path)
    try:
        await runtime.start()
        await wait_completed_day(runtime, '2026-09-12')
        first = sleep_jobs(runtime)
        assert len(first) == 1 and first[0].value['state'] == 'completed'
        generation = runtime.journal.get('memory.sleep', 'Europe/Berlin|2026-09-12', 's').value['generation']
        await observe_idle_ticks(runtime)
        assert [row.id for row in sleep_jobs(runtime)] == [first[0].id]
    finally:
        await runtime.close()

    runtime = create_runtime(tmp_path)
    try:
        await runtime.start()
        await observe_idle_ticks(runtime)
        assert [row.id for row in sleep_jobs(runtime)] == [first[0].id]
        assert runtime.journal.get('memory.sleep', 'Europe/Berlin|2026-09-12', 's').value['generation'] == generation
        # UTC has not changed date, but the configured local day has.
        current[0] += 120
        await wait_completed_day(runtime, '2026-09-13')
        await observe_idle_ticks(runtime)
        jobs = sleep_jobs(runtime)
        assert len(jobs) == 2 and all(row.value['state'] == 'completed' for row in jobs)
        assert len(runtime.journal.list('memory.sleep', 's')) == 2
        assert not [event for event in runtime.journal.events('s') if event.kind == 'runtime.worker_failed']
    finally:
        await runtime.close()


async def test_partial_day_is_due_after_owner_restart_and_completes_once(tmp_path):
    journal = Journal(tmp_path / 'operations.sqlite')
    client = NativeMemoryClient(tmp_path / 'memory.sqlite', binary=BINARY)
    try:
        assert await client.start()
        ingestor = MemoryIngestor(journal, client)
        first = MemorySleep(journal, client, ingestor, admission=lambda *_: False)
        result = await first.run_cycle('s', now=BEFORE_MIDNIGHT)
        assert result['state'] == 'deferred'
        assert first.due('s', now=BEFORE_MIDNIGHT)
        replacement = MemorySleep(journal, client, ingestor)
        assert replacement.due('s', now=BEFORE_MIDNIGHT)
        completed = await replacement.run_cycle('s', now=BEFORE_MIDNIGHT)
        assert completed['phase'] == 'complete' and completed['status'] == 'ok'
        assert not replacement.due('s', now=BEFORE_MIDNIGHT)
        assert not first.due('s', now=BEFORE_MIDNIGHT)
        assert replacement.due('s', now=BEFORE_MIDNIGHT + 86400)
        assert len(journal.list('memory.sleep', 's')) == 1
    finally:
        await client.close()
        journal.close()


def test_due_respects_completion_scope_and_live_owner_without_mutating_receipts(tmp_path):
    journal = Journal(tmp_path / 'operations.sqlite')
    try:
        sleep = MemorySleep(journal, None, None, timezone='Europe/Berlin')
        _, key = sleep._cycle_identity(BEFORE_MIDNIGHT)
        assert sleep.due('s', now=BEFORE_MIDNIGHT)
        row = journal.put('memory.sleep', key, 's', {
            'phase': 'publishing', 'owner': 'other-worker', 'owner_until': time.time() + 100})
        assert not sleep.due('s', now=BEFORE_MIDNIGHT)
        assert sleep.due('another-scope', now=BEFORE_MIDNIGHT)
        assert journal.get('memory.sleep', key, 's') == row
        row = journal.put('memory.sleep', key, 's', {**row.value, 'owner_until': 0}, row.revision)
        assert sleep.due('s', now=BEFORE_MIDNIGHT)
        row = journal.put('memory.sleep', key, 's', {**row.value, 'owner': sleep.owner,
                          'owner_until': time.time() + 100}, row.revision)
        assert sleep.due('s', now=BEFORE_MIDNIGHT)
        journal.put('memory.sleep', key, 's', {**row.value, 'phase': 'complete', 'status': 'partial'}, row.revision)
        assert not sleep.due('s', now=BEFORE_MIDNIGHT), 'Rejected candidates do not make a completed day eligible again'
        assert sleep.due('s', now=BEFORE_MIDNIGHT + 120)
        for invalid in (True, float('nan'), -1, 'today', 1e30):
            with pytest.raises(CoreError):
                sleep.due('s', now=invalid)
    finally:
        journal.close()
