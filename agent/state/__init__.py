"""Operational journal and revisioned domain records in one transaction boundary."""

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import sqlite3
import threading
import time

from agent.protocol import CoreError, Event, canonical_json, new_id, strict_loads, validate_scope


@dataclass(frozen=True)
class Record:
    namespace: str
    id: str
    scope: str
    revision: int
    value: dict


class Journal:
    def __init__(self, path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False, timeout=5)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._depth = 0
        self._closed = False
        self._db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,scope TEXT NOT NULL,kind TEXT NOT NULL,source TEXT NOT NULL,payload TEXT NOT NULL,created REAL NOT NULL,dedupe TEXT,UNIQUE(scope,dedupe));
            CREATE INDEX IF NOT EXISTS events_scope ON events(scope,seq);
            CREATE INDEX IF NOT EXISTS events_kinds ON events(scope,kind,seq);
            CREATE INDEX IF NOT EXISTS events_binding ON events(scope,json_extract(payload,'$.binding_id'),kind,seq);
            CREATE INDEX IF NOT EXISTS events_work ON events(scope,json_extract(payload,'$.work_id'),kind,seq);
            CREATE TABLE IF NOT EXISTS records(namespace TEXT NOT NULL,id TEXT NOT NULL,scope TEXT NOT NULL,revision INTEGER NOT NULL,body TEXT NOT NULL,PRIMARY KEY(namespace,id,scope));
            CREATE INDEX IF NOT EXISTS records_states ON records(namespace,scope,json_extract(body,'$.state'),id);
            CREATE INDEX IF NOT EXISTS records_bindings ON records(namespace,scope,json_extract(body,'$.binding_id'),id);
            CREATE TABLE IF NOT EXISTS cursors(scope TEXT NOT NULL,consumer TEXT NOT NULL,seq INTEGER NOT NULL,PRIMARY KEY(scope,consumer));
        """)

    @contextmanager
    def transaction(self):
        with self._lock:
            if self._closed:
                raise CoreError("closed", "Journal is closed")
            level = self._depth
            self._db.execute("BEGIN IMMEDIATE" if level == 0 else f"SAVEPOINT sturgis_{level}")
            self._depth += 1
            try:
                yield self
            except BaseException:
                self._db.execute("ROLLBACK" if level == 0 else f"ROLLBACK TO sturgis_{level}")
                if level:
                    self._db.execute(f"RELEASE sturgis_{level}")
                raise
            else:
                self._db.execute("COMMIT" if level == 0 else f"RELEASE sturgis_{level}")
            finally:
                self._depth -= 1

    @staticmethod
    def _record(row):
        return None if row is None else Record(row["namespace"], row["id"], row["scope"], row["revision"], strict_loads(row["body"]))

    @staticmethod
    def _event(row):
        return Event(row["id"], row["seq"], row["scope"], row["kind"], row["source"], strict_loads(row["payload"]), row["created"])

    def get(self, namespace, key, scope):
        validate_scope(scope)
        with self._lock:
            return self._record(self._db.execute("SELECT * FROM records WHERE namespace=? AND id=? AND scope=?", (namespace,key,scope)).fetchone())

    def list(self, namespace, scope):
        validate_scope(scope)
        with self._lock:
            return [self._record(r) for r in self._db.execute("SELECT * FROM records WHERE namespace=? AND scope=? ORDER BY id", (namespace,scope))]

    def list_states(self, namespace, scope, states):
        """Read one scope's matching states without decoding unrelated history.

        Accept 1–64 state strings, each nonblank and at most 128 characters.
        Results retain the same identifier ordering and Record shape as list.
        """
        validate_scope(scope)
        if not isinstance(states, (list, tuple, set, frozenset)) or not 1 <= len(states) <= 64:
            raise CoreError("invalid_states", "States must be a nonempty collection of at most 64 strings")
        if any(not isinstance(state, str) or not state.strip() or len(state) > 128 or "\0" in state for state in states):
            raise CoreError("invalid_states", "State names must be nonblank strings of at most 128 characters")
        states = sorted(set(states))
        placeholders = ",".join("?" for _ in states)
        with self._lock:
            rows = self._db.execute(
                # Without the explicit index, SQLite can prefer the primary
                # key's ID ordering over filtering states when IN has >1 value.
                "SELECT * FROM records INDEXED BY records_states WHERE namespace=? AND scope=? AND json_extract(body,'$.state') IN ("
                + placeholders + ") ORDER BY id", (namespace, scope, *states))
            return [self._record(row) for row in rows]

    def put(self, namespace, key, scope, body, expected_revision=0):
        validate_scope(scope)
        if not namespace or not key or not isinstance(body, dict) or type(expected_revision) is not int or expected_revision < 0:
            raise CoreError("invalid_record", "Record identity, object body and expected revision are required")
        encoded = canonical_json(body)
        with self.transaction():
            prior = self.get(namespace,key,scope)
            revision = prior.revision if prior else 0
            if revision != expected_revision:
                raise CoreError("conflict", "Record revision changed", expected=expected_revision, actual=revision)
            self._db.execute("INSERT INTO records VALUES(?,?,?,?,?) ON CONFLICT(namespace,id,scope) DO UPDATE SET revision=excluded.revision,body=excluded.body", (namespace,key,scope,revision+1,encoded))
            return Record(namespace,key,scope,revision+1,strict_loads(encoded))

    def delete(self, namespace, key, scope, expected_revision):
        with self.transaction():
            prior = self.get(namespace,key,scope)
            if prior is None or prior.revision != expected_revision:
                raise CoreError("conflict", "Record missing or revision changed")
            self._db.execute("DELETE FROM records WHERE namespace=? AND id=? AND scope=?", (namespace,key,scope))

    def append(self, scope, kind, payload, source="runtime", dedupe_key=None):
        validate_scope(scope)
        if not isinstance(payload, dict) or not kind or not source:
            raise CoreError("invalid_event", "Event requires kind, source and object payload")
        encoded = canonical_json(payload)
        with self.transaction():
            if dedupe_key is not None:
                prior = self._db.execute("SELECT * FROM events WHERE scope=? AND dedupe=?", (scope,dedupe_key)).fetchone()
                if prior:
                    if (prior["kind"],prior["source"],prior["payload"]) != (kind,source,encoded):
                        raise CoreError("dedupe_conflict", "Delivery key reused with different content")
                    return self._event(prior)
            id = new_id("evt")
            self._db.execute("INSERT INTO events(id,scope,kind,source,payload,created,dedupe) VALUES(?,?,?,?,?,?,?)", (id,scope,kind,source,encoded,time.time(),dedupe_key))
            return self._event(self._db.execute("SELECT * FROM events WHERE id=?", (id,)).fetchone())

    def events(self, scope, after=0, limit=1000, kinds=None):
        validate_scope(scope)
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100000:
            raise CoreError("invalid_cursor", "Invalid event range")
        sql, args = "SELECT * FROM events WHERE scope=? AND seq>?", [scope,after]
        if kinds is not None:
            if not kinds:
                return []
            sql += " AND kind IN (" + ",".join("?" for _ in kinds) + ")"
            args.extend(kinds)
        with self._lock:
            return [self._event(r) for r in self._db.execute(sql+" ORDER BY seq LIMIT ?", [*args,limit])]

    def event(self, scope, event_id):
        validate_scope(scope)
        with self._lock:
            row = self._db.execute("SELECT * FROM events WHERE scope=? AND id=?", (scope,event_id)).fetchone()
            return self._event(row) if row else None

    def routed_events(self, scope, kinds, *, after=0, limit=1000, binding_id=None):
        """Page visible routed events before decoding, retaining a global cursor.

        A missing event binding may resolve through its scoped runtime turn.
        The watermark and page share one transaction; unseen matching events
        keep the cursor at the page tail, while exhausted pages skip hidden work.
        """
        validate_scope(scope)
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise CoreError("invalid_cursor", "Invalid routed event range")
        if (not isinstance(kinds, (list, tuple, set, frozenset)) or not 1 <= len(kinds) <= 64
                or any(not isinstance(kind, str) or not kind or len(kind) > 128 or "\0" in kind for kind in kinds)):
            raise CoreError("invalid_event", "Routed events require bounded explicit kinds")
        if binding_id is not None and (not isinstance(binding_id, str) or not binding_id or len(binding_id) > 256 or "\0" in binding_id):
            raise CoreError("invalid_binding", "Event binding must be a bounded identifier")
        kinds = sorted(set(kinds))
        placeholders = ",".join("?" for _ in kinds)
        with self.transaction():
            watermark = self._db.execute("SELECT COALESCE(MAX(seq),0) FROM events WHERE scope=?", (scope,)).fetchone()[0]
            if binding_id is None:
                sql = "SELECT * FROM events INDEXED BY events_kinds WHERE scope=? AND kind IN (" + placeholders + ") AND seq>? AND seq<=? ORDER BY seq LIMIT ?"
                args = (scope, *kinds, after, watermark, limit + 1)
            else:
                # The two branches are disjoint: an explicit binding always wins
                # over a work reference, including conflicting imported evidence.
                sql = ("SELECT * FROM (SELECT e.* FROM events e INDEXED BY events_binding "
                    "WHERE e.scope=? AND json_extract(e.payload,'$.binding_id')=? AND e.kind IN (" + placeholders + ") AND e.seq>? AND e.seq<=? "
                    "UNION ALL SELECT e.* FROM records r INDEXED BY records_bindings CROSS JOIN events e INDEXED BY events_work "
                    "WHERE r.namespace='runtime_turns' AND r.scope=? AND json_extract(r.body,'$.binding_id')=? "
                    "AND e.scope=r.scope AND json_extract(e.payload,'$.work_id')=r.id AND json_extract(e.payload,'$.binding_id') IS NULL "
                    "AND e.kind IN (" + placeholders + ") AND e.seq>? AND e.seq<=?) ORDER BY seq LIMIT ?")
                args = (scope, binding_id, *kinds, after, watermark, scope, binding_id, *kinds, after, watermark, limit + 1)
            rows = self._db.execute(sql, args).fetchall()
            has_more = len(rows) > limit
            rows = rows[:limit]
            cursor = rows[-1]["seq"] if has_more else max(after, watermark)
            return {"events": [self._event(row) for row in rows], "cursor": cursor, "has_more": has_more}

    def cursor(self, scope, consumer):
        validate_scope(scope)
        with self._lock:
            row = self._db.execute("SELECT seq FROM cursors WHERE scope=? AND consumer=?", (scope,consumer)).fetchone()
            return row[0] if row else 0

    def advance_cursor(self, scope, consumer, seq):
        validate_scope(scope)
        if type(seq) is not int or seq < 0 or not consumer:
            raise CoreError("invalid_cursor", "Invalid consumer cursor")
        with self.transaction():
            max_seq = self._db.execute("SELECT COALESCE(MAX(seq),0) FROM events WHERE scope=?", (scope,)).fetchone()[0]
            if seq > max_seq or seq < self.cursor(scope,consumer):
                raise CoreError("invalid_cursor", "Cursor cannot regress or exceed available events")
            self._db.execute("INSERT INTO cursors VALUES(?,?,?) ON CONFLICT(scope,consumer) DO UPDATE SET seq=excluded.seq", (scope,consumer,seq))

    def close(self):
        with self._lock:
            if not self._closed:
                self._db.close()
                self._closed = True
