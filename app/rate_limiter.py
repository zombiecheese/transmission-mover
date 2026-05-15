"""Rate limiter for authentication attempts."""

import time
from collections import defaultdict

# Track failed attempts: {ip_address: [(timestamp, attempts), ...]}
_failed_attempts: dict[str, list[tuple[float, int]]] = defaultdict(list)
_WINDOW_SECONDS = 300  # 5-minute window
_MAX_ATTEMPTS = 5


def check_rate_limit(ip_address: str) -> bool:
    """
    Check if an IP has exceeded rate limit for failed auth attempts.
    Returns True if allowed (within limit), False if rate limited.
    """
    now = time.time()
    cutoff = now - _WINDOW_SECONDS

    # Clean old entries
    if ip_address in _failed_attempts:
        _failed_attempts[ip_address] = [
            (ts, attempts) for ts, attempts in _failed_attempts[ip_address] if ts > cutoff
        ]

    total_attempts = sum(attempts for ts, attempts in _failed_attempts[ip_address])
    return total_attempts < _MAX_ATTEMPTS


def record_failed_attempt(ip_address: str) -> None:
    """Record a failed authentication attempt from an IP."""
    now = time.time()
    if not _failed_attempts[ip_address]:
        _failed_attempts[ip_address].append((now, 1))
    else:
        ts, attempts = _failed_attempts[ip_address][-1]
        if now - ts < 60:  # Same minute
            _failed_attempts[ip_address][-1] = (ts, attempts + 1)
        else:
            _failed_attempts[ip_address].append((now, 1))


def reset_rate_limit(ip_address: str) -> None:
    """Reset rate limit for an IP (called on successful auth)."""
    if ip_address in _failed_attempts:
        del _failed_attempts[ip_address]
