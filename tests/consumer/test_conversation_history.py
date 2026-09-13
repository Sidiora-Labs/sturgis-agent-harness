"""Conversation pages skip hidden journal history without losing routed events or cursors."""
import json
import threading

import pytest

from agent.protocol import CoreError, canonical_json
from agent.state import Journal
from consumer.application import AgentApplication
from tests.consumer.test_application import configuration


@pytest.fixture
async def app(tmp_path):
    value = AgentApplication(configuration(tmp_path))
    yield value
    await value.close()


def maintenance(journal, scope, count):
    with journal.transaction():
        journal._db.executemany('INSERT INTO events(id,scope,kind,source,payload,created) VALUES(?,?,?,?,?,?)',
            ((f'maintenance-{scope}-{i}', scope, 'work.changed', 'scheduler', '{"state":"completed"}', i)
             for i in range(count)))


def turn(journal, scope, id, binding):
    return journal.put('runtime_turns', id, scope, {'binding_id': binding, 'state': 'completed', 'history': []})


def emitted(journal, scope, binding, *, kind='output.persisted', **extra):
    return journal.append(scope, kind, {'binding_id': binding, **extra}, source='delivery')


def test_public_page_filters_backlog_before_limit_and_resolves_terminal_scope(app, monkeypatch):
    room, other = app.conversation_create('Saved room'), app.conversation_create('Other room')
    journal, scope = app.journal, app.scope
    maintenance(journal, scope, 30000)
    binding = room['binding_id']
    turn(journal, scope, 'shared-work-id', binding)
    turn(journal, 'other-tenant', 'shared-work-id', binding)
    first = emitted(journal, scope, binding, kind='user.prompt', text='Saved input')
    foreign_room = emitted(journal, scope, other['binding_id'])
    emitted(journal, 'other-tenant', binding)
    second = emitted(journal, scope, binding, content={'type': 'text', 'text': 'Saved reply'})
    terminal = journal.append(scope, 'runtime.turn_completed', {'work_id': 'shared-work-id'}, source='runtime')
    journal.append('other-tenant', 'runtime.turn_completed', {'work_id': 'shared-work-id'}, source='runtime')
    explicit_other = emitted(journal, scope, other['binding_id'], kind='runtime.turn_failed', work_id='shared-work-id')
    orphan = journal.append(scope, 'runtime.turn_failed', {'work_id': 'unknown'}, source='runtime')
    high_water = journal.append(scope, 'runtime.sleep_result', {'status': 'ok'})
    decoded, statements = [], []
    original = journal._event
    def decode(row):
        decoded.append(row['id'])
        return original(row)
    monkeypatch.setattr(journal, '_event', decode)
    journal._db.set_trace_callback(statements.append)
    page = app.events(room['id'], limit=2)
    assert [event['id'] for event in page['items']] == [first.id, second.id]
    assert decoded == [first.id, second.id], 'Hidden history and the look-ahead row must not be decoded'
    assert page['has_more'] and page['cursor'] == second.seq
    query = next(sql for sql in statements if sql.startswith('SELECT * FROM (SELECT e.*'))
    plan = [row[3] for row in journal._db.execute('EXPLAIN QUERY PLAN ' + query)]
    assert all(any(index in detail for detail in plan) for index in ('events_binding', 'records_bindings', 'events_work'))
    assert not any(detail.startswith('SCAN e') or detail.startswith('SCAN r') for detail in plan)
    journal._db.set_trace_callback(None)
    decoded.clear()
    tail = app.events(room['id'], after=page['cursor'], limit=2)
    assert [event['id'] for event in tail['items']] == [terminal.id]
    assert decoded == [terminal.id]
    assert not tail['has_more'] and tail['cursor'] == high_water.seq
    assert app.events(room['id'], after=tail['cursor']) == {'items': [], 'cursor': tail['cursor'], 'has_more': False}
    unfiltered = app.events(limit=100)
    assert {event['id'] for event in unfiltered['items']} == {first.id, foreign_room.id, second.id, terminal.id, explicit_other.id, orphan.id}
    with pytest.raises(CoreError): app.events('missing-conversation')


def test_exact_page_boundary_empty_scope_and_future_cursor_do_not_regress(app):
    room = app.conversation_create()
    first = emitted(app.journal, app.scope, room['binding_id'])
    second = emitted(app.journal, app.scope, room['binding_id'])
    hidden = app.journal.append(app.scope, 'work.changed', {'state': 'completed'})
    page = app.events(room['id'], limit=2)
    assert [item['id'] for item in page['items']] == [first.id, second.id]
    assert page['has_more'] is False and page['cursor'] == hidden.seq
    future = hidden.seq + 1000
    assert app.events(room['id'], after=future, limit=1) == {'items': [], 'cursor': future, 'has_more': False}
    empty = app.journal.routed_events('empty-scope', {'output.persisted'})
    assert empty == {'events': [], 'cursor': 0, 'has_more': False}
    other = app.conversation_create('No messages')
    assert app.events(other['id']) == {'items': [], 'cursor': hidden.seq, 'has_more': False}


def test_concurrent_append_is_observed_on_following_snapshot_without_loss(app):
    room = app.conversation_create()
    first = emitted(app.journal, app.scope, room['binding_id'])
    decoded, attempting = threading.Event(), threading.Event()
    appended, failures = [], []
    writer = Journal(app.journal._db.execute('PRAGMA database_list').fetchone()[2])
    original = app.journal._event
    def decode(row):
        decoded.set()
        assert attempting.wait(2)
        return original(row)
    app.journal._event = decode
    def append():
        try:
            assert decoded.wait(2)
            attempting.set()
            appended.append(emitted(writer, app.scope, room['binding_id']))
        except BaseException as error:
            failures.append(error)
    thread = threading.Thread(target=append)
    thread.start()
    try:
        page = app.events(room['id'], limit=1)
        assert page['cursor'] == first.seq and not page['has_more']
        thread.join(2)
        assert not thread.is_alive() and not failures
        assert len(appended) == 1
        app.journal._event = original
        next_page = app.events(room['id'], after=page['cursor'], limit=1)
        assert [event['id'] for event in next_page['items']] == [appended[0].id]
    finally:
        thread.join(2)
        writer.close()


def test_imported_history_keeps_source_and_terminal_binding_after_reopen(tmp_path):
    path = tmp_path / 'history.sqlite'
    journal = Journal(path)
    scope, binding = 'restored-scope', 'preserved-binding'
    try:
        turn(journal, scope, 'historical-turn', binding)
        with journal.transaction():
            for id, kind, payload in (
                ('old-input', 'user.prompt', {'binding_id': binding, 'text': 'Historical input'}),
                ('old-output', 'output.persisted', {'binding_id': binding, 'content': {'type': 'text', 'text': 'Historical reply'}}),
                ('old-terminal', 'runtime.turn_completed', {'work_id': 'historical-turn'})):
                journal._db.execute('INSERT INTO events(id,scope,kind,source,payload,created) VALUES(?,?,?,?,?,?)',
                    (id, scope, kind, 'portable.import', canonical_json(payload), 123))
        before = [(row['id'], row['payload']) for row in journal._db.execute('SELECT id,payload FROM events ORDER BY seq')]
    finally:
        journal.close()
    journal = Journal(path)
    try:
        result = journal.routed_events(scope, {'user.prompt', 'output.persisted', 'runtime.turn_completed'}, binding_id=binding)
        assert [event.id for event in result['events']] == ['old-input', 'old-output', 'old-terminal']
        assert all(event.source == 'portable.import' for event in result['events'])
        assert not result['has_more']
        assert [(row['id'], row['payload']) for row in journal._db.execute('SELECT id,payload FROM events ORDER BY seq')] == before
    finally:
        journal.close()


def test_saved_room_order_uses_creation_time_not_random_ids(app):
    for id, created, archived in [('room_zz_old', 1, False), ('room_mm_new', 3, False),
                                 ('room_aa_middle', 2, False), ('room_archived', 4, True)]:
        app.journal.put('consumer.conversations', id, app.scope, {
            'title': id, 'binding_id': 'binding_' + id, 'created_at': created, 'archived': archived})
    assert [room['id'] for room in app.conversation_list()['items']] == ['room_mm_new', 'room_aa_middle', 'room_zz_old']
    assert app.conversation_list(include_archived=True)['items'][0]['id'] == 'room_archived'
    assert [room['id'] for room in app.conversation_list(query='middle')['items']] == ['room_aa_middle']


@pytest.mark.parametrize('options', [
    {'after': True}, {'after': -1}, {'limit': True}, {'limit': 0}, {'limit': 1001},
    {'kinds': 'output.persisted'}, {'kinds': []}, {'kinds': ['']}, {'kinds': [None]},
    {'kinds': ['x' * 129]}, {'kinds': ['bad\0kind']}, {'kinds': [str(i) for i in range(65)]},
    {'binding_id': True}, {'binding_id': ''}, {'binding_id': 'x' * 257}, {'binding_id': 'bad\0binding'},
])
def test_routed_page_rejects_unbounded_or_ambiguous_arguments(tmp_path, options):
    journal = Journal(tmp_path / 'bounds.sqlite')
    try:
        with pytest.raises(CoreError):
            journal.routed_events('scope', **{'kinds': {'output.persisted'}, **options})
    finally:
        journal.close()
