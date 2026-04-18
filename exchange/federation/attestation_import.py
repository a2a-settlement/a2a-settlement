"""Federation attestation import — ``/federation/attestation/import``.

Imports cross-exchange VCs, applies Trust Discount, and stores
effective reputation for the agent.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from exchange.config import get_session
from exchange.federation.models import FederatedAttestation, FederationPeer

router = APIRouter(prefix="/federation", tags=["Federation"])


class ImportRequest(BaseModel):
    agent_did: str
    attestations: list[dict]


class ImportResultItem(BaseModel):
    attestation_id: str
    status: str  # accepted, rejected, duplicate
    source_exchange: Optional[str] = None
    native_reputation: Optional[float] = None
    trust_discount_rho: Optional[float] = None
    effective_reputation: Optional[float] = None
    rejection_reason: Optional[str] = None


class ImportResponse(BaseModel):
    imported: int
    rejected: int = 0
    results: list[ImportResultItem]


@router.post("/attestation/import")
def import_attestations(
    body: ImportRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> ImportResponse:
    """Import cross-exchange attestation VCs."""
    results: list[ImportResultItem] = []
    imported = 0
    rejected = 0

    with session.begin():
        for vc_data in body.attestations:
            vc_id = vc_data.get("id", "")
            issuer_did = vc_data.get("issuer", "")
            vc_types = vc_data.get("type", [])
            subject = vc_data.get("credentialSubject", {})

            attestation_type = None
            for t in vc_types:
                if t != "VerifiableCredential":
                    attestation_type = t
                    break

            existing = session.execute(
                select(FederatedAttestation).where(
                    FederatedAttestation.vc_id == vc_id
                )
            ).scalar_one_or_none()

            if existing:
                results.append(
                    ImportResultItem(
                        attestation_id=vc_id,
                        status="duplicate",
                        source_exchange=issuer_did,
                    )
                )
                continue

            peer = session.execute(
                select(FederationPeer).where(
                    FederationPeer.peer_did == issuer_did,
                    FederationPeer.status == "active",
                )
            ).scalar_one_or_none()

            if not peer:
                results.append(
                    ImportResultItem(
                        attestation_id=vc_id,
                        status="rejected",
                        source_exchange=issuer_did,
                        rejection_reason="Issuer is not an active federation peer",
                    )
                )
                rejected += 1
                continue

            native_rep = subject.get("reputationScore")
            rho = peer.current_rho
            effective_rep = None
            if native_rep is not None:
                effective_rep = native_rep * rho

            valid_from = None
            valid_until = None
            try:
                vf = vc_data.get("validFrom")
                if vf:
                    valid_from = datetime.fromisoformat(vf).replace(
                        tzinfo=timezone.utc
                    )
                vu = vc_data.get("validUntil")
                if vu:
                    valid_until = datetime.fromisoformat(vu).replace(
                        tzinfo=timezone.utc
                    )
            except (ValueError, TypeError):
                pass

            attestation = FederatedAttestation(
                vc_id=vc_id,
                agent_did=body.agent_did,
                source_exchange_did=issuer_did,
                attestation_type=attestation_type or "Unknown",
                credential_data=vc_data,
                native_reputation=native_rep,
                trust_discount_rho=rho,
                effective_reputation=effective_rep,
                valid_from=valid_from,
                valid_until=valid_until,
            )
            session.add(attestation)
            imported += 1

            results.append(
                ImportResultItem(
                    attestation_id=vc_id,
                    status="accepted",
                    source_exchange=issuer_did,
                    native_reputation=native_rep,
                    trust_discount_rho=rho,
                    effective_reputation=effective_rep,
                )
            )

    return ImportResponse(
        imported=imported,
        rejected=rejected,
        results=results,
    )
