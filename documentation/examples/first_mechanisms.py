"""Run the first-mechanisms tutorial entirely inside a disposable local workspace."""
from pathlib import Path
import tempfile

from agent.framing import FrameCodec, encode_frame
from agent.goals import GoalService
from agent.protocol import CoreError, canonical_json, strict_loads
from agent.state import Journal


def main():
    scope = 'tutorial-owner/tutorial-agent'
    with tempfile.TemporaryDirectory(prefix='sturgis-doc-tutorial-') as directory:
        journal = Journal(Path(directory) / 'state.db')
        first = journal.put('tutorial', 'note', scope, {'text': 'Observe before acting'})
        assert first.revision == 1
        revised = journal.put('tutorial', 'note', scope, {'text': 'Preserve the receipt'}, expected_revision=first.revision)
        assert revised.revision == 2
        try:
            journal.put('tutorial', 'note', scope, {'text': 'stale writer'}, expected_revision=first.revision)
        except CoreError as error:
            assert error.code == 'conflict'
        else:
            raise AssertionError('A stale revision must be rejected')
        assert journal.get('tutorial', 'note', 'another-owner/agent') is None
        goals = GoalService(journal)
        goal = goals.create(scope, 'Review source material', limits={'tokens': 1000})
        active = goals.transition(scope, goal.id, 'active', expected_revision=goal.revision)
        assert active.value['state'] == 'active'
        reservation = goals.reserve(scope, goal.id, 'tutorial-reservation', {'tokens': 100})
        goals.charge(scope, goal.id, reservation.id, 'tutorial-usage', {'tokens': 40})
        goals.charge(scope, goal.id, reservation.id, 'tutorial-usage', {'tokens': 40})
        assert goals.get(scope, goal.id).value['usage']['tokens'] == 40
        journal.close()
        reopened = Journal(Path(directory) / 'state.db')
        assert reopened.get('tutorial', 'note', scope).revision == 2
        reopened.close()
    body = canonical_json({'source': 'tutorial', 'revision': 2}).encode()
    framed = encode_frame(body)
    codec = FrameCodec()
    assert codec.feed(framed[:3]) == []
    assert codec.feed(framed[3:]) == [body]
    codec.finish()
    assert strict_loads(body)['revision'] == 2
    try:
        strict_loads('{"revision":1,"revision":2}')
    except CoreError as error:
        assert error.code == 'invalid_json'
    else:
        raise AssertionError('Duplicate JSON fields must be rejected')
    print('Tutorial passed: durable revision, conflict, owner scope, goal transition, idempotent usage, restart, fragmented frame and strict JSON.')


if __name__ == '__main__':
    main()
