"""Federation health endpoint implementation.

Serves ``/.well-known/a2a-federation-health`` with operational telemetry.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()

_start_time = time.monotonic()
_attestation_latencies: list[float] = []
_MAX_LATENCY_SAMPLES = 1000


def record_attestation_latency(latency_ms: float) -> None:
    """Record an attestation verification latency sample."""
    _attestation_latencies.append(latency_ms)
    if len(_attestation_latencies) > _MAX_LATENCY_SAMPLES:
        _attestation_latencies.pop(0)


def _avg_latency() -> int:
    if not _attestation_latencies:
        return 0
    return int(sum(_attestation_latencies) / len(_attestation_latencies))


def _uptime_ratio() -> float:
    """Approximate uptime based on process uptime.

    A proper implementation would track downtime events. For MVP,
    we report 1.0 if the process has been running.
    """
    return 1.0


@router.get("/.well-known/a2a-federation-health")
async def federation_health(request: Request) -> JSONResponse:
    """Return federation health telemetry."""
    from exchange.config import settings

    node_did = getattr(settings, "federation_node_did", "")
    active_peers = 0

    db = getattr(request.app.state, "db", None)
    if db:
        try:
            from exchange.federation.models import FederationPeer
            from sqlalchemy import select, func

            async with db() as session:
                result = await session.execute(
                    select(func.count()).where(
                        FederationPeer.status == "active"
                    )
                )
                active_peers = result.scalar() or 0
        except Exception:
            pass

    payload = {
        "status": "operational",
        "node_did": node_did,
        "avg_attestation_latency_ms": _avg_latency(),
        "uptime_90d": _uptime_ratio(),
        "version": getattr(settings, "version", "0.10.0"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "active_peers": active_peers,
        "federation_protocol_version": "0.1.0",
    }

    return JSONResponse(
        payload,
        headers={"Cache-Control": "no-cache"},
    )
