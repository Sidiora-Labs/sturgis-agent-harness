"""Active admission stays bounded by live work while durable history remains intact."""

from collections import Counter
import sqlite3

import pytest

from agent.protocol import CoreError
from agent.scheduler import CONTROL, Scheduler
from agent.state import Journal


@pytest.fixture
def journal(tmp_path):
    value = Journal(tmp_path / "active.db")
    yield value
    value.close()


@pytest.mark.parametrize("states", [
    None, "queued", b"queued", 1, {}, {"queued": True}, [], (), set(),
    [None], [1], [False], [""], [" "], ["queued", []], ["q\0"],
    ["x" * 129], [str(i) for i in range(65)], iter(["queued"]),
])
def test_state_filters_reject_invalid_or_unbounded_collections(journal, states):
    with pytest.raises(CoreError) as error:
        journal.list_states("work", "alpha", states)
    assert error.value.code == "invalid_states"


@pytest.mark.parametrize("collection", [list, tuple, set, frozenset])
def test_state_queries_preserve_scope_namespace_and_identifier_order(journal, collection):
    journal.put("work", "z", "alpha", {"state": "queued"})
    journal.put("work", "a", "alpha", {"state": "running"})
    journal.put("work", "m", "alpha", {"state": "completed"})
    journal.put("work", "missing", "alpha", {})
    journal.put("work", "null", "alpha", {"state": None})
    journal.put("work", "array", "alpha", {"state": ["queued"]})
    journal.put("work", "object", "alpha", {"state": {"state": "queued"}})
    journal.put("work", "a", "beta", {"state": "queued"})
    journal.put("other", "b", "alpha", {"state": "queued"})
    before = journal.list("work", "alpha")
    rows = journal.list_states("work", "alpha", collection(["queued", "running", "queued"]))
    assert [row.id for row in rows] == ["a", "z"]
    assert all(row.namespace == "work" and row.scope == "alpha" and row.revision == 1 for row in rows)
    assert [row.id for row in journal.list_states("work", "beta", ["queued"])] == ["a"]
    assert [row.id for row in journal.list_states("other", "alpha", ["queued"])] == ["b"]
    assert journal.list_states("work", "unknown", ["queued"]) == []
    assert journal.list("work", "alpha") == before
    with pytest.raises(CoreError) as error:
        journal.list_states("work", "", ["queued"])
    assert error.value.code == "invalid_scope"


def test_state_query_treats_sql_text_as_data_and_updates_transactionally(journal):
    state = "queued') OR 1=1 --"
    row = journal.put("work", "quoted", "alpha", {"state": state})
    journal.put("work", "normal", "alpha", {"state": "queued"})
    assert journal.list_states("work", "alpha", [state]) == [row]
    changed = journal.put("work", row.id, "alpha", {"state": "running"}, row.revision)
    assert journal.list_states("work", "alpha", [state]) == []
    assert journal.list_states("work", "alpha", ["running"]) == [changed]
    with pytest.raises(RuntimeError):
        with journal.transaction():
            journal.put("work", row.id, "alpha", {"state": "completed"}, changed.revision)
            assert journal.list_states("work", "alpha", ["running"]) == []
            raise RuntimeError("rollback")
    assert journal.list_states("work", "alpha", ["running"]) == [changed]


def test_existing_journal_installs_state_index_without_changing_history(tmp_path):
    path = tmp_path / "existing.db"
    prior = Journal(path)
    old = prior.put("work", "history", "alpha", {"state": "completed", "evidence": "retain me"})
    live = prior.put("work", "live", "alpha", {"state": "queued"})
    prior.close()
    with sqlite3.connect(path) as database:
        database.execute("DROP INDEX records_states")
    reopened = Journal(path)
    try:
        assert reopened.list("work", "alpha") == [old, live]
        queries = []
        reopened._db.set_trace_callback(queries.append)
        assert reopened.list_states("work", "alpha", ["queued", "running"]) == [live]
        reopened._db.set_trace_callback(None)
        query = next(query for query in queries if query.startswith("SELECT * FROM records"))
        plan = reopened._db.execute("EXPLAIN QUERY PLAN " + query).fetchall()
        # The query must seek matching states, rather than walk every historical
        # body in the scope. This also verifies upgrading an existing journal.
        assert any("USING INDEX records_states" in row["detail"] and "<expr>=" in row["detail"] for row in plan)
    finally:
        reopened.close()


def _terminal_history(scheduler, scope, count):
    identities = set()
    with scheduler.journal.transaction():
        for number in range(count):
            row = scheduler.submit(scope, "memory.sleep", {"evidence": "history " * 128}, now=100)
            scheduler.journal.put("work", row.id, scope,
                                  {**row.value, "state": ("completed", "failed", "cancelled")[number % 3]}, row.revision)
            identities.add(row.id)
    return identities


def test_admission_ignores_thousands_of_terminal_bodies_preserving_global_capacity_and_priority(journal, monkeypatch):
    scheduler = Scheduler(journal, clock=lambda: 1000, max_running=1)
    running = scheduler.submit("beta", "model", {})
    running = scheduler.admit("beta", running.id)
    history = _terminal_history(scheduler, "alpha", 1000) | _terminal_history(scheduler, "beta", 1000)
    oldest = scheduler.submit("alpha", "model", {}, now=800)
    tied = scheduler.submit("alpha", "model", {}, now=800)
    newest = scheduler.submit("alpha", "model", {}, priority=1)
    scheduler.submit("alpha", "model", {}, priority=100, not_before=1001)
    revoked = scheduler.submit("alpha", "model", {}, priority=100)
    journal.put("work", revoked.id, "alpha", {**revoked.value, "revoked": True}, revoked.revision)
    # Rows outside the control index cannot consume capacity or be admitted.
    journal.put("work", "orphan-running", "alpha", {**running.value})
    journal.put("work", "orphan-queued", "alpha", {**oldest.value, "priority": 100})
    journal.put("work", "foreign-running", "unindexed", {**running.value})
    journal.put("work_index", "foreign-running", "untrusted", {"scope": "unindexed"})
    original_record, original_query = journal._record, journal.list_states
    queries = []
    decoded_work = []

    def active_record(row):
        if row is not None and row["namespace"] == "work":
            assert row["id"] not in history, "Admission decoded completed historical work"
            decoded_work.append((row["scope"], row["id"]))
        return original_record(row)

    def state_query(namespace, scope, states):
        queries.append((namespace, scope))
        return original_query(namespace, scope, states)

    def admit_without_history(*args):
        queries.clear()
        decoded_work.clear()
        with monkeypatch.context() as patch:
            patch.setattr(journal, "_record", active_record)
            patch.setattr(journal, "list_states", state_query)
            result = scheduler.admit(*args)
        assert Counter(queries) == Counter({("work", "alpha"): 1, ("work", "beta"): 1})
        assert len(decoded_work) < 12
        return result

    assert admit_without_history("alpha") is None  # Running in beta consumes the global slot.
    scheduler.finish("beta", running.id, 1)
    assert admit_without_history("alpha", newest.id) is None  # Aging wins over the newer priority.
    admitted = admit_without_history("alpha")
    assert admitted.id == oldest.id  # Queue sequence breaks the same-age tie.
    assert admit_without_history("beta") is None
    assert scheduler.get("alpha", tied.id).value["state"] == "queued"
    assert scheduler.get("alpha", "orphan-queued").value["state"] == "queued"
    assert sum(row.id in history for scope in ("alpha", "beta") for row in journal.list("work", scope)) == 2000


def test_admission_rejects_unindexed_work_even_in_an_authorized_scope(journal):
    scheduler = Scheduler(journal, clock=lambda: 1000)
    row = scheduler.submit("alpha", "model", {})
    journal.delete("work_index", row.id, CONTROL, 1)
    assert scheduler.admit("alpha", row.id) is None
    registered = scheduler.submit("alpha", "model", {}, priority=-100)
    assert scheduler.admit("alpha", row.id) is None
    assert scheduler.admit("alpha").id == registered.id
    assert scheduler.get("alpha", row.id).value["state"] == "queued"
