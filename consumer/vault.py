"""Private authenticated-encryption storage; public records contain no secrets."""
from __future__ import annotations

import os
import secrets
import sqlite3
import stat
import threading
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from agent.protocol import CoreError, canonical_json, validate_scope
from consumer.contracts import identifier


class CredentialVault:
    def __init__(self, root):
        supplied = Path(root).absolute()
        if supplied.is_symlink():
            raise CoreError("unsafe_vault", "Vault directory must be private and cannot be a symlink")
        supplied.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root = supplied.resolve()
        self._lock = threading.RLock()
        self._check(self.root, directory=True)
        self._key_path, self._db_path = self.root / "master.key", self.root / "credentials.sqlite"
        try:
            fd = os.open(self._key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(secrets.token_bytes(32))
                stream.flush()
                os.fsync(stream.fileno())
        self._check(self._key_path)
        fd = os.open(self._key_path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            key = stream.read(33)
        if len(key) != 32:
            raise CoreError("unsafe_vault", "Vault key is invalid")
        self._cipher = AESGCM(key)
        fd = os.open(self._db_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        self._check(self._db_path)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS credentials (scope TEXT NOT NULL, name TEXT NOT NULL, revision INTEGER NOT NULL, reference TEXT NOT NULL UNIQUE, active INTEGER NOT NULL, nonce BLOB, ciphertext BLOB, PRIMARY KEY(scope,name))")

    @staticmethod
    def _check(path, *, directory=False):
        info = path.lstat()
        expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if (not expected or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077
                or not directory and info.st_nlink != 1):
            raise CoreError("unsafe_vault", "Vault files must be privately owned and cannot be linked")

    def _connect(self):
        self._check(self.root, directory=True)
        self._check(self._key_path)
        self._check(self._db_path)
        # Connections are short lived, and this vault never calls back into Journal.
        db = sqlite3.connect(self._db_path, timeout=2)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("PRAGMA synchronous=FULL")
        return _Connection(db)

    @staticmethod
    def _metadata(row):
        return {"name": row["name"], "revision": row["revision"], "reference": row["reference"], "active": bool(row["active"])}

    @staticmethod
    def _revision(value):
        if type(value) is not int or value < 0:
            raise CoreError("invalid_revision", "Expected a nonnegative credential revision")

    @staticmethod
    def _aad(scope, name, revision, reference):
        return canonical_json({"scope": scope, "name": name, "revision": revision, "reference": reference}).encode()

    def put(self, scope, name, secret, expected_revision=0):
        validate_scope(scope)
        identifier(name)
        self._revision(expected_revision)
        if not isinstance(secret, str) or not secret or len(secret.encode()) > 65536 or "\0" in secret:
            raise CoreError("invalid_credential", "Credential must be bounded nonempty text")
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM credentials WHERE scope=? AND name=?", (scope, name)).fetchone()
            revision = row["revision"] if row else 0
            if revision != expected_revision:
                raise CoreError("conflict", "Credential revision changed", current_revision=revision)
            revision += 1
            reference, nonce = "cred_" + secrets.token_hex(24), secrets.token_bytes(12)
            ciphertext = self._cipher.encrypt(nonce, secret.encode(), self._aad(scope, name, revision, reference))
            db.execute("INSERT INTO credentials VALUES (?,?,?,?,1,?,?) ON CONFLICT(scope,name) DO UPDATE SET revision=excluded.revision,reference=excluded.reference,active=1,nonce=excluded.nonce,ciphertext=excluded.ciphertext", (scope, name, revision, reference, nonce, ciphertext))
            return {"name": name, "revision": revision, "reference": reference, "active": True}

    def get(self, scope, name):
        validate_scope(scope)
        identifier(name)
        with self._lock, self._connect() as db:
            row = db.execute("SELECT name,revision,reference,active FROM credentials WHERE scope=? AND name=?", (scope, name)).fetchone()
            return self._metadata(row) if row else None

    def list(self, scope):
        validate_scope(scope)
        with self._lock, self._connect() as db:
            return [self._metadata(row) for row in db.execute("SELECT name,revision,reference,active FROM credentials WHERE scope=? ORDER BY name", (scope,))]

    def current(self, scope, reference):
        validate_scope(scope)
        identifier(reference)
        with self._lock, self._connect() as db:
            return db.execute("SELECT 1 FROM credentials WHERE scope=? AND reference=? AND active=1", (scope, reference)).fetchone() is not None

    def resolve(self, scope, reference):
        validate_scope(scope)
        identifier(reference)
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM credentials WHERE scope=? AND reference=? AND active=1", (scope, reference)).fetchone()
            if row is None:
                raise CoreError("credential_unavailable", "Credential reference is unavailable or has changed")
            try:
                return self._cipher.decrypt(row["nonce"], row["ciphertext"], self._aad(scope, row["name"], row["revision"], reference)).decode()
            except (InvalidTag, UnicodeError, ValueError, TypeError) as exc:
                raise CoreError("credential_corrupt", "Credential integrity verification failed") from exc

    def revoke(self, scope, name, expected_revision):
        validate_scope(scope)
        identifier(name)
        self._revision(expected_revision)
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM credentials WHERE scope=? AND name=?", (scope, name)).fetchone()
            if row is None:
                raise CoreError("not_found", "Credential is unavailable")
            if row["revision"] != expected_revision:
                raise CoreError("conflict", "Credential revision changed", current_revision=row["revision"])
            revision, reference = row["revision"] + 1, "cred_" + secrets.token_hex(24)
            db.execute("UPDATE credentials SET revision=?, reference=?,active=0,nonce=NULL,ciphertext=NULL WHERE scope=? AND name=?", (revision, reference, scope, name))
            return {"name": name, "revision": revision, "reference": reference, "active": False}


class _Connection:
    """sqlite's built-in context manager commits but does not close a connection."""
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, kind, value, traceback):
        try:
            self.db.rollback() if kind else self.db.commit()
        finally:
            self.db.close()
