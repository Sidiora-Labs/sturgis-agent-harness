"""Durable local accounts, revocable sessions and owner-scoped agent lookup."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from dataclasses import asdict, dataclass

from agent.protocol import CoreError, new_id
from consumer.contracts import identifier, public_record

CONTROL = "control"
USERS = "consumer.users"
SESSIONS = "consumer.sessions"


@dataclass(frozen=True)
class Principal:
    id: str
    email: str
    admin: bool
    session_id: str


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _email(value):
    if not isinstance(value, str) or len(value) > 254 or not re.fullmatch(r"[^\s@\x00-\x1f]+@[^\s@\x00-\x1f]+\.[^\s@\x00-\x1f]+", value, re.ASCII):
        raise CoreError("invalid_account", "A valid bounded email address is required")
    return value.casefold()


def _password(value):
    if not isinstance(value, str) or not 12 <= len(value) <= 1024 or "\0" in value:
        raise CoreError("invalid_password", "Password must contain between 12 and 1024 characters")
    return value


def _derive(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024).hex()


def _principal(journal, principal):
    """Never accept caller-constructed privileges or stale sessions as authority."""
    if not isinstance(principal, Principal):
        raise CoreError("unauthenticated", "An authenticated session is required")
    session = journal.get(SESSIONS, principal.session_id, CONTROL)
    user = journal.get(USERS, principal.id, CONTROL)
    if (session is None or user is None or session.value["user_id"] != user.id
            or session.value["revoked"] or session.value["expires_at"] <= time.time()
            or not user.value["enabled"]):
        raise CoreError("unauthenticated", "Session is unavailable or expired")
    return Principal(user.id, user.value["email"], user.value["admin"], session.id)


class Accounts:
    def __init__(self, journal, *, bootstrap_token, allow_signup=False, session_ttl=86400):
        if (not isinstance(bootstrap_token, str) or len(bootstrap_token) < 16
                or type(allow_signup) is not bool or type(session_ttl) not in {int, float}
                or not 60 <= session_ttl <= 30 * 86400):
            raise CoreError("invalid_configuration", "Secure bootstrap token and bounded session lifetime are required")
        self.journal, self.allow_signup, self.session_ttl = journal, allow_signup, session_ttl
        self._bootstrap_digest = _digest(bootstrap_token)

    def status(self):
        return {"bootstrap_required": not bool(self.journal.list(USERS, CONTROL)), "allow_signup": self.allow_signup}

    def _mint(self, user):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now, session_id = time.time(), new_id("session")
        row = self.journal.put(SESSIONS, session_id, CONTROL, {
            "user_id": user.id, "token_digest": _digest(token), "csrf_digest": _digest(csrf),
            "expires_at": now + self.session_ttl, "created_at": now, "revoked": False})
        self.journal.put("consumer.session_tokens", _digest(token), CONTROL, {"session_id": row.id})
        principal = Principal(user.id, user.value["email"], user.value["admin"], row.id)
        return {"principal": asdict(principal), "token": token, "csrf_token": csrf, "expires_at": row.value["expires_at"]}

    def _create(self, email, password, *, admin, bootstrap=False):
        email, password = _email(email), _password(password)
        salt = secrets.token_hex(16)
        encoded = _derive(password, salt)  # Expensive work happens before the storage lock.
        with self.journal.transaction():
            present = bool(self.journal.list(USERS, CONTROL))
            if bootstrap == present:
                raise CoreError("bootstrap_closed" if bootstrap else "bootstrap_required", "Account registration is unavailable")
            if self.journal.get("consumer.emails", _digest(email), CONTROL):
                raise CoreError("account_unavailable", "Account registration is unavailable")
            user = self.journal.put(USERS, new_id("user"), CONTROL, {
                "email": email, "admin": admin, "enabled": True, "salt": salt,
                "password_hash": encoded, "password_algorithm": "scrypt-16384-8-1", "created_at": time.time()})
            self.journal.put("consumer.emails", _digest(email), CONTROL, {"user_id": user.id})
            self.journal.append(CONTROL, "account.created", {"user_id": user.id, "admin": admin}, source="consumer.identity")
            return self._mint(user)

    def bootstrap(self, email, password, token):
        if not isinstance(token, str) or len(token) > 4096 or not hmac.compare_digest(_digest(token), self._bootstrap_digest):
            raise CoreError("unauthenticated", "Bootstrap authorization failed")
        return self._create(email, password, admin=True, bootstrap=True)

    def signup(self, email, password):
        if not self.allow_signup:
            raise CoreError("signup_disabled", "Account registration is disabled")
        return self._create(email, password, admin=False)

    def login(self, email, password):
        email = _email(email)
        if not isinstance(password, str) or len(password) > 1024:
            raise CoreError("unauthenticated", "Invalid email or password")
        key, now = _digest(email), time.time()
        # Increment before hashing, so simultaneous failures cannot evade throttling.
        with self.journal.transaction():
            rate = self.journal.get("consumer.login_limits", key, CONTROL)
            recent = rate and rate.value["window_start"] + 60 > now
            count = rate.value["count"] if recent else 0
            if count >= 10:
                raise CoreError("rate_limited", "Too many login attempts; try again later", retry_after=60)
            self.journal.put("consumer.login_limits", key, CONTROL, {
                "window_start": rate.value["window_start"] if recent else now, "count": count + 1},
                expected_revision=rate.revision if rate else 0)
            index = self.journal.get("consumer.emails", key, CONTROL)
            user = self.journal.get(USERS, index.value["user_id"], CONTROL) if index else None
        salt = user.value["salt"] if user else "0" * 32
        expected = user.value["password_hash"] if user else "0" * 64
        valid = hmac.compare_digest(_derive(password, salt), expected)
        with self.journal.transaction():
            current = self.journal.get(USERS, user.id, CONTROL) if user else None
            if not valid or current is None or not current.value["enabled"] or current.revision != user.revision:
                raise CoreError("unauthenticated", "Invalid email or password")
            return self._mint(current)

    def authenticate(self, token):
        if not isinstance(token, str) or not 32 <= len(token) <= 256:
            raise CoreError("unauthenticated", "Session is unavailable or expired")
        index = self.journal.get("consumer.session_tokens", _digest(token), CONTROL)
        session = self.journal.get(SESSIONS, index.value["session_id"], CONTROL) if index else None
        if session is None or not hmac.compare_digest(session.value["token_digest"], _digest(token)):
            raise CoreError("unauthenticated", "Session is unavailable or expired")
        return _principal(self.journal, Principal(session.value["user_id"], "", False, session.id))

    def validate_csrf(self, principal, token):
        principal = _principal(self.journal, principal)
        session = self.journal.get(SESSIONS, principal.session_id, CONTROL)
        if not isinstance(token, str) or len(token) > 256 or not hmac.compare_digest(_digest(token), session.value["csrf_digest"]):
            raise CoreError("csrf_failed", "Request authorization failed")

    def logout(self, principal):
        with self.journal.transaction():
            principal = _principal(self.journal, principal)
            row = self.journal.get(SESSIONS, principal.session_id, CONTROL)
            self.journal.put(SESSIONS, row.id, CONTROL, {**row.value, "revoked": True}, row.revision)
        return {"logged_out": True}

    def disable(self, principal, user_id, expected_revision):
        identifier(user_id)
        with self.journal.transaction():
            principal = _principal(self.journal, principal)
            if not principal.admin:
                raise CoreError("forbidden", "Account administration is required")
            row = self.journal.get(USERS, user_id, CONTROL)
            if row is None:
                raise CoreError("not_found", "Unknown account")
            if row.value["admin"] and row.value["enabled"] and sum(r.value["admin"] and r.value["enabled"] for r in self.journal.list(USERS, CONTROL)) <= 1:
                raise CoreError("last_administrator", "The last enabled administrator cannot be disabled")
            saved = self.journal.put(USERS, user_id, CONTROL, {**row.value, "enabled": False}, expected_revision)
            for session in self.journal.list(SESSIONS, CONTROL):
                if session.value["user_id"] == user_id and not session.value["revoked"]:
                    self.journal.put(SESSIONS, session.id, CONTROL, {**session.value, "revoked": True}, session.revision)
            self.journal.append(CONTROL, "account.disabled", {"user_id": user_id}, source="consumer.identity")
            return {"id": saved.id, "revision": saved.revision, "enabled": False}


class AgentDirectory:
    def __init__(self, journal):
        self.journal = journal

    @staticmethod
    def _name(name):
        if not isinstance(name, str) or not name.strip() or len(name) > 120 or any(ord(c) < 32 for c in name):
            raise CoreError("invalid_name", "Agent name must contain 1 to 120 printable characters")
        return name.strip()

    def create(self, principal, name):
        name = self._name(name)
        with self.journal.transaction():
            principal = _principal(self.journal, principal)
            row = self.journal.put("consumer.agents", new_id("agent"), CONTROL, {
                "owner_id": principal.id, "name": name, "archived": False, "created_at": time.time()})
            return public_record(row)

    def list(self, principal, *, include_archived=False):
        principal = _principal(self.journal, principal)
        return [public_record(row) for row in self.journal.list("consumer.agents", CONTROL)
                if row.value["owner_id"] == principal.id and (include_archived or not row.value["archived"])]

    def authorize(self, principal, agent_id, permission="read"):
        identifier(agent_id)
        if permission not in {"read", "write", "manage"}:
            raise CoreError("invalid_permission", "Unknown agent permission")
        principal = _principal(self.journal, principal)
        row = self.journal.get("consumer.agents", agent_id, CONTROL)
        if row is None or row.value["owner_id"] != principal.id:
            raise CoreError("not_found", "Agent is unavailable")
        if row.value["archived"] and permission != "read":
            raise CoreError("agent_archived", "Agent is archived")
        return public_record(row)

    def rename(self, principal, agent_id, name, expected_revision):
        name = self._name(name)
        with self.journal.transaction():
            self.authorize(principal, agent_id, "manage")
            row = self.journal.get("consumer.agents", agent_id, CONTROL)
            return public_record(self.journal.put("consumer.agents", row.id, CONTROL, {**row.value, "name": name}, expected_revision))

    def archive(self, principal, agent_id, expected_revision):
        with self.journal.transaction():
            self.authorize(principal, agent_id, "manage")
            row = self.journal.get("consumer.agents", agent_id, CONTROL)
            return public_record(self.journal.put("consumer.agents", row.id, CONTROL, {**row.value, "archived": True}, expected_revision))
