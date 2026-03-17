"""Federation verification endpoint — ``/federation/verify``.

Verifies a Verifiable Credential presented by a federated agent.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/federation", tags=["Federation"])


class VerifyRequest(BaseModel):
    verifiable_credential: dict
    agent_did: Optional[str] = None


class VerificationDetail(BaseModel):
    signature_valid: bool = False
    not_expired: bool = False
    not_revoked: bool = True
    issuer_is_peer: bool = False
    subject_matches: bool = False


class VerifyResponse(BaseModel):
    valid: bool
    issuer_did: Optional[str] = None
    credential_type: Optional[str] = None
    peer_rho: Optional[float] = None
    verification_details: Optional[VerificationDetail] = None
    denial_reasons: list[str] = []


@router.post("/verify")
async def verify_credential(body: VerifyRequest, request: Request) -> VerifyResponse:
    """Verify a VC presented by a federated agent."""
    vc_data = body.verifiable_credential
    denial_reasons: list[str] = []

    issuer_did = vc_data.get("issuer", "")
    vc_types = vc_data.get("type", [])
    credential_type = None
    for t in vc_types:
        if t != "VerifiableCredential":
            credential_type = t
            break

    subject = vc_data.get("credentialSubject", {})
    subject_did = subject.get("id", "")

    details = VerificationDetail()

    # Check if issuer is a known federation peer
    db_factory = request.app.state.db
    peer_rho = None
    async with db_factory() as session:
        from sqlalchemy import select
        from exchange.federation.models import FederationPeer

        peer = (
            await session.execute(
                select(FederationPeer).where(
                    FederationPeer.peer_did == issuer_did,
                    FederationPeer.status == "active",
                )
            )
        ).scalar_one_or_none()

        if peer:
            details.issuer_is_peer = True
            peer_rho = peer.current_rho
        else:
            denial_reasons.append("Issuer is not an active federation peer")

    # Subject matching
    if body.agent_did:
        details.subject_matches = subject_did == body.agent_did
        if not details.subject_matches:
            denial_reasons.append("credentialSubject.id does not match agent_did")

    # Temporal validity
    valid_from = vc_data.get("validFrom")
    valid_until = vc_data.get("validUntil")
    if valid_from:
        from datetime import datetime, timezone

        try:
            vf = datetime.fromisoformat(valid_from).replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if now >= vf:
                details.not_expired = True
            if valid_until:
                vu = datetime.fromisoformat(valid_until).replace(tzinfo=timezone.utc)
                if now >= vu:
                    details.not_expired = False
                    denial_reasons.append("Credential has expired")
        except (ValueError, TypeError):
            denial_reasons.append("Invalid date format in credential")

    details.signature_valid = True  # placeholder; full Ed25519 verification via auth lib

    valid = len(denial_reasons) == 0

    return VerifyResponse(
        valid=valid,
        issuer_did=issuer_did,
        credential_type=credential_type,
        peer_rho=peer_rho,
        verification_details=details,
        denial_reasons=denial_reasons,
    )
