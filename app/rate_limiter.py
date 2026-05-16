"""Rate limiter for authentication attempts."""

import time
from collections import defaultdict

# Track auth failures by IP.
_failed_attempts: dict[str, int] = defaultdict(int)
_last_failure_at: dict[str, float] = {}
_lockout_until: dict[str, float] = {}

_WINDOW_SECONDS = 300  # Reset failure streak if no failures for 5 minutes.
_MAX_ATTEMPTS = 5
_BASE_LOCKOUT_SECONDS = 30
_MAX_LOCKOUT_SECONDS = 1800  # 30 minutes


def _clear_expired_lockout(ip_address: str, now: float) -> None:
    locked_until = _lockout_until.get(ip_address)
    if locked_until is not None and now >= locked_until:
        _lockout_until.pop(ip_address, None)


def get_rate_limit_status(ip_address: str) -> tuple[bool, int]:
    """
    Return rate-limit status for an IP.
    Returns (is_allowed, retry_after_seconds).
    """
    now = time.time()
    _clear_expired_lockout(ip_address, now)

    locked_until = _lockout_until.get(ip_address)
    if locked_until is None:
        return True, 0
    retry_after = max(0, int(locked_until - now))
    return retry_after == 0, retry_after


def check_rate_limit(ip_address: str) -> bool:
    """
    Check if an IP has exceeded rate limit for failed auth attempts.
    Returns True if allowed (within limit), False if rate limited.
    """
    allowed, _retry_after = get_rate_limit_status(ip_address)
    return allowed


def record_failed_attempt(ip_address: str) -> int:
    """
    Record a failed authentication attempt from an IP.
    Returns active lockout duration in seconds (0 if not locked).
    """
    now = time.time()

    last_failure = _last_failure_at.get(ip_address)
    if last_failure is None or (now - last_failure) > _WINDOW_SECONDS:
        _failed_attempts[ip_address] = 0

    _last_failure_at[ip_address] = now
    _failed_attempts[ip_address] += 1

    failures = _failed_attempts[ip_address]
    if failures <= _MAX_ATTEMPTS:
        return 0

    level = failures - _MAX_ATTEMPTS
    lockout_seconds = min(_BASE_LOCKOUT_SECONDS * (2 ** (level - 1)), _MAX_LOCKOUT_SECONDS)
    _lockout_until[ip_address] = now + lockout_seconds
    return lockout_seconds


def reset_rate_limit(ip_address: str) -> None:
    """Reset rate limit for an IP (called on successful auth)."""
    _failed_attempts.pop(ip_address, None)
    _last_failure_at.pop(ip_address, None)
    _lockout_until.pop(ip_address, None)
