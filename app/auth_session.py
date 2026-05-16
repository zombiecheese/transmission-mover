from __future__ import annotations

import secrets
import threading
import time

SESSION_COOKIE_NAME = "tm_session"
SESSION_TTL_SECONDS = 60 * 60 * 12

_sessions: dict[str, tuple[str, float]] = {}
_lock = threading.Lock()


def _prune_expired(now: float) -> None:
    expired = [token for token, (_username, expires_at) in _sessions.items() if expires_at <= now]
    for token in expired:
        _sessions.pop(token, None)


def create_session(username: str) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _lock:
        _prune_expired(now)
        _sessions[token] = (username, now + SESSION_TTL_SECONDS)
    return token


def get_session_username(token: str | None) -> str | None:
    if not token:
        return None
    now = time.time()
    with _lock:
        _prune_expired(now)
        row = _sessions.get(token)
        if not row:
            return None
        username, expires_at = row
        if expires_at <= now:
            _sessions.pop(token, None)
            return None
        return username


def clear_session(token: str | None) -> None:
    if not token:
        return
    with _lock:
        _sessions.pop(token, None)
