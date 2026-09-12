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
            CREATE TABLE IF NOT EXISTS records(namespace TEXT NOT NULL,id TEXT NOT NULL,scope TEXT NOT NULL,revision INTEGER NOT NULL,body TEXT NOT NULL,PRIMARY KEY(namespace,id,scope));
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
