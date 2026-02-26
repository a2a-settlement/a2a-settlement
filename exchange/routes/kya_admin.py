"""KYA Administration Endpoints.

All endpoints require exchange operator authentication (``status == "operator"``).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from exchange.auth import authenticate_bot
from exchange.config import get_session, settings
from exchange.identity.crypto import canonicalize_json, generate_keypair, sign_ed25519
from exchange.identity.issuer_registry import IssuerRegistry, TrustedIssuer
from exchange.models import Account

router = APIRouter()
_registry = IssuerRegistry()


def _require_operator(current: dict = Depends(authenticate_bot)) -> dict:
    if current.get("status") != "operator":
        raise HTTPException(status_code=403, detail="Only the exchange operator can access KYA admin")
    return current


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TrustedIssuerCreate(BaseModel):
    did: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    issuer_type: str = Field(..., min_length=1)
    accepted_claims: list[str] = Field(default_factory=list)
    notes: str | None = None


class TrustedIssuerResponse(BaseModel):
    did: str
    name: str
    issuer_type: str
    accepted_claims: list[str]
    active: bool
    added_at: datetime | None = None
    added_by: str
    notes: str | None = None


class KYAStatusOverview(BaseModel):
    total_agents: int
    level_0: int
    level_1: int
    level_2: int
    expiring_within_7d: int
    expired: int


class IssueVCRequest(BaseModel):
    claim: str = "KYA-Level-2-Verified"
    validity_days: int = 180


class IssueVCResponse(BaseModel):
    credential: dict
    agent_id: str
    kya_level_verified: int


class RevokeRequest(BaseModel):
    reason: str


class RevokeResponse(BaseModel):
    agent_id: str
    previous_level: int
    new_level: int = 0
    reason: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/admin/kya/issuers", tags=["KYA Admin"])
def list_trusted_issuers(
    _op: dict = Depends(_require_operator),
    session: Session = Depends(get_session),
) -> list[TrustedIssuerResponse]:
    with session.begin():
        issuers = _registry.get_all_active(session)
    return [
        TrustedIssuerResponse(
            did=i.did,
            name=i.name,
            issuer_type=i.issuer_type,
            accepted_claims=i.accepted_claims,
            active=i.active,
            added_at=i.added_at,
            added_by=i.added_by,
            notes=i.notes,
        )
        for i in issuers
    ]


@router.post("/admin/kya/issuers", status_code=201, tags=["KYA Admin"])
def add_trusted_issuer(
    body: TrustedIssuerCreate,
    _op: dict = Depends(_require_operator),
    session: Session = Depends(get_session),
) -> TrustedIssuerResponse:
    with session.begin():
        existing = _registry.get_issuer(session, body.did)
        if existing is not None:
            raise HTTPException(status_code=409, detail="Issuer already exists")
        issuer = _registry.add_issuer(
            session,
            did=body.did,
            name=body.name,
            issuer_type=body.issuer_type,
            accepted_claims=body.accepted_claims,
            added_by=_op["id"],
            notes=body.notes,
        )
    return TrustedIssuerResponse(
        did=issuer.did,
        name=issuer.name,
        issuer_type=issuer.issuer_type,
        accepted_claims=issuer.accepted_claims,
        active=issuer.active,
        added_at=issuer.added_at,
        added_by=issuer.added_by,
        notes=issuer.notes,
    )


@router.delete("/admin/kya/issuers/{issuer_did}", tags=["KYA Admin"])
def deactivate_trusted_issuer(
    issuer_did: str,
    _op: dict = Depends(_require_operator),
    session: Session = Depends(get_session),
) -> dict:
    with session.begin():
        ok = _registry.deactivate_issuer(session, issuer_did)
    if not ok:
        raise HTTPException(status_code=404, detail="Issuer not found")
    return {"status": "deactivated", "did": issuer_did}


@router.get("/admin/kya/agents/status", tags=["KYA Admin"])
def get_kya_status_overview(
    _op: dict = Depends(_require_operator),
    session: Session = Depends(get_session),
) -> KYAStatusOverview:
    now = datetime.now(timezone.utc)
    warning_boundary = now + timedelta(days=7)
    with session.begin():
        total = session.execute(select(func.count()).select_from(Account)).scalar_one()
        l0 = session.execute(
            select(func.count()).select_from(Account).where(Account.kya_level_verified == 0)
        ).scalar_one()
        l1 = session.execute(
            select(func.count()).select_from(Account).where(Account.kya_level_verified == 1)
        ).scalar_one()
        l2 = session.execute(
            select(func.count()).select_from(Account).where(Account.kya_level_verified == 2)
        ).scalar_one()
        expiring = session.execute(
            select(func.count()).select_from(Account).where(
                Account.attestation_expires_at.isnot(None),
                Account.attestation_expires_at <= warning_boundary,
                Account.attestation_expires_at > now,
            )
        ).scalar_one()
        expired = session.execute(
            select(func.count()).select_from(Account).where(
                Account.attestation_expires_at.isnot(None),
                Account.attestation_expires_at <= now,
            )
        ).scalar_one()
    return KYAStatusOverview(
        total_agents=total,
        level_0=l0,
        level_1=l1,
        level_2=l2,
        expiring_within_7d=expiring,
        expired=expired,
    )


@router.post("/admin/kya/agents/{agent_id}/issue-vc", tags=["KYA Admin"])
def issue_exchange_vc(
    agent_id: str,
    body: IssueVCRequest,
    _op: dict = Depends(_require_operator),
    session: Session = Depends(get_session),
) -> IssueVCResponse:
    with session.begin():
        agent = session.execute(select(Account).where(Account.id == agent_id)).scalar_one_or_none()
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found")

    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=body.validity_days)

    operator_did = settings.kya_operator_did
    key_path = settings.kya_operator_private_key_path

    if not key_path:
        op_priv, op_pub = generate_keypair()
    else:
        try:
            with open(key_path, "rb") as f:
                op_priv = f.read(32)
        except FileNotFoundError:
            raise HTTPException(status_code=500, detail="Operator private key not found")

    credential: dict = {
        "type": "VerifiableCredential",
        "issuer": operator_did,
        "issuer_name": "A2A-SE Exchange",
        "claim": body.claim,
        "credential_subject": agent.did or agent.id,
        "valid_from": now.isoformat(),
        "expires_at": expires.isoformat(),
        "proof": {
            "type": "Ed25519Signature2020",
            "created": now.isoformat(),
            "verification_method": f"{operator_did}#key-1",
            "proof_value": "",
        },
    }

    payload = {k: v for k, v in credential.items() if k != "proof"}
    sig = sign_ed25519(canonicalize_json(payload), op_priv)
    credential["proof"]["proof_value"] = sig

    with session.begin():
        agent = session.execute(select(Account).where(Account.id == agent_id)).scalar_one()
        agent.kya_level_verified = 2
        agent.attestation_expires_at = expires
        agent.card_verified_at = now
        session.add(agent)

    return IssueVCResponse(
        credential=credential,
        agent_id=agent_id,
        kya_level_verified=2,
    )


@router.post("/admin/kya/agents/{agent_id}/revoke", tags=["KYA Admin"])
def revoke_agent_identity(
    agent_id: str,
    body: RevokeRequest,
    _op: dict = Depends(_require_operator),
    session: Session = Depends(get_session),
) -> RevokeResponse:
    with session.begin():
        agent = session.execute(select(Account).where(Account.id == agent_id)).scalar_one_or_none()
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found")
        previous = agent.kya_level_verified
        agent.kya_level_verified = 0
        agent.attestation_expires_at = None
        agent.card_verified_at = None
        session.add(agent)
    return RevokeResponse(
        agent_id=agent_id,
        previous_level=previous,
        reason=body.reason,
    )
