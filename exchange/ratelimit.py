from __future__ import annotations

import math
import threading
import time

from fastapi import HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from exchange.config import client_ip_matches_register_trusted_rules, settings

# ---------------------------------------------------------------------------
# Global slowapi limiter (per-IP, in-memory)
#
# NOTE: with multiple Gunicorn workers each process holds its own counter,
# so effective limits are ~workers × configured value.  For true shared
# state, swap the storage to Redis via `storage_uri="redis://..."`.
# ---------------------------------------------------------------------------

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.rate_limit_public],
    enabled=True,
)

# ---------------------------------------------------------------------------
# Registration-specific rate limiter (per-IP; env-tunable; optional trusted-IP bypass)
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_hits: dict[str, list[float]] = {}
_last_cleanup = 0.0
_CLEANUP_INTERVAL = 300.0  # purge stale IPs every 5 minutes


def _cleanup(now: float) -> None:
    global _last_cleanup
    if now - _last_cleanup < _CLEANUP_INTERVAL:
        return
    _last_cleanup = now
    cutoff = now - 86400
    stale = [ip for ip, ts in _hits.items() if ts[-1] < cutoff]
    for ip in stale:
        del _hits[ip]


def _first_index_at_or_after(timestamps: list[float], threshold: float) -> int:
    lo, hi = 0, len(timestamps)
    while lo < hi:
        mid = (lo + hi) // 2
        if timestamps[mid] < threshold:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _count_since(timestamps: list[float], since: float) -> int:
    idx = _first_index_at_or_after(timestamps, since)
    return len(timestamps) - idx


def _retry_after_window_ends(timestamps: list[float], now: float, window_seconds: float) -> int:
    """Seconds until the oldest hit in [now - window, now] expires (ceiling, at least 1)."""
    idx = _first_index_at_or_after(timestamps, now - window_seconds)
    if idx >= len(timestamps):
        return 1
    oldest = timestamps[idx]
    return max(1, int(math.ceil(oldest + window_seconds - now)))


def _register_rate_limit_exceeded(
    *,
    message: str,
    limit_kind: str,
    retry_after_seconds: int,
) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail={
            "error": "rate_limit_exceeded",
            "message": message,
            "limit": "registration",
            "limit_kind": limit_kind,
            "retry_after_seconds": retry_after_seconds,
        },
        headers={"Retry-After": str(retry_after_seconds)},
    )


def check_register_rate_limit(request: Request) -> None:
    """FastAPI dependency that enforces per-IP registration rate limits."""
    hour_limit = settings.register_rate_limit_per_hour
    day_limit = settings.register_rate_limit_per_day

    if hour_limit <= 0 and day_limit <= 0:
        return

    ip = request.client.host if request.client else "unknown"
    if client_ip_matches_register_trusted_rules(ip, settings.register_trusted_ip_rules):
        return

    now = time.monotonic()

    with _lock:
        _cleanup(now)
        timestamps = _hits.setdefault(ip, [])

        if hour_limit > 0 and _count_since(timestamps, now - 3600) >= hour_limit:
            ra = _retry_after_window_ends(timestamps, now, 3600.0)
            raise _register_rate_limit_exceeded(
                message="Registration rate limit exceeded. Try again later.",
                limit_kind="per_ip_per_hour",
                retry_after_seconds=ra,
            )

        if day_limit > 0 and _count_since(timestamps, now - 86400) >= day_limit:
            ra = _retry_after_window_ends(timestamps, now, 86400.0)
            raise _register_rate_limit_exceeded(
                message="Daily registration limit exceeded. Try again tomorrow.",
                limit_kind="per_ip_per_day",
                retry_after_seconds=ra,
            )

        timestamps.append(now)
