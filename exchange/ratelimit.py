from __future__ import annotations

import threading
import time

from fastapi import HTTPException, Request

from exchange.config import settings

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


def _count_since(timestamps: list[float], since: float) -> int:
    lo, hi = 0, len(timestamps)
    while lo < hi:
        mid = (lo + hi) // 2
        if timestamps[mid] < since:
            lo = mid + 1
        else:
            hi = mid
    return len(timestamps) - lo


def check_register_rate_limit(request: Request) -> None:
    """FastAPI dependency that enforces per-IP registration rate limits."""
    hour_limit = settings.register_rate_limit_per_hour
    day_limit = settings.register_rate_limit_per_day

    if hour_limit <= 0 and day_limit <= 0:
        return

    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()

    with _lock:
        _cleanup(now)
        timestamps = _hits.setdefault(ip, [])

        if hour_limit > 0 and _count_since(timestamps, now - 3600) >= hour_limit:
            raise HTTPException(
                status_code=429,
                detail="Registration rate limit exceeded. Try again later.",
                headers={"Retry-After": "3600"},
            )

        if day_limit > 0 and _count_since(timestamps, now - 86400) >= day_limit:
            raise HTTPException(
                status_code=429,
                detail="Daily registration limit exceeded. Try again tomorrow.",
                headers={"Retry-After": "86400"},
            )

        timestamps.append(now)
