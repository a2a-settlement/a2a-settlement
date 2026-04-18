"""Federation escrow notification receiver — ``POST /federation/escrow/notify``.

Receives signed notifications from peer exchanges about escrow state transitions
in the Designated Escrow model (cross-exchange settlement).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from exchange.config import get_session, settings
from exchange.federation.models import FederationPeer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/federation", tags=["Federation"])

_VALID_EVENT_TYPES = {
    "escrow.created",
    "delivery.submitted",
    "escrow.released",
    "escrow.refunded",
    "dispute.filed",
}


class EscrowNotifyRequest(BaseModel):
    type: str
    source_exchange_did: str
    timestamp: str
    nonce: str
    escrow: dict
    extra: Optional[dict] = None


class EscrowNotifyResponse(BaseModel):
    status: str
    event_type: str
    received_at: str


def _verify_signature(secret: str, body: bytes, provided_sig: str) -> bool:
    """Verify HMAC-SHA256 signature."""
    if not secret or not provided_sig:
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, provided_sig)


@router.post("/escrow/notify")
async def receive_escrow_notification(
    request: Request,
    session: Session = Depends(get_session),
) -> EscrowNotifyResponse:
    """Receive a signed escrow state notification from a peer exchange."""
    raw_body = await request.body()

    try:
        body = json.loads(raw_body)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    event_type = body.get("type", "")
    source_did = body.get("source_exchange_did", "")

    if event_type not in _VALID_EVENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown event type: {event_type}",
        )

    if not source_did:
        raise HTTPException(status_code=400, detail="Missing source_exchange_did")

    with session.begin():
        peer = session.execute(
            select(FederationPeer).where(
                FederationPeer.peer_did == source_did,
                FederationPeer.status == "active",
            )
        ).scalar_one_or_none()

        if not peer:
            raise HTTPException(
                status_code=403,
                detail="Source exchange is not an active federation peer",
            )

    provided_sig = request.headers.get("X-A2ASE-Signature", "")
    signing_secret = getattr(settings, "federation_escrow_signing_secret", "")
    if signing_secret and provided_sig:
        if not _verify_signature(signing_secret, raw_body, provided_sig):
            raise HTTPException(status_code=403, detail="Invalid signature")
    elif signing_secret and not provided_sig:
        logger.warning(
            "Federation escrow notification from %s missing signature", source_did
        )

    escrow_data = body.get("escrow", {})
    escrow_id = escrow_data.get("id", "unknown")

    logger.info(
        "Federation escrow notification: %s for escrow %s from %s",
        event_type,
        escrow_id,
        source_did,
    )

    now = datetime.now(timezone.utc)
    return EscrowNotifyResponse(
        status="accepted",
        event_type=event_type,
        received_at=now.isoformat(),
    )
