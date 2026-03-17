from __future__ import annotations

import hashlib
import json as _json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from exchange.auth import authenticate_bot
from exchange.config import get_session, settings
from exchange.models import Attestation, Balance, Transaction
from exchange.ratelimit import limiter
from exchange.schemas import (
    AttestationCreate,
    AttestationListResponse,
    AttestationResponse,
    AttestationStatusResponse,
    RenewAttestationResponse,
    RevokeAttestationRequest,
    RevokeAttestationResponse,
)
from exchange.webhooks import fire_account_webhook_event

logger = logging.getLogger(__name__)

router = APIRouter()

_TTL_MAP = {
    "identity": lambda: timedelta(days=settings.attestation_ttl_identity_days),
    "reputation": lambda: timedelta(days=settings.attestation_ttl_reputation_days),
    "capability": lambda: timedelta(days=settings.attestation_ttl_capability_days),
    "transaction": lambda: None,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _lock(stmt):
    return stmt.with_for_update()


def _ttl_remaining(att: Attestation) -> float | None:
    if att.expires_at is None:
        return None
    remaining = (att.expires_at - _now()).total_seconds()
    return max(0.0, remaining)


def _to_response(att: Attestation) -> AttestationResponse:
    return AttestationResponse(
        id=att.id,
        account_id=att.account_id,
        attestation_type=att.attestation_type,
        status=att.status,
        issued_at=att.issued_at,
        expires_at=att.expires_at,
        revoked_at=att.revoked_at,
        revocation_reason=att.revocation_reason,
        parent_attestation_id=att.parent_attestation_id,
        payload_hash=att.payload_hash,
        ttl_remaining_seconds=_ttl_remaining(att),
    )


def _check_and_expire(att: Attestation, session: Session) -> None:
    """Transition an active attestation to expired if its TTL has elapsed."""
    if att.status == "active" and att.expires_at and att.expires_at < _now():
        att.status = "expired"
        session.add(att)


def _has_in_flight_escrows(session: Session, account_id: str) -> bool:
    """Check whether the account has any in-flight (held/evidence_pending) escrows."""
    from exchange.models import Escrow

    count = session.execute(
        select(Escrow.id).where(
            and_(
                Escrow.status.in_(["held", "evidence_pending"]),
                (Escrow.requester_id == account_id)
                | (Escrow.provider_id == account_id),
            )
        ).limit(1)
    ).scalar_one_or_none()
    return count is not None


# ── Issue ────────────────────────────────────────────────────────────────


@router.post(
    "/exchange/attestations",
    status_code=201,
    response_model=AttestationResponse,
    tags=["Attestations"],
)
@limiter.limit(settings.rate_limit_authenticated)
def issue_attestation(
    request: Request,
    req: AttestationCreate,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> AttestationResponse:
    now = _now()
    ttl_delta = _TTL_MAP.get(req.attestation_type.value, lambda: None)()
    expires_at = now + ttl_delta if ttl_delta else None

    payload = {
        "account_id": current["id"],
        "attestation_type": req.attestation_type.value,
        "issued_at": now.isoformat(),
    }
    canonical = _json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload_hash = hashlib.sha256(canonical).hexdigest()

    with session.begin():
        att = Attestation(
            account_id=current["id"],
            attestation_type=req.attestation_type.value,
            status="active",
            issued_at=now,
            expires_at=expires_at,
            payload_hash=payload_hash,
            metadata_json=req.metadata,
        )
        session.add(att)
        session.flush()

    return _to_response(att)


# ── Status (OCSP-style) ─────────────────────────────────────────────────


@router.get(
    "/exchange/attestations/{attestation_id}/status",
    response_model=AttestationStatusResponse,
    tags=["Attestations"],
)
@limiter.limit(settings.rate_limit_public)
def get_attestation_status(
    request: Request,
    attestation_id: str,
    session: Session = Depends(get_session),
) -> AttestationStatusResponse:
    """OCSP-style online status check for a single attestation."""
    with session.begin():
        att = session.execute(
            select(Attestation).where(Attestation.id == attestation_id)
        ).scalar_one_or_none()
        if att is None:
            raise HTTPException(status_code=404, detail="Attestation not found")

        _check_and_expire(att, session)

        in_flight = False
        if att.status == "expired" and att.attestation_type in ("reputation", "capability"):
            grace_end = att.expires_at + timedelta(hours=settings.attestation_grace_period_hours)
            if _now() < grace_end and _has_in_flight_escrows(session, att.account_id):
                in_flight = True

    return AttestationStatusResponse(
        id=att.id,
        status=att.status,
        attestation_type=att.attestation_type,
        issued_at=att.issued_at,
        expires_at=att.expires_at,
        ttl_remaining_seconds=_ttl_remaining(att),
        revoked_at=att.revoked_at,
        revocation_reason=att.revocation_reason,
        in_flight_grace=in_flight,
    )


# ── Revoke ───────────────────────────────────────────────────────────────


@router.post(
    "/exchange/attestations/{attestation_id}/revoke",
    response_model=RevokeAttestationResponse,
    tags=["Attestations"],
)
@limiter.limit(settings.rate_limit_authenticated)
def revoke_attestation(
    request: Request,
    attestation_id: str,
    req: RevokeAttestationRequest,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> RevokeAttestationResponse:
    now = _now()

    with session.begin():
        att = session.execute(
            _lock(select(Attestation).where(Attestation.id == attestation_id))
        ).scalar_one_or_none()
        if att is None:
            raise HTTPException(status_code=404, detail="Attestation not found")

        is_owner = att.account_id == current["id"]
        is_operator = current.get("status") == "operator"
        if not is_owner and not is_operator:
            raise HTTPException(
                status_code=403,
                detail="Only the attestation owner or operator can revoke",
            )

        if att.status in ("revoked", "renewed"):
            raise HTTPException(
                status_code=400,
                detail=f"Attestation is already {att.status}",
            )

        if att.attestation_type in ("identity", "capability") and not req.signatures:
            raise HTTPException(
                status_code=400,
                detail=f"Multi-sig signatures required for {att.attestation_type} revocation",
            )

        att.status = "revoked"
        att.revoked_at = now
        att.revocation_reason = req.reason.value
        session.add(att)

    fire_account_webhook_event(
        att.account_id,
        "attestation.revoked",
        {
            "attestation_id": att.id,
            "attestation_type": att.attestation_type,
            "reason": req.reason.value,
        },
    )

    return RevokeAttestationResponse(
        id=att.id,
        revoked_at=now,
        revocation_reason=req.reason.value,
    )


# ── Renew ────────────────────────────────────────────────────────────────


@router.post(
    "/exchange/attestations/{attestation_id}/renew",
    status_code=201,
    response_model=RenewAttestationResponse,
    tags=["Attestations"],
)
@limiter.limit(settings.rate_limit_authenticated)
def renew_attestation(
    request: Request,
    attestation_id: str,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> RenewAttestationResponse:
    now = _now()
    fee = settings.attestation_renewal_fee

    with session.begin():
        old = session.execute(
            _lock(select(Attestation).where(Attestation.id == attestation_id))
        ).scalar_one_or_none()
        if old is None:
            raise HTTPException(status_code=404, detail="Attestation not found")
        if old.account_id != current["id"]:
            raise HTTPException(status_code=403, detail="Only the owner can renew")
        if old.attestation_type == "transaction":
            raise HTTPException(
                status_code=400,
                detail="Transaction attestations are permanent and cannot be renewed",
            )
        if old.status == "renewed":
            raise HTTPException(status_code=400, detail="Attestation already renewed")

        bal = session.execute(
            _lock(select(Balance).where(Balance.account_id == current["id"]))
        ).scalar_one_or_none()
        if bal is None or bal.available < fee:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient balance for renewal fee ({fee} ATE)",
            )

        bal.available -= fee
        session.add(bal)

        session.add(
            Transaction(
                from_account=current["id"],
                to_account=None,
                amount=fee,
                tx_type="attestation_renewal_fee",
                description=f"Renewal fee for attestation {old.id}",
            )
        )

        ttl_delta = _TTL_MAP.get(old.attestation_type, lambda: None)()
        new_expires = now + ttl_delta if ttl_delta else None

        payload = {
            "account_id": current["id"],
            "attestation_type": old.attestation_type,
            "issued_at": now.isoformat(),
            "parent_attestation_id": old.id,
        }
        canonical = _json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        payload_hash = hashlib.sha256(canonical).hexdigest()

        new_att = Attestation(
            account_id=current["id"],
            attestation_type=old.attestation_type,
            status="active",
            issued_at=now,
            expires_at=new_expires,
            parent_attestation_id=old.id,
            payload_hash=payload_hash,
            metadata_json=old.metadata_json,
        )
        session.add(new_att)

        old.status = "renewed"
        session.add(old)

        session.flush()

    fire_account_webhook_event(
        new_att.account_id,
        "attestation.renewed",
        {
            "old_attestation_id": old.id,
            "new_attestation_id": new_att.id,
            "attestation_type": new_att.attestation_type,
            "fee_charged": fee,
        },
    )

    return RenewAttestationResponse(
        old_attestation_id=old.id,
        new_attestation=_to_response(new_att),
        fee_charged=fee,
    )


# ── List ─────────────────────────────────────────────────────────────────


@router.get(
    "/exchange/attestations",
    response_model=AttestationListResponse,
    tags=["Attestations"],
)
@limiter.limit(settings.rate_limit_authenticated)
def list_attestations(
    request: Request,
    account_id: str | None = None,
    attestation_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> AttestationListResponse:
    target = account_id or current["id"]
    with session.begin():
        stmt = select(Attestation).where(Attestation.account_id == target)
        if attestation_type:
            stmt = stmt.where(Attestation.attestation_type == attestation_type)
        if status:
            stmt = stmt.where(Attestation.status == status)

        from sqlalchemy import func as sa_func

        total = session.execute(
            select(sa_func.count()).select_from(stmt.subquery())
        ).scalar_one()

        rows = (
            session.execute(
                stmt.order_by(Attestation.issued_at.desc())
                .limit(limit)
                .offset(offset)
            )
            .scalars()
            .all()
        )

        for att in rows:
            _check_and_expire(att, session)

    return AttestationListResponse(
        attestations=[_to_response(att) for att in rows],
        total=total,
    )
