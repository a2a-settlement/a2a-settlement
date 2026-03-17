"""Federation peering endpoint — ``/federation/peer``.

Implements the mutual cryptographic peering handshake defined in the
A2A-SE Federation Protocol (Section 04).
"""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from exchange.federation.manifest import generate_capability_manifest
from exchange.federation.models import FederationPeer

router = APIRouter(prefix="/federation", tags=["Federation"])

_NONCE_WINDOW: dict[str, float] = {}
_NONCE_EXPIRY_SECONDS = 86400  # 24 hours
_CLOCK_SKEW_SECONDS = 300  # ±5 minutes


class PeerInitiator(BaseModel):
    did: str
    name: str
    operator: str = ""


class PeeringRequestBody(BaseModel):
    type: str = "PeeringRequest"
    initiator: PeerInitiator
    capability_manifest: dict
    trust_discount_policy: dict
    timestamp: str
    nonce: str
    proof: dict


class PeeringResponseBody(BaseModel):
    type: str = "PeeringResponse"
    status: str
    responder: dict
    capability_manifest: Optional[dict] = None
    trust_discount_policy: Optional[dict] = None
    peering_id: Optional[str] = None
    effective_from: Optional[str] = None
    rejection_reason: Optional[str] = None
    timestamp: str
    nonce: str
    request_nonce: str
    proof: dict = Field(default_factory=dict)


def _validate_timestamp(ts_str: str) -> None:
    """Reject timestamps outside the ±5-minute skew window."""
    try:
        ts = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid timestamp: {exc}")

    now = datetime.now(timezone.utc)
    skew = timedelta(seconds=_CLOCK_SKEW_SECONDS)
    if ts < now - skew or ts > now + skew:
        raise HTTPException(
            status_code=400,
            detail="timestamp_skew: request timestamp outside ±5 minute window",
        )


def _validate_nonce(nonce: str) -> None:
    """Reject previously seen nonces within the 24-hour window."""
    now = time.monotonic()

    expired = [n for n, t in _NONCE_WINDOW.items() if now - t > _NONCE_EXPIRY_SECONDS]
    for n in expired:
        del _NONCE_WINDOW[n]

    if nonce in _NONCE_WINDOW:
        raise HTTPException(
            status_code=400,
            detail="nonce_reused: this nonce was already used in a prior request",
        )
    _NONCE_WINDOW[nonce] = now


@router.post("/peer")
async def peering_handshake(body: PeeringRequestBody, request: Request):
    """Handle a federation peering request."""
    _validate_timestamp(body.timestamp)
    _validate_nonce(body.nonce)

    from exchange.config import settings

    db_factory = request.app.state.db
    async with db_factory() as session:
        from sqlalchemy import select

        existing = (
            await session.execute(
                select(FederationPeer).where(
                    FederationPeer.peer_did == body.initiator.did
                )
            )
        ).scalar_one_or_none()

        if existing and existing.status == "active":
            raise HTTPException(
                status_code=409,
                detail="peering_exists: active peering relationship already exists",
            )

        node_did = getattr(settings, "federation_node_did", "")
        base_url = getattr(settings, "base_url", "")
        manifest = generate_capability_manifest(node_did, base_url)

        peering_id = f"urn:uuid:{secrets.token_hex(16)}"
        now = datetime.now(timezone.utc)

        if existing:
            existing.status = "active"
            existing.name = body.initiator.name
            existing.operator = body.initiator.operator
            existing.capability_manifest = body.capability_manifest
            existing.trust_discount_policy = body.trust_discount_policy
            existing.peered_at = now
            existing.peering_id = peering_id
        else:
            peer = FederationPeer(
                peer_did=body.initiator.did,
                name=body.initiator.name,
                operator=body.initiator.operator,
                peering_id=peering_id,
                peered_at=now,
                status="active",
                capability_manifest=body.capability_manifest,
                trust_discount_policy=body.trust_discount_policy,
                current_rho=body.trust_discount_policy.get("initial_rho", 0.15),
            )
            session.add(peer)

        await session.commit()

    response_nonce = secrets.token_hex(16)

    return PeeringResponseBody(
        type="PeeringResponse",
        status="accepted",
        responder={
            "did": node_did,
            "name": getattr(settings, "exchange_name", "A2A Settlement Exchange"),
            "operator": getattr(settings, "exchange_operator", ""),
        },
        capability_manifest=manifest,
        trust_discount_policy={
            "algorithm_id": getattr(
                settings,
                "trust_discount_algorithm",
                "urn:a2a:trust:discount:linear-volume-weighted-v1",
            ),
            "initial_rho": getattr(settings, "trust_discount_initial_rho", 0.15),
            "parameters": getattr(settings, "trust_discount_params", {}),
        },
        peering_id=peering_id,
        effective_from=now.isoformat(),
        timestamp=now.isoformat(),
        nonce=response_nonce,
        request_nonce=body.nonce,
    )
