"""Atomic account creation and cross-process credential refresh fencing.

No transaction remains open across network I/O. An abandoned refresh lease is
treated as uncertain: replaying a possibly rotated refresh token is unsafe, so a
fresh sign-in is required rather than silently repeating the exchange.
"""
from __future__ import annotations

import secrets

from .errors import GatewayError
from .wire import json_dumps


def _read(store, db, tenant, kind, id):
    row = db.execute("SELECT * FROM objects WHERE tenant=? AND kind=? AND id=?", (tenant, kind, id)).fetchone()
    if row is None:
        raise GatewayError("not_found", "This account sign-in is unavailable.", 404)
    return store._row(row)


def _write(store, db, tenant, kind, row, secret=None):
    """Apply one object revision and encrypted secret rotation in the caller transaction."""
    body = {k: v for k, v in row.items() if k not in {"id", "revision"}}
    if secret is not None:
        if not isinstance(secret, str) or len(secret.encode()) > 65536 or any(ord(c) < 32 or ord(c) > 126 for c in secret):
            raise GatewayError("invalid_auth_response", "The account credential exceeds the storage bounds.", 502)
        if body.get("secret_ref"):
            db.execute("DELETE FROM secrets WHERE tenant=? AND id=?", (tenant, body["secret_ref"]))
        body["secret_ref"] = None
        if secret:
            ref, nonce = "secret_" + secrets.token_hex(16), secrets.token_bytes(12)
            cipher = store._cipher.encrypt(nonce, secret.encode(), json_dumps([tenant, ref]).encode())
            db.execute("INSERT INTO secrets VALUES (?,?,?,?)", (tenant, ref, nonce, cipher))
            body["secret_ref"] = ref
    revision = row["revision"] + 1
    db.execute("INSERT INTO objects VALUES (?,?,?,?,?) ON CONFLICT(tenant,kind,id) DO UPDATE SET revision=excluded.revision,body=excluded.body",
               (tenant, kind, row["id"], revision, json_dumps(body)))
    return {**body, "id": row["id"], "revision": revision}


def finish_session(store, tenant, session_id, account_id, account_body, secret, now):
    """Create the account and destroy temporary credentials under one cancellation fence."""
    tenant = store._tenant(tenant)
    with store.transaction() as db:
        row = _read(store, db, tenant, "auth_session", session_id)
        if row["status"] != "pending":
            return row
        if row["expires_at"] <= now:
            return _write(store, db, tenant, "auth_session", {**row, "status": "expired"}, "")
        _write(store, db, tenant, "auth_account", {**account_body, "id": account_id, "revision": 0}, secret)
        return _write(store, db, tenant, "auth_session", {**row, "status": "connected", "account_id": account_id}, "")


def claim_refresh(store, tenant, account_id, expected_revision, now, ttl):
    """Fence a refresh before contacting a token endpoint, including separate processes."""
    tenant = store._tenant(tenant)
    with store.transaction() as db:
        row = _read(store, db, tenant, "auth_account", account_id)
        if row["status"] != "connected":
            raise GatewayError("account_disconnected", "This account was disconnected. Sign in again.", 503)
        if row.get("refresh_uncertain") or (row.get("refresh_lease") and row["refresh_lease"]["expires_at"] <= now):
            raise GatewayError("account_reauth_required", "A previous account refresh was interrupted. Sign in again to restore it.", 503)
        if row.get("refresh_lease"):
            raise GatewayError("auth_refresh_busy", "This account is refreshing. Retry shortly.", 503, retryable=True)
        if row["revision"] != expected_revision:
            raise GatewayError("auth_refresh_busy", "This account was refreshed by another gateway worker. Retry shortly.", 503, retryable=True)
        owner = secrets.token_hex(16)
        return _write(store, db, tenant, "auth_account", {**row, "refresh_lease": {"owner": owner, "expires_at": now + ttl}})


def finish_refresh(store, tenant, lease, *, secret=None, expires_at=None, uncertain=False):
    """Commit only the lease owner; disconnect and later writers always win."""
    tenant = store._tenant(tenant)
    with store.transaction() as db:
        row = _read(store, db, tenant, "auth_account", lease["id"])
        if row["status"] != "connected" or row["revision"] != lease["revision"] or row.get("refresh_lease") != lease["refresh_lease"]:
            raise GatewayError("auth_refresh_superseded", "This account changed during refresh. Reconnect it if needed.", 409)
        row.pop("refresh_lease", None)
        if uncertain:
            row["refresh_uncertain"] = True
        if expires_at is not None:
            row["expires_at"] = expires_at
        return _write(store, db, tenant, "auth_account", row, secret)
