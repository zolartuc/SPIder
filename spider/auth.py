"""Accounts, password hashing and cookie sessions.

Passwords are hashed with scrypt (Python standard library), sessions are random
tokens stored server-side, and the cookie is HttpOnly + SameSite=Strict.
"""

import hashlib
import hmac
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request

from .db import connect, now

COOKIE = "spider_session"
SESSION_DAYS = 14
ROLES = ("admin", "viewer")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
MIN_PASSWORD = 8

# --------------------------------------------------------------------------- passwords

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        test = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(test.hex(), digest)
    except (ValueError, TypeError):
        return False


def check_new_credentials(username: str | None, password: str | None) -> None:
    if username is not None and not USERNAME_RE.match(username):
        raise HTTPException(422, "Usernames are 3 to 32 characters: letters, numbers, dots, dashes or underscores.")
    if password is not None and len(password) < MIN_PASSWORD:
        raise HTTPException(422, f"Passwords need at least {MIN_PASSWORD} characters.")


# --------------------------------------------------------------------------- users

def has_users() -> bool:
    with connect() as conn:
        return conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


def public_user(row) -> dict:
    keys = row.keys()
    return {"id": row["id"], "username": row["username"], "role": row["role"], "theme": row["theme"],
            "layout": row["layout"] if "layout" in keys else None}


def list_users() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY role, username").fetchall()
    return [public_user(r) | {"created": r["created"]} for r in rows]


def create_user(username: str, password: str, role: str = "viewer") -> dict:
    check_new_credentials(username, password)
    if role not in ROLES:
        raise HTTPException(422, "Role must be admin or viewer.")
    with connect() as conn:
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise HTTPException(409, f"There's already a user called {username}.")
        cur = conn.execute(
            "INSERT INTO users (username, pw_hash, role, created) VALUES (?, ?, ?, ?)",
            (username, hash_password(password), role, now()),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
    return public_user(row)


def delete_user(user_id: int, acting_user: dict) -> None:
    if user_id == acting_user["id"]:
        raise HTTPException(400, "You can't remove your own account while signed in to it.")
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(404, "No such user.")
        if row["role"] == "admin":
            admins = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]
            if admins <= 1:
                raise HTTPException(400, "SPIder needs at least one admin.")
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def change_password(user_id: int, current: str, new: str) -> None:
    check_new_credentials(None, new)
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row or not verify_password(current, row["pw_hash"]):
            raise HTTPException(403, "Your current password isn't right.")
        conn.execute("UPDATE users SET pw_hash = ? WHERE id = ?", (hash_password(new), user_id))
        # Sign out every other session for this account.
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def set_layout(user_id: int, layout: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE users SET layout = ? WHERE id = ?", (layout, user_id))


def set_theme(user_id: int, theme: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE users SET theme = ? WHERE id = ?", (theme, user_id))


# --------------------------------------------------------------------------- sign-in throttling

_failures: dict[str, deque] = defaultdict(deque)
MAX_FAILURES, WINDOW = 5, 300  # 5 wrong passwords per 5 minutes per address


def _throttled(ip: str) -> bool:
    q = _failures[ip]
    while q and q[0] < time.monotonic() - WINDOW:
        q.popleft()
    return len(q) >= MAX_FAILURES


def authenticate(username: str, password: str, ip: str) -> dict:
    if _throttled(ip):
        raise HTTPException(429, "Too many wrong passwords. Wait five minutes, then try again.")
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    # Verify against a dummy hash when the user doesn't exist, so timing doesn't reveal usernames.
    ok = verify_password(password, row["pw_hash"] if row else _DUMMY_HASH)
    if not (row and ok):
        _failures[ip].append(time.monotonic())
        raise HTTPException(401, "That username and password don't match.")
    _failures.pop(ip, None)
    return public_user(row)


_DUMMY_HASH = hash_password(secrets.token_hex(8))

# --------------------------------------------------------------------------- sessions


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE expires < ?", (now(),))
        conn.execute(
            "INSERT INTO sessions (token, user_id, created, expires) VALUES (?, ?, ?, ?)",
            (token, user_id, now(), expires),
        )
    return token


def delete_session(token: str | None) -> None:
    if token:
        with connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def current_user(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ? AND s.expires > ?",
            (token, now()),
        ).fetchone()
    return public_user(row) if row else None


def set_cookie(response, token: str) -> None:
    response.set_cookie(
        COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="strict", path="/"
    )


# --------------------------------------------------------------------------- FastAPI dependencies

def require_user(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(401, "Sign in to do that.")
    return user


def require_admin(request: Request) -> dict:
    user = require_user(request)
    if user["role"] != "admin":
        raise HTTPException(403, "Only admins can change this.")
    return user
