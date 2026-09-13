"""Gateway-owned SQLite state, encrypted upstream accounts and hashed client keys."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import threading
import time

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from model_gateway.errors import GatewayError
from model_gateway.wire import json_dumps, json_loads


def text(value, label="value", maximum=256):
    """Validate text before it becomes a durable identifier or public label."""
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise GatewayError("invalid_request", f"Enter a valid {label}.")
    return value.strip()


class GatewayStore:
    """An independent data owner; every operation requires an authenticated tenant."""

    def __init__(self, root):
        self.root = Path(root).absolute()
        if self.root.is_symlink():
            raise GatewayError("unsafe_storage", "Gateway storage cannot be a symbolic link.")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._private(self.root, directory=True)
        self.path = self.root / "gateway.sqlite"
        key_path = self.root / "master.key"
        key = self._private_file(key_path, secrets.token_bytes(32))
        if len(key) != 32:
            raise GatewayError("unsafe_storage", "Gateway encryption key is invalid.")
        self._cipher = AESGCM(key)
        self._private_file(self.path, b"")
        self._lock = threading.RLock()
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS objects (
                  tenant TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL,
                  revision INTEGER NOT NULL, body TEXT NOT NULL,
                  PRIMARY KEY(tenant,kind,id));
                CREATE TABLE IF NOT EXISTS secrets (
                  tenant TEXT NOT NULL, id TEXT NOT NULL, nonce BLOB NOT NULL,
                  ciphertext BLOB NOT NULL, PRIMARY KEY(tenant,id));
                CREATE TABLE IF NOT EXISTS client_keys (
                  digest TEXT PRIMARY KEY, tenant TEXT NOT NULL, id TEXT UNIQUE NOT NULL,
                  name TEXT NOT NULL, prefix TEXT NOT NULL, models TEXT NOT NULL,
                  created_at REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS usage (
                  id TEXT PRIMARY KEY, tenant TEXT NOT NULL, client_id TEXT NOT NULL,
                  model TEXT NOT NULL, connection_id TEXT NOT NULL,
                  status TEXT NOT NULL, input_tokens INTEGER, output_tokens INTEGER,
                  total_tokens INTEGER, created_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS usage_tenant ON usage(tenant,created_at);
            """)

    @staticmethod
    def _private(path, directory=False):
        info = path.lstat()
        valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if not valid or info.st_uid != os.getuid() or info.st_mode & 0o077 or not directory and info.st_nlink != 1:
            raise GatewayError("unsafe_storage", "Gateway files must be privately owned and unlinked.")

    def _private_file(self, path, initial):
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(initial)
                stream.flush()
                os.fsync(stream.fileno())
        self._private(path)
        if path == self.path:
            return b""
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            return stream.read(4096)

    @contextmanager
    def transaction(self):
        """Serialize local writes; SQLite also fences other standalone processes."""
        with self._lock:
            self._private(self.root, directory=True)
            self._private(self.path)
            db = sqlite3.connect(self.path, timeout=5)
            db.row_factory = sqlite3.Row
            try:
                db.execute("PRAGMA synchronous=FULL")
                db.execute("BEGIN IMMEDIATE")
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    @staticmethod
    def _row(row):
        return {**json_loads(row["body"]), "id": row["id"], "revision": row["revision"]}

    @staticmethod
    def _tenant(tenant):
        return text(tenant, "tenant", 128)

    def get(self, tenant, kind, id):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM objects WHERE tenant=? AND kind=? AND id=?", (self._tenant(tenant), kind, text(id))).fetchone()
            if row is None:
                raise GatewayError("not_found", "This gateway item is unavailable.", 404)
            return self._row(row)

    def list(self, tenant, kind):
        with self.transaction() as db:
            return [self._row(row) for row in db.execute("SELECT * FROM objects WHERE tenant=? AND kind=? ORDER BY id", (self._tenant(tenant), kind))]

    @staticmethod
    def _revision(value):
        if type(value) is not int or value < 0:
            raise GatewayError("invalid_revision", "Refresh this item before saving it.")

    def put(self, tenant, kind, id, body, expected_revision=0, *, secret=None):
        """Atomically persist a revision and optional encrypted credential rotation."""
        tenant, id = self._tenant(tenant), text(id)
        self._revision(expected_revision)
        body = dict(body)
        body.pop("id", None)
        body.pop("revision", None)
        if secret is not None and (not isinstance(secret, str) or len(secret.encode()) > 65536 or any(ord(c) < 32 or ord(c) > 126 for c in secret)):
            raise GatewayError("invalid_key", "API keys must be bounded printable text.")
        with self.transaction() as db:
            prior = db.execute("SELECT * FROM objects WHERE tenant=? AND kind=? AND id=?", (tenant, kind, id)).fetchone()
            if (prior["revision"] if prior else 0) != expected_revision:
                raise GatewayError("conflict", "This item changed. Refresh it and try again.", 409)
            if secret is not None:
                old_ref = json_loads(prior["body"]).get("secret_ref") if prior else None
                if old_ref:
                    db.execute("DELETE FROM secrets WHERE tenant=? AND id=?", (tenant, old_ref))
                body["secret_ref"] = None
                if secret:
                    ref, nonce = "secret_" + secrets.token_hex(16), secrets.token_bytes(12)
                    ciphertext = self._cipher.encrypt(nonce, secret.encode(), json_dumps([tenant, ref]).encode())
                    db.execute("INSERT INTO secrets VALUES (?,?,?,?)", (tenant, ref, nonce, ciphertext))
                    body["secret_ref"] = ref
            db.execute("INSERT INTO objects VALUES (?,?,?,?,?) ON CONFLICT(tenant,kind,id) DO UPDATE SET revision=excluded.revision,body=excluded.body",
                       (tenant, kind, id, expected_revision + 1, json_dumps(body)))
        return {**body, "id": id, "revision": expected_revision + 1}

    def resolve(self, tenant, reference):
        if not reference:
            return None
        with self.transaction() as db:
            row = db.execute("SELECT * FROM secrets WHERE tenant=? AND id=?", (self._tenant(tenant), reference)).fetchone()
            if row is None:
                raise GatewayError("credential_unavailable", "Reconnect this provider to restore its API key.", 503)
            try:
                return self._cipher.decrypt(row["nonce"], row["ciphertext"], json_dumps([tenant, reference]).encode()).decode()
            except (InvalidTag, ValueError, UnicodeError):
                raise GatewayError("credential_unavailable", "The stored credential could not be verified. Reconnect this provider.", 503) from None

    def delete_connection(self, tenant, id, expected_revision):
        """Remove the account and secret, pruning references from routing recipes."""
        self._revision(expected_revision)
        with self.transaction() as db:
            row = db.execute("SELECT * FROM objects WHERE tenant=? AND kind='connection' AND id=?", (self._tenant(tenant), text(id))).fetchone()
            if row is None:
                raise GatewayError("not_found", "This connection is unavailable.", 404)
            if row["revision"] != expected_revision:
                raise GatewayError("conflict", "This connection changed. Refresh it first.", 409)
            body = json_loads(row["body"])
            db.execute("DELETE FROM secrets WHERE tenant=? AND id=?", (tenant, body.get("secret_ref")))
            db.execute("DELETE FROM objects WHERE tenant=? AND kind='connection' AND id=?", (tenant, id))
            for route in db.execute("SELECT * FROM objects WHERE tenant=? AND kind='route'", (tenant,)).fetchall():
                value = json_loads(route["body"])
                remaining = [m for m in value["models"] if not m.startswith(id + "/")]
                if remaining == value["models"]:
                    continue
                if remaining:
                    value["models"] = remaining
                    db.execute("UPDATE objects SET revision=revision+1,body=? WHERE tenant=? AND kind='route' AND id=?", (json_dumps(value), tenant, route["id"]))
                else:
                    db.execute("DELETE FROM objects WHERE tenant=? AND kind='route' AND id=?", (tenant, route["id"]))
        return {"disconnected": True}

    def delete_route(self, tenant, id, expected_revision):
        """Remove exactly the reviewed route revision within its owning tenant."""
        tenant, id = self._tenant(tenant), text(id)
        self._revision(expected_revision)
        with self.transaction() as db:
            row = db.execute("SELECT revision FROM objects WHERE tenant=? AND kind='route' AND id=?", (tenant, id)).fetchone()
            if row is None:
                raise GatewayError("not_found", "This model route is unavailable.", 404)
            if row["revision"] != expected_revision:
                raise GatewayError("conflict", "This model route changed. Refresh it first.", 409)
            db.execute("DELETE FROM objects WHERE tenant=? AND kind='route' AND id=?", (tenant, id))
        return {"deleted": True}

    @staticmethod
    def public_connection(value):
        return {**{k: v for k, v in value.items() if k != "secret_ref"}, "credential_configured": bool(value.get("secret_ref"))}

    @staticmethod
    def _key_metadata(row):
        return {"id": row["id"], "name": row["name"], "prefix": row["prefix"], "models": json_loads(row["models"]), "created_at": row["created_at"], "revoked": bool(row["revoked"])}

    def create_key(self, tenant, name, models=()):
        name, tenant = text(name, "key name", 120), self._tenant(tenant)
        if not isinstance(models, (list, tuple)) or len(models) > 256 or any(not isinstance(m, str) or not m or len(m) > 512 for m in models):
            raise GatewayError("invalid_request", "Choose valid model names for this key.")
        key = "stg_" + secrets.token_urlsafe(36)
        value = {"id": "key_" + secrets.token_hex(12), "name": name, "prefix": key[:12], "models": list(dict.fromkeys(models)), "created_at": time.time(), "revoked": False}
        with self.transaction() as db:
            db.execute("INSERT INTO client_keys VALUES (?,?,?,?,?,?,?,0)", (hashlib.sha256(key.encode()).hexdigest(), tenant, value["id"], name, value["prefix"], json_dumps(value["models"]), value["created_at"]))
        return {**value, "key": key}

    def keys(self, tenant):
        with self.transaction() as db:
            return [self._key_metadata(row) for row in db.execute("SELECT * FROM client_keys WHERE tenant=? ORDER BY created_at", (self._tenant(tenant),))]

    def authenticate(self, key):
        if not isinstance(key, str) or not 16 <= len(key) <= 256:
            raise GatewayError("unauthenticated", "A valid gateway API key is required.", 401)
        with self.transaction() as db:
            row = db.execute("SELECT * FROM client_keys WHERE digest=? AND revoked=0", (hashlib.sha256(key.encode()).hexdigest(),)).fetchone()
            if row is None:
                raise GatewayError("unauthenticated", "This gateway API key is invalid or revoked.", 401)
            return {**self._key_metadata(row), "tenant": row["tenant"]}

    def revoke_key(self, tenant, id):
        with self.transaction() as db:
            result = db.execute("UPDATE client_keys SET revoked=1 WHERE tenant=? AND id=?", (self._tenant(tenant), text(id)))
            if not result.rowcount:
                raise GatewayError("not_found", "This API key is unavailable.", 404)
        return {"revoked": True}

    def record_usage(self, tenant, client_id, model, connection_id, status, usage=None):
        """Record factual consumption once per upstream attempt, including failures."""
        usage = usage or {}
        counts = [usage.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens")]
        if any(n is not None and (type(n) is not int or n < 0 or n >= 2**63) for n in counts):
            raise GatewayError("invalid_usage", "Upstream usage counts are invalid.", 502)
        with self.transaction() as db:
            db.execute("INSERT INTO usage VALUES (?,?,?,?,?,?,?,?,?,?)", (secrets.token_hex(16), self._tenant(tenant), client_id, model, connection_id, status, *counts, time.time()))

    def usage(self, tenant):
        with self.transaction() as db:
            totals = db.execute("SELECT COUNT(*) requests,COALESCE(SUM(input_tokens),0) input_tokens,COALESCE(SUM(output_tokens),0) output_tokens,COALESCE(SUM(total_tokens),0) total_tokens,COALESCE(SUM(status!='completed'),0) failures,SUM(total_tokens IS NULL) unreported FROM usage WHERE tenant=?", (self._tenant(tenant),)).fetchone()
            recent = [{**dict(row), "tokens": row["total_tokens"]} for row in db.execute("SELECT model,connection_id,status,input_tokens,output_tokens,total_tokens,created_at FROM usage WHERE tenant=? ORDER BY created_at DESC LIMIT 50", (tenant,))]
            return {**dict(totals), "recent": recent}
