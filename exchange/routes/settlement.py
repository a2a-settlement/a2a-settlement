from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import and_, func as sa_func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from exchange.auth import authenticate_bot
from exchange.config import get_session, settings
from exchange.ratelimit import limiter
from exchange.models import Account, Balance, Escrow, Transaction
from exchange.schemas import (
    BalanceResponse,
    BatchEscrowRequest,
    BatchEscrowResponse,
    DeliverRequest,
    DeliverResponse,
    DepositRequest,
    DepositResponse,
    DisputeRequest,
    DisputeResponse,
    EscrowDetailResponse,
    EscrowListResponse,
    EscrowRequest,
    EscrowResponse,
    PartialReleaseRequest,
    PartialReleaseResponse,
    RefundRequest,
    RefundResponse,
    ReleaseRequest,
    ReleaseResponse,
    ResolveRefundResponse,
    ResolveReleaseResponse,
    ResolveRequest,
    TransactionItem,
    TransactionsResponse,
    VIAttestation,
)
from exchange.compliance_log import log_settlement_event
from exchange.spending_guard import SpendingLimitGuard
from exchange.tasks import expire_stale_escrows as _expire_stale_escrows
from exchange.webhooks import fire_webhook_event

_spending_guard = SpendingLimitGuard(
    spending_window_hours=settings.spending_window_hours,
    hourly_velocity_limit=settings.hourly_velocity_limit,
    spending_freeze_minutes=settings.spending_freeze_minutes,
)


router = APIRouter()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fee_amount(amount: int) -> int:
    pct = Decimal(str(settings.fee_percent)) / Decimal("100")
    fee = (Decimal(amount) * pct).to_integral_value(rounding=ROUND_CEILING)
    return int(max(fee, settings.min_fee))


def _effective_fee_percent(amount: int, fee: int) -> float:
    if amount <= 0:
        return 0.0
    return float(
        (Decimal(fee) / Decimal(amount) * Decimal("100")).quantize(Decimal("0.0001"))
    )


def _escrow_detail(escrow: Escrow) -> EscrowDetailResponse:
    from exchange.schemas import Deliverable

    deliverables = None
    if escrow.deliverables:
        deliverables = [Deliverable(**d) for d in escrow.deliverables]

    return EscrowDetailResponse(
        id=escrow.id,
        requester_id=escrow.requester_id,
        provider_id=escrow.provider_id,
        amount=int(escrow.amount),
        fee_amount=int(escrow.fee_amount),
        effective_fee_percent=_effective_fee_percent(
            int(escrow.amount), int(escrow.fee_amount)
        ),
        status=escrow.status,
        dispute_reason=escrow.dispute_reason,
        resolution_strategy=escrow.resolution_strategy,
        expires_at=escrow.expires_at,
        task_id=escrow.task_id,
        task_type=escrow.task_type,
        group_id=escrow.group_id,
        depends_on=escrow.depends_on,
        deliverables=deliverables,
        required_attestation_level=escrow.required_attestation_level,
        delivered_content=escrow.delivered_content,
        provenance=escrow.provenance,
        provenance_result=escrow.provenance_result,
        delivered_at=escrow.delivered_at,
        released_amount=int(escrow.released_amount) if escrow.released_amount else None,
        released_fee=int(escrow.released_fee) if escrow.released_fee else None,
        holdback_amount=int(escrow.holdback_amount) if escrow.holdback_amount else None,
        holdback_fee=int(escrow.holdback_fee) if escrow.holdback_fee else None,
        score=escrow.score,
        efficacy_check_at=escrow.efficacy_check_at,
        efficacy_criteria=escrow.efficacy_criteria,
        vi_credential_chain=escrow.vi_credential_chain,
        created_at=escrow.created_at,
        resolved_at=escrow.resolved_at,
    )


def _lock(stmt):
    return stmt.with_for_update()


def _auto_refund_dependents(session: Session, upstream_escrow_id: str) -> None:
    """Auto-refund any held escrows that depend on the given (now-refunded) escrow."""
    dependents = (
        session.execute(
            select(Escrow).where(
                and_(Escrow.status == "held", Escrow.depends_on.isnot(None))
            )
        )
        .scalars()
        .all()
    )
    for dep in dependents:
        if dep.depends_on and upstream_escrow_id in dep.depends_on:
            dep_total = int(dep.amount + dep.fee_amount)
            bal = session.execute(
                _lock(select(Balance).where(Balance.account_id == dep.requester_id))
            ).scalar_one_or_none()
            if bal is None:
                continue
            bal.available += dep_total
            bal.held_in_escrow -= dep_total
            session.add(bal)
            dep.status = "refunded"
            dep.resolved_at = _now()
            session.add(dep)
            session.add(
                Transaction(
                    escrow_id=dep.id,
                    from_account=None,
                    to_account=dep.requester_id,
                    amount=dep_total,
                    tx_type="escrow_refund",
                    description=f"Auto-refunded: upstream escrow {upstream_escrow_id} was refunded",
                )
            )
            _auto_refund_dependents(session, dep.id)


def _verify_provenance(
    provenance_dict: dict | None, content: str, required_level: str | None
) -> dict | None:
    """Verify delivery provenance and return a result dict, or None if no provenance."""
    if not provenance_dict:
        return None

    import hashlib

    tier = provenance_dict.get("attestation_level", "self_declared")
    source_refs = provenance_dict.get("source_refs", [])
    signature = provenance_dict.get("signature")

    checks: list[str] = []
    verified = True

    tier_order = {"self_declared": 0, "signed": 1, "verifiable": 2}
    if required_level:
        provided = tier_order.get(tier, 0)
        required = tier_order.get(required_level, 0)
        if provided >= required:
            checks.append(f"attestation_level_met:{tier}>={required_level}")
        else:
            checks.append(f"attestation_level_insufficient:{tier}<{required_level}")
            verified = False

    if tier in ("signed", "verifiable") and not source_refs:
        checks.append("source_refs_missing")
        verified = False
    elif source_refs:
        checks.append(f"source_refs_present:{len(source_refs)}")

    for ref in source_refs:
        ref_hash = ref.get("content_hash")
        if ref_hash:
            actual = hashlib.sha256(content.encode()).hexdigest()
            if ref_hash == actual:
                checks.append("content_hash_match")
            else:
                checks.append("content_hash_mismatch")

    if tier in ("signed", "verifiable") and not signature:
        checks.append("signature_missing")
    elif signature:
        checks.append("signature_present")

    grounding = provenance_dict.get("grounding_metadata")
    if grounding:
        g_chunks = grounding.get("chunks") or []
        g_supports = grounding.get("supports") or []
        g_coverage = grounding.get("coverage")

        if g_chunks:
            checks.append(f"grounding_chunks_present:{len(g_chunks)}")
        else:
            checks.append("grounding_chunks_empty")

        valid_indices = True
        for sup in g_supports:
            for idx in sup.get("chunk_indices", []):
                if idx < 0 or idx >= len(g_chunks):
                    valid_indices = False
                    break
        checks.append(f"grounding_supports_valid:{valid_indices}")

        if g_coverage is not None:
            checks.append(f"grounding_coverage:{g_coverage:.2f}")
            if g_coverage > 0.5:
                checks.append("grounding_sufficient")
            else:
                checks.append("grounding_insufficient")
        elif g_supports and content:
            text_len = len(content)
            covered = bytearray(text_len)
            for sup in g_supports:
                seg = sup.get("segment", {})
                s = max(0, min(seg.get("start_index", 0), text_len))
                e = max(s, min(seg.get("end_index", 0), text_len))
                for i in range(s, e):
                    covered[i] = 1
            computed = sum(covered) / text_len if text_len else 0.0
            checks.append(f"grounding_coverage:{computed:.2f}")
            if computed > 0.5:
                checks.append("grounding_sufficient")
            else:
                checks.append("grounding_insufficient")

        domains = {_extract_domain(c.get("uri", "")) for c in g_chunks if c.get("uri")}
        if len(domains) >= 2:
            checks.append(f"grounding_source_diversity:{len(domains)}")
        elif domains:
            checks.append("grounding_single_source")

    return {
        "verified": verified,
        "tier": tier,
        "checks": checks,
        "recommendation": "approve" if verified else "review",
    }


def _extract_domain(uri: str) -> str:
    """Extract the domain from a URI for source diversity checks."""
    try:
        from urllib.parse import urlparse

        return urlparse(uri).netloc
    except Exception:
        return uri


def _apply_provenance_reputation_penalty(
    provider: Account, provenance_result: dict | None
) -> None:
    """Apply additional reputation adjustments based on provenance verification outcome."""
    if not provenance_result:
        return

    verified = provenance_result.get("verified", True)
    tier = provenance_result.get("tier", "self_declared")
    recommendation = provenance_result.get("recommendation", "approve")

    if verified and recommendation == "approve":
        # Bonus for voluntarily providing higher-tier attestation
        if tier in ("signed", "verifiable"):
            provider.reputation = min(1.0, float(provider.reputation) + 0.02)
        return

    if not verified or recommendation == "reject":
        if tier == "self_declared":
            provider.reputation = max(0.0, float(provider.reputation) * 0.9)
        elif tier == "signed":
            provider.reputation = max(0.0, float(provider.reputation) * 0.85)
        elif tier == "verifiable":
            provider.reputation = max(0.0, float(provider.reputation) * 0.7)


def _check_spending_limits(session: Session, account_id: str, new_hold: int) -> None:
    """Enforce rolling-window spending limits and hourly velocity via the guard."""
    _spending_guard.check(session, account_id, new_hold)


def _check_kya_gate(
    session: Session,
    requester_id: str,
    provider_id: str,
    amount: int,
) -> dict:
    """Check if both agents meet KYA requirements for the transaction amount.

    Returns a dict with gate decision and metadata.  When ``kya_enabled`` is
    ``False`` (default), always allows the transaction.
    """
    if not settings.kya_enabled:
        return {
            "allowed": True,
            "required_level": 0,
            "requester_level": 0,
            "provider_level": 0,
            "hitl_required": False,
            "requester_did": None,
            "provider_did": None,
            "rejection_reason": None,
        }

    requester = session.execute(
        select(Account).where(Account.id == requester_id)
    ).scalar_one_or_none()
    provider = session.execute(
        select(Account).where(Account.id == provider_id)
    ).scalar_one_or_none()

    req_level = requester.kya_level_verified if requester else 0
    prov_level = provider.kya_level_verified if provider else 0
    min_level = min(req_level, prov_level)

    if amount > settings.kya_escrow_tier2_max:
        required = 2
    elif amount > settings.kya_escrow_tier1_max:
        required = 1
    else:
        required = 0

    hitl = amount >= settings.kya_hitl_threshold and required >= 2
    allowed = min_level >= required

    return {
        "allowed": allowed,
        "required_level": required,
        "requester_level": req_level,
        "provider_level": prov_level,
        "hitl_required": hitl,
        "requester_did": getattr(requester, "did", None),
        "provider_did": getattr(provider, "did", None),
        "rejection_reason": (
            f"KYA level {required} required (amount={amount}), "
            f"but requester={req_level}, provider={prov_level}"
            if not allowed
            else None
        ),
    }


@router.post(
    "/exchange/deposit",
    status_code=201,
    response_model=DepositResponse,
    tags=["Settlement"],
)
@limiter.limit(settings.rate_limit_authenticated)
def deposit(
    request: Request,
    req: DepositRequest,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> DepositResponse:
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Deposit amount must be positive")

    with session.begin():
        bal = session.execute(
            _lock(select(Balance).where(Balance.account_id == current["id"]))
        ).scalar_one_or_none()
        if bal is None:
            raise HTTPException(status_code=404, detail="Account not found")

        bal.available += req.amount
        session.add(bal)

        deposit_id = str(uuid.uuid4())

        session.add(
            Transaction(
                escrow_id=None,
                from_account=None,
                to_account=current["id"],
                amount=req.amount,
                tx_type="deposit",
                description=f"Deposit: {req.reference or 'direct'}",
            )
        )

    return DepositResponse(
        deposit_id=deposit_id,
        account_id=current["id"],
        amount=req.amount,
        currency=req.currency,
        new_balance=int(bal.available),
        reference=req.reference,
    )


@router.post(
    "/exchange/escrow",
    status_code=201,
    response_model=EscrowResponse,
    tags=["Settlement"],
)
@limiter.limit(settings.rate_limit_authenticated)
def create_escrow(
    request: Request,
    req: EscrowRequest,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> EscrowResponse:
    if req.amount < settings.min_escrow or req.amount > settings.max_escrow:
        raise HTTPException(
            status_code=400,
            detail=f"Amount must be between {settings.min_escrow} and {settings.max_escrow}",
        )
    if current["id"] == req.provider_id:
        raise HTTPException(status_code=400, detail="Cannot escrow to yourself")

    fee_amount = _fee_amount(req.amount)
    total_hold = req.amount + fee_amount
    ttl = req.ttl_minutes or settings.default_ttl_minutes
    expires_at = _now() + timedelta(minutes=ttl)

    with session.begin():
        _expire_stale_escrows(session)

        bal = session.execute(
            _lock(select(Balance).where(Balance.account_id == current["id"]))
        ).scalar_one_or_none()
        if bal is None:
            raise HTTPException(status_code=404, detail="Requester account not found")
        if bal.available < total_hold:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient balance. Need {total_hold} ({req.amount} + {fee_amount} fee), have {bal.available}",
            )

        _check_spending_limits(session, current["id"], total_hold)

        provider = session.execute(
            select(Account).where(Account.id == req.provider_id)
        ).scalar_one_or_none()
        if provider is None:
            raise HTTPException(status_code=404, detail="Provider account not found")
        if provider.status != "active":
            raise HTTPException(
                status_code=400, detail="Provider account is not active"
            )

        kya_gate = _check_kya_gate(session, current["id"], req.provider_id, req.amount)
        if not kya_gate["allowed"]:
            raise HTTPException(status_code=403, detail=kya_gate["rejection_reason"])

        bal.available -= total_hold
        bal.held_in_escrow += total_hold
        session.add(bal)

        if req.depends_on:
            deps = (
                session.execute(
                    select(Escrow).where(
                        and_(
                            Escrow.id.in_(req.depends_on),
                            Escrow.requester_id == current["id"],
                        )
                    )
                )
                .scalars()
                .all()
            )
            if len(deps) != len(req.depends_on):
                raise HTTPException(
                    status_code=400,
                    detail="One or more depends_on escrow IDs not found or not owned by requester",
                )

        deliverables_json = (
            [d.model_dump() for d in req.deliverables] if req.deliverables else None
        )

        if req.task_id:
            existing = session.execute(
                select(Escrow).where(
                    and_(
                        Escrow.requester_id == current["id"],
                        Escrow.provider_id == req.provider_id,
                        Escrow.task_id == req.task_id,
                        Escrow.status == "held",
                    )
                )
            ).scalar_one_or_none()
            if existing:
                raise HTTPException(
                    status_code=409,
                    detail=f"An active escrow already exists for this task_id (escrow_id={existing.id})",
                )

        vi_chain_json = (
            req.vi_credential_chain.model_dump(mode="json")
            if req.vi_credential_chain
            else None
        )

        escrow = Escrow(
            requester_id=current["id"],
            provider_id=req.provider_id,
            amount=req.amount,
            fee_amount=fee_amount,
            task_id=req.task_id,
            task_type=req.task_type,
            group_id=req.group_id,
            depends_on=req.depends_on,
            deliverables=deliverables_json,
            required_attestation_level=req.required_attestation_level,
            vi_credential_chain=vi_chain_json,
            status="held",
            expires_at=expires_at,
            requester_did=kya_gate["requester_did"],
            provider_did=kya_gate["provider_did"],
            kya_level_at_creation=kya_gate["required_level"],
            hitl_required=kya_gate["hitl_required"],
        )
        session.add(escrow)
        session.flush()

        session.add(
            Transaction(
                escrow_id=escrow.id,
                from_account=current["id"],
                to_account=None,
                amount=total_hold,
                tx_type="escrow_hold",
                description=f"Escrow for task: {req.task_type or req.task_id or 'unspecified'}",
            )
        )

    fire_webhook_event(session, escrow, "escrow.created")
    log_settlement_event(
        escrow_id=escrow.id,
        event_type="escrow.created",
        requester_id=current["id"],
        provider_id=req.provider_id,
        amount=req.amount,
        status="held",
    )

    return EscrowResponse(
        escrow_id=escrow.id,
        requester_id=current["id"],
        provider_id=req.provider_id,
        amount=int(req.amount),
        fee_amount=int(fee_amount),
        effective_fee_percent=_effective_fee_percent(req.amount, fee_amount),
        total_held=int(total_hold),
        status=escrow.status,
        expires_at=escrow.expires_at,
        group_id=escrow.group_id,
    )


@router.post(
    "/exchange/escrow/{escrow_id}/deliver",
    status_code=200,
    response_model=DeliverResponse,
    tags=["Settlement"],
)
def deliver(
    escrow_id: str,
    req: DeliverRequest,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> DeliverResponse:
    with session.begin():
        escrow = session.execute(
            _lock(select(Escrow).where(Escrow.id == escrow_id))
        ).scalar_one_or_none()
        if escrow is None:
            raise HTTPException(status_code=404, detail="Escrow not found")
        if escrow.provider_id != current["id"]:
            raise HTTPException(
                status_code=403,
                detail="Only the provider can deliver against an escrow",
            )
        if escrow.status != "held":
            raise HTTPException(
                status_code=400,
                detail=f"Escrow cannot accept delivery (status: {escrow.status})",
            )

        if req.provenance and escrow.required_attestation_level:
            tier_order = {"self_declared": 0, "signed": 1, "verifiable": 2}
            provided = tier_order.get(req.provenance.attestation_level, 0)
            required = tier_order.get(escrow.required_attestation_level, 0)
            if provided < required:
                raise HTTPException(
                    status_code=400,
                    detail=f"Attestation level '{req.provenance.attestation_level}' does not meet required '{escrow.required_attestation_level}'",
                )

        now = _now()
        escrow.delivered_content = req.content
        prov_dict = req.provenance.model_dump(mode="json") if req.provenance else None
        escrow.provenance = prov_dict
        escrow.provenance_result = _verify_provenance(
            prov_dict, req.content, escrow.required_attestation_level
        )
        escrow.delivered_at = now
        session.add(escrow)

    grounding_chain = None
    if prov_dict and prov_dict.get("grounding_metadata"):
        gm = prov_dict["grounding_metadata"]
        grounding_chain = {
            "search_queries": gm.get("search_queries", []),
            "chunk_count": len(gm.get("chunks", [])),
            "coverage": gm.get("coverage"),
            "source_uris": [c.get("uri") for c in gm.get("chunks", []) if c.get("uri")],
        }

    fire_webhook_event(session, escrow, "escrow.delivered")
    log_settlement_event(
        escrow_id=escrow.id,
        event_type="escrow.delivered",
        requester_id=escrow.requester_id,
        provider_id=escrow.provider_id,
        amount=int(escrow.amount),
        status=escrow.status,
        grounding_chain=grounding_chain,
    )

    return DeliverResponse(
        escrow_id=escrow.id,
        status=escrow.status,
        delivered_at=now,
    )


@router.post(
    "/exchange/escrow/{escrow_id}/partial-release",
    status_code=200,
    response_model=PartialReleaseResponse,
    tags=["Settlement"],
)
@limiter.limit(settings.rate_limit_authenticated)
def partial_release(
    request: Request,
    escrow_id: str,
    req: PartialReleaseRequest,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> PartialReleaseResponse:
    with session.begin():
        _expire_stale_escrows(session)

        escrow = session.execute(
            _lock(select(Escrow).where(Escrow.id == escrow_id))
        ).scalar_one_or_none()
        if escrow is None:
            raise HTTPException(status_code=404, detail="Escrow not found")
        if escrow.requester_id != current["id"]:
            raise HTTPException(
                status_code=403, detail="Only the requester can partially release"
            )
        if escrow.status != "held":
            raise HTTPException(
                status_code=400,
                detail=f"Escrow cannot be partially released (status: {escrow.status})",
            )

        pct = req.release_percent
        release_amount = int(escrow.amount) * pct // 100
        release_fee = int(escrow.fee_amount) * pct // 100
        holdback_amount = int(escrow.amount) - release_amount
        holdback_fee = int(escrow.fee_amount) - release_fee
        release_total = release_amount + release_fee

        requester_bal = session.execute(
            _lock(select(Balance).where(Balance.account_id == escrow.requester_id))
        ).scalar_one_or_none()
        provider_bal = session.execute(
            _lock(select(Balance).where(Balance.account_id == escrow.provider_id))
        ).scalar_one_or_none()
        if requester_bal is None or provider_bal is None:
            raise HTTPException(status_code=404, detail="Balance not found")

        requester_bal.held_in_escrow -= release_total
        requester_bal.total_spent += release_total
        provider_bal.available += release_amount
        provider_bal.total_earned += release_amount
        session.add(requester_bal)
        session.add(provider_bal)

        escrow.released_amount = release_amount
        escrow.released_fee = release_fee
        escrow.holdback_amount = holdback_amount
        escrow.holdback_fee = holdback_fee
        escrow.score = req.score

        session.add(
            Transaction(
                escrow_id=escrow.id,
                from_account=escrow.requester_id,
                to_account=escrow.provider_id,
                amount=release_amount,
                tx_type="escrow_partial_release",
                description=f"Partial release ({pct}%) - score {req.score or 'n/a'}",
            )
        )
        if release_fee > 0:
            session.add(
                Transaction(
                    escrow_id=escrow.id,
                    from_account=escrow.requester_id,
                    to_account=None,
                    amount=release_fee,
                    tx_type="fee",
                    description=f"Partial release fee ({pct}%)",
                )
            )

        if req.efficacy_check_at:
            escrow.status = "partially_released"
            escrow.efficacy_check_at = req.efficacy_check_at
            escrow.efficacy_criteria = req.efficacy_criteria
        else:
            holdback_total = holdback_amount + holdback_fee
            requester_bal.available += holdback_total
            requester_bal.held_in_escrow -= holdback_total
            session.add(requester_bal)

            session.add(
                Transaction(
                    escrow_id=escrow.id,
                    from_account=None,
                    to_account=escrow.requester_id,
                    amount=holdback_total,
                    tx_type="escrow_holdback_refund",
                    description=f"Holdback refunded ({100 - pct}%) - no efficacy review",
                )
            )

            escrow.status = "released"
            escrow.resolved_at = _now()

        session.add(escrow)

        provider = session.execute(
            select(Account).where(Account.id == escrow.provider_id)
        ).scalar_one_or_none()
        if provider is not None:
            boost = (pct / 100.0) * 0.1
            provider.reputation = min(
                1.0, float(provider.reputation) * (1.0 - boost) + 1.0 * boost
            )
            session.add(provider)

    fire_webhook_event(session, escrow, "escrow.partial_release")
    log_settlement_event(
        escrow_id=escrow.id,
        event_type="escrow.partial_release",
        requester_id=escrow.requester_id,
        provider_id=escrow.provider_id,
        amount=release_amount,
        status=escrow.status,
    )

    return PartialReleaseResponse(
        escrow_id=escrow.id,
        status=escrow.status,
        released_amount=release_amount,
        fee_collected=release_fee,
        holdback_amount=holdback_amount if escrow.status == "partially_released" else 0,
        holdback_fee=holdback_fee if escrow.status == "partially_released" else 0,
        provider_id=escrow.provider_id,
        efficacy_check_at=escrow.efficacy_check_at,
    )


@router.post("/exchange/release", response_model=ReleaseResponse, tags=["Settlement"])
@limiter.limit(settings.rate_limit_authenticated)
def release(
    request: Request,
    req: ReleaseRequest,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> ReleaseResponse:
    with session.begin():
        _expire_stale_escrows(session)

        escrow = session.execute(
            _lock(select(Escrow).where(Escrow.id == req.escrow_id))
        ).scalar_one_or_none()
        if escrow is None:
            raise HTTPException(status_code=404, detail="Escrow not found")
        if escrow.requester_id != current["id"]:
            raise HTTPException(
                status_code=403, detail="Only the requester can release an escrow"
            )
        if escrow.status not in ("held", "partially_released"):
            raise HTTPException(
                status_code=400, detail=f"Escrow is already {escrow.status}"
            )

        is_holdback = escrow.status == "partially_released"

        if not is_holdback and escrow.depends_on:
            unresolved = (
                session.execute(
                    select(Escrow).where(
                        and_(
                            Escrow.id.in_(escrow.depends_on),
                            Escrow.status != "released",
                        )
                    )
                )
                .scalars()
                .all()
            )
            if unresolved:
                ids = [e.id for e in unresolved]
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot release: upstream escrows not yet released: {ids}",
                )

        if is_holdback:
            pay_amount = int(escrow.holdback_amount or 0)
            pay_fee = int(escrow.holdback_fee or 0)
        else:
            pay_amount = int(escrow.amount)
            pay_fee = int(escrow.fee_amount)
        total_held = pay_amount + pay_fee

        requester_bal = session.execute(
            _lock(select(Balance).where(Balance.account_id == escrow.requester_id))
        ).scalar_one_or_none()
        provider_bal = session.execute(
            _lock(select(Balance).where(Balance.account_id == escrow.provider_id))
        ).scalar_one_or_none()
        if requester_bal is None or provider_bal is None:
            raise HTTPException(status_code=404, detail="Balance not found")

        requester_bal.held_in_escrow -= total_held
        requester_bal.total_spent += total_held
        session.add(requester_bal)

        provider_bal.available += pay_amount
        provider_bal.total_earned += pay_amount
        session.add(provider_bal)

        escrow.status = "released"
        escrow.resolved_at = _now()
        if is_holdback:
            escrow.holdback_amount = 0
            escrow.holdback_fee = 0
        session.add(escrow)

        tx_type = "escrow_holdback_release" if is_holdback else "escrow_release"
        tx_desc = (
            "Holdback released - efficacy approved"
            if is_holdback
            else "Task completed - payment released"
        )

        session.add(
            Transaction(
                escrow_id=escrow.id,
                from_account=escrow.requester_id,
                to_account=escrow.provider_id,
                amount=pay_amount,
                tx_type=tx_type,
                description=tx_desc,
            )
        )
        if pay_fee > 0:
            session.add(
                Transaction(
                    escrow_id=escrow.id,
                    from_account=escrow.requester_id,
                    to_account=None,
                    amount=pay_fee,
                    tx_type="fee",
                    description="Platform transaction fee"
                    + (" (holdback)" if is_holdback else ""),
                )
            )

        provider = session.execute(
            select(Account).where(Account.id == escrow.provider_id)
        ).scalar_one_or_none()
        if provider is not None:
            provider.reputation = min(1.0, float(provider.reputation) * 0.9 + 1.0 * 0.1)
            session.add(provider)

    fire_webhook_event(session, escrow, "escrow.released")
    log_settlement_event(
        escrow_id=req.escrow_id,
        event_type="escrow.released",
        requester_id=escrow.requester_id,
        provider_id=escrow.provider_id,
        amount=pay_amount,
        status="released",
    )

    return ReleaseResponse(
        escrow_id=req.escrow_id,
        status="released",
        amount_paid=pay_amount,
        fee_collected=pay_fee,
        provider_id=escrow.provider_id,
    )


@router.post("/exchange/refund", response_model=RefundResponse, tags=["Settlement"])
@limiter.limit(settings.rate_limit_authenticated)
def refund(
    request: Request,
    req: RefundRequest,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> RefundResponse:
    with session.begin():
        _expire_stale_escrows(session)

        escrow = session.execute(
            _lock(select(Escrow).where(Escrow.id == req.escrow_id))
        ).scalar_one_or_none()
        if escrow is None:
            raise HTTPException(status_code=404, detail="Escrow not found")
        if escrow.requester_id != current["id"]:
            raise HTTPException(
                status_code=403, detail="Only the requester can refund an escrow"
            )
        if escrow.status not in ("held", "partially_released"):
            raise HTTPException(
                status_code=400, detail=f"Escrow is already {escrow.status}"
            )

        is_holdback = escrow.status == "partially_released"

        if is_holdback:
            refund_total = int(escrow.holdback_amount or 0) + int(
                escrow.holdback_fee or 0
            )
        else:
            refund_total = int(escrow.amount + escrow.fee_amount)

        requester_bal = session.execute(
            _lock(select(Balance).where(Balance.account_id == escrow.requester_id))
        ).scalar_one_or_none()
        if requester_bal is None:
            raise HTTPException(status_code=404, detail="Requester balance not found")

        requester_bal.available += refund_total
        requester_bal.held_in_escrow -= refund_total
        session.add(requester_bal)

        if is_holdback:
            escrow.status = "released"
            escrow.holdback_amount = 0
            escrow.holdback_fee = 0
        else:
            escrow.status = "refunded"
        escrow.resolved_at = _now()
        session.add(escrow)

        tx_type = "escrow_holdback_refund" if is_holdback else "escrow_refund"
        tx_desc = req.reason or (
            "Holdback refunded - efficacy not met"
            if is_holdback
            else "Task failed or cancelled"
        )

        session.add(
            Transaction(
                escrow_id=escrow.id,
                from_account=None,
                to_account=escrow.requester_id,
                amount=refund_total,
                tx_type=tx_type,
                description=tx_desc,
            )
        )

        provider = session.execute(
            select(Account).where(Account.id == escrow.provider_id)
        ).scalar_one_or_none()
        if provider is not None and escrow.delivered_at is not None:
            provider.reputation = max(0.0, float(provider.reputation) * 0.9 + 0.0 * 0.1)
            session.add(provider)

        if not is_holdback:
            _auto_refund_dependents(session, escrow.id)

    event_type = "escrow.released" if is_holdback else "escrow.refunded"
    fire_webhook_event(session, escrow, event_type)
    log_settlement_event(
        escrow_id=req.escrow_id,
        event_type=event_type,
        requester_id=escrow.requester_id,
        provider_id=escrow.provider_id,
        amount=refund_total,
        status=escrow.status,
    )

    return RefundResponse(
        escrow_id=req.escrow_id,
        status=escrow.status,
        amount_returned=refund_total,
        requester_id=escrow.requester_id,
    )


@router.post("/exchange/dispute", response_model=DisputeResponse, tags=["Settlement"])
@limiter.limit(settings.rate_limit_authenticated)
def dispute(
    request: Request,
    req: DisputeRequest,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> DisputeResponse:
    with session.begin():
        escrow = session.execute(
            _lock(select(Escrow).where(Escrow.id == req.escrow_id))
        ).scalar_one_or_none()
        if escrow is None:
            raise HTTPException(status_code=404, detail="Escrow not found")
        if current["id"] not in (escrow.requester_id, escrow.provider_id):
            raise HTTPException(
                status_code=403,
                detail="Only the requester or provider can dispute an escrow",
            )
        if escrow.status != "held":
            raise HTTPException(
                status_code=400,
                detail=f"Escrow cannot be disputed (status: {escrow.status})",
            )

        escrow.status = "disputed"
        escrow.dispute_reason = req.reason
        escrow.dispute_expires_at = _now() + timedelta(
            minutes=settings.dispute_ttl_minutes
        )
        session.add(escrow)

    fire_webhook_event(session, escrow, "escrow.disputed")
    fire_webhook_event(session, escrow, "escrow.dispute_pending_mediation")
    log_settlement_event(
        escrow_id=req.escrow_id,
        event_type="escrow.disputed",
        requester_id=escrow.requester_id,
        provider_id=escrow.provider_id,
        amount=int(escrow.amount),
        status="disputed",
        dispute_reason=req.reason,
    )

    return DisputeResponse(
        escrow_id=req.escrow_id,
        status="disputed",
        reason=req.reason,
    )


@router.post("/exchange/resolve", tags=["Settlement"])
@limiter.limit(settings.rate_limit_authenticated)
def resolve(
    request: Request,
    req: ResolveRequest,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> ResolveReleaseResponse | ResolveRefundResponse:
    if req.resolution not in ("release", "refund"):
        raise HTTPException(
            status_code=400, detail="resolution must be 'release' or 'refund'"
        )

    if current.get("status") != "operator":
        raise HTTPException(
            status_code=403, detail="Only the exchange operator can resolve disputes"
        )

    with session.begin():
        escrow = session.execute(
            _lock(select(Escrow).where(Escrow.id == req.escrow_id))
        ).scalar_one_or_none()
        if escrow is None:
            raise HTTPException(status_code=404, detail="Escrow not found")
        if escrow.status != "disputed":
            raise HTTPException(
                status_code=400,
                detail=f"Escrow is not disputed (status: {escrow.status})",
            )

        escrow.resolution_strategy = req.strategy
        if req.provenance_result:
            escrow.provenance_result = req.provenance_result
        total_held = int(escrow.amount + escrow.fee_amount)

        if req.resolution == "release":
            requester_bal = session.execute(
                _lock(select(Balance).where(Balance.account_id == escrow.requester_id))
            ).scalar_one_or_none()
            provider_bal = session.execute(
                _lock(select(Balance).where(Balance.account_id == escrow.provider_id))
            ).scalar_one_or_none()
            if requester_bal is None or provider_bal is None:
                raise HTTPException(status_code=404, detail="Balance not found")

            requester_bal.held_in_escrow -= total_held
            requester_bal.total_spent += total_held
            session.add(requester_bal)

            provider_bal.available += int(escrow.amount)
            provider_bal.total_earned += int(escrow.amount)
            session.add(provider_bal)

            escrow.status = "released"
            escrow.resolved_at = _now()
            session.add(escrow)

            session.add(
                Transaction(
                    escrow_id=escrow.id,
                    from_account=escrow.requester_id,
                    to_account=escrow.provider_id,
                    amount=int(escrow.amount),
                    tx_type="escrow_release",
                    description="Dispute resolved - payment released",
                )
            )
            if escrow.fee_amount > 0:
                session.add(
                    Transaction(
                        escrow_id=escrow.id,
                        from_account=escrow.requester_id,
                        to_account=None,
                        amount=int(escrow.fee_amount),
                        tx_type="fee",
                        description="Platform transaction fee (dispute resolved)",
                    )
                )

            provider = session.execute(
                select(Account).where(Account.id == escrow.provider_id)
            ).scalar_one_or_none()
            if provider is not None:
                provider.reputation = min(
                    1.0, float(provider.reputation) * 0.9 + 1.0 * 0.1
                )
                _apply_provenance_reputation_penalty(provider, req.provenance_result)
                session.add(provider)

        else:
            requester_bal = session.execute(
                _lock(select(Balance).where(Balance.account_id == escrow.requester_id))
            ).scalar_one_or_none()
            if requester_bal is None:
                raise HTTPException(
                    status_code=404, detail="Requester balance not found"
                )

            requester_bal.available += total_held
            requester_bal.held_in_escrow -= total_held
            session.add(requester_bal)

            escrow.status = "refunded"
            escrow.resolved_at = _now()
            session.add(escrow)

            session.add(
                Transaction(
                    escrow_id=escrow.id,
                    from_account=None,
                    to_account=escrow.requester_id,
                    amount=total_held,
                    tx_type="escrow_refund",
                    description="Dispute resolved - tokens refunded",
                )
            )

            provider = session.execute(
                select(Account).where(Account.id == escrow.provider_id)
            ).scalar_one_or_none()
            if provider is not None:
                provider.reputation = max(
                    0.0, float(provider.reputation) * 0.9 + 0.0 * 0.1
                )
                _apply_provenance_reputation_penalty(provider, req.provenance_result)
                session.add(provider)

    fire_webhook_event(session, escrow, "escrow.resolved")
    log_settlement_event(
        escrow_id=req.escrow_id,
        event_type="escrow.resolved",
        requester_id=escrow.requester_id,
        provider_id=escrow.provider_id,
        amount=int(escrow.amount),
        status=escrow.status,
        resolution_strategy=req.strategy,
    )

    if req.resolution == "release":
        return ResolveReleaseResponse(
            escrow_id=req.escrow_id,
            amount_paid=int(escrow.amount),
            fee_collected=int(escrow.fee_amount),
            provider_id=escrow.provider_id,
        )
    return ResolveRefundResponse(
        escrow_id=req.escrow_id,
        amount_returned=total_held,
        requester_id=escrow.requester_id,
    )


@router.get("/exchange/balance", response_model=BalanceResponse, tags=["Settlement"])
@limiter.limit(settings.rate_limit_authenticated)
def balance(
    request: Request,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> BalanceResponse:
    with session.begin():
        row = session.execute(
            select(Balance, Account)
            .join(Account, Account.id == Balance.account_id)
            .where(Balance.account_id == current["id"])
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Account not found")
        bal, acct = row
        return BalanceResponse(
            account_id=acct.id,
            bot_name=acct.bot_name,
            reputation=float(acct.reputation),
            account_status=acct.status,
            available=int(bal.available),
            held_in_escrow=int(bal.held_in_escrow),
            total_earned=int(bal.total_earned),
            total_spent=int(bal.total_spent),
        )


@router.get(
    "/exchange/transactions", response_model=TransactionsResponse, tags=["Settlement"]
)
@limiter.limit(settings.rate_limit_authenticated)
def transactions(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> TransactionsResponse:
    with session.begin():
        txs = (
            session.execute(
                select(Transaction)
                .where(
                    or_(
                        Transaction.from_account == current["id"],
                        Transaction.to_account == current["id"],
                    )
                )
                .order_by(Transaction.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            .scalars()
            .all()
        )
    return TransactionsResponse(
        transactions=[
            TransactionItem(
                id=tx.id,
                escrow_id=tx.escrow_id,
                from_account=tx.from_account,
                to_account=tx.to_account,
                amount=int(tx.amount),
                type=tx.tx_type,
                description=tx.description,
                created_at=tx.created_at,
            )
            for tx in txs
        ]
    )


@router.get(
    "/exchange/escrows/{escrow_id}",
    response_model=EscrowDetailResponse,
    tags=["Settlement"],
)
@limiter.limit(settings.rate_limit_authenticated)
def get_escrow(
    request: Request,
    escrow_id: str,
    _current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> EscrowDetailResponse:
    with session.begin():
        escrow = session.execute(
            select(Escrow).where(Escrow.id == escrow_id)
        ).scalar_one_or_none()
        if escrow is None:
            raise HTTPException(status_code=404, detail="Escrow not found")
        return _escrow_detail(escrow)


@router.get("/exchange/escrows", response_model=EscrowListResponse, tags=["Settlement"])
@limiter.limit(settings.rate_limit_authenticated)
def list_escrows(
    request: Request,
    task_id: str | None = None,
    group_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> EscrowListResponse:
    with session.begin():
        stmt = select(Escrow).where(
            or_(
                Escrow.requester_id == current["id"],
                Escrow.provider_id == current["id"],
            )
        )
        if task_id is not None:
            stmt = stmt.where(Escrow.task_id == task_id)
        if group_id is not None:
            stmt = stmt.where(Escrow.group_id == group_id)
        if status is not None:
            stmt = stmt.where(Escrow.status == status)

        count = session.execute(
            select(sa_func.count()).select_from(stmt.subquery())
        ).scalar_one()

        rows = (
            session.execute(
                stmt.order_by(Escrow.created_at.desc()).limit(limit).offset(offset)
            )
            .scalars()
            .all()
        )

    return EscrowListResponse(
        escrows=[_escrow_detail(e) for e in rows],
        total=count,
    )


@router.post(
    "/exchange/escrow/batch",
    status_code=201,
    response_model=BatchEscrowResponse,
    tags=["Settlement"],
)
@limiter.limit(settings.rate_limit_authenticated)
def batch_create_escrow(
    request: Request,
    req: BatchEscrowRequest,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> BatchEscrowResponse:
    group_id = req.group_id or str(uuid.uuid4())
    created: list[EscrowResponse] = []

    with session.begin():
        _expire_stale_escrows(session)

        bal = session.execute(
            _lock(select(Balance).where(Balance.account_id == current["id"]))
        ).scalar_one_or_none()
        if bal is None:
            raise HTTPException(status_code=404, detail="Requester account not found")

        total_needed = 0
        for item in req.escrows:
            if item.amount < settings.min_escrow or item.amount > settings.max_escrow:
                raise HTTPException(
                    status_code=400,
                    detail=f"Amount must be between {settings.min_escrow} and {settings.max_escrow}",
                )
            if current["id"] == item.provider_id:
                raise HTTPException(status_code=400, detail="Cannot escrow to yourself")
            fee = _fee_amount(item.amount)
            total_needed += item.amount + fee

        if bal.available < total_needed:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient balance for batch. Need {total_needed}, have {bal.available}",
            )

        _check_spending_limits(session, current["id"], total_needed)

        created_escrows: list[Escrow] = []
        for idx, item in enumerate(req.escrows):
            fee = _fee_amount(item.amount)
            total_hold = item.amount + fee
            ttl = item.ttl_minutes or settings.default_ttl_minutes
            expires_at = _now() + timedelta(minutes=ttl)

            provider = session.execute(
                select(Account).where(Account.id == item.provider_id)
            ).scalar_one_or_none()
            if provider is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Provider account not found: {item.provider_id}",
                )
            if provider.status != "active":
                raise HTTPException(
                    status_code=400,
                    detail=f"Provider account is not active: {item.provider_id}",
                )

            kya_gate = _check_kya_gate(
                session, current["id"], item.provider_id, item.amount
            )
            if not kya_gate["allowed"]:
                raise HTTPException(
                    status_code=403, detail=kya_gate["rejection_reason"]
                )

            resolved_deps: list[str] | None = None
            if item.depends_on:
                resolved_deps = []
                for dep_ref in item.depends_on:
                    if dep_ref.startswith("$"):
                        dep_idx = int(dep_ref[1:])
                        if dep_idx >= idx:
                            raise HTTPException(
                                status_code=400,
                                detail=f"depends_on '${dep_idx}' must reference an earlier batch item",
                            )
                        resolved_deps.append(created_escrows[dep_idx].id)
                    else:
                        resolved_deps.append(dep_ref)

            deliverables_json = (
                [d.model_dump() for d in item.deliverables]
                if item.deliverables
                else None
            )

            bal.available -= total_hold
            bal.held_in_escrow += total_hold

            batch_vi_chain = (
                item.vi_credential_chain.model_dump(mode="json")
                if item.vi_credential_chain
                else None
            )

            escrow = Escrow(
                requester_id=current["id"],
                provider_id=item.provider_id,
                amount=item.amount,
                fee_amount=fee,
                task_id=item.task_id,
                task_type=item.task_type,
                group_id=group_id,
                depends_on=resolved_deps,
                deliverables=deliverables_json,
                required_attestation_level=item.required_attestation_level,
                vi_credential_chain=batch_vi_chain,
                status="held",
                expires_at=expires_at,
                requester_did=kya_gate["requester_did"],
                provider_did=kya_gate["provider_did"],
                kya_level_at_creation=kya_gate["required_level"],
                hitl_required=kya_gate["hitl_required"],
            )
            session.add(escrow)
            session.flush()
            created_escrows.append(escrow)

            session.add(
                Transaction(
                    escrow_id=escrow.id,
                    from_account=current["id"],
                    to_account=None,
                    amount=total_hold,
                    tx_type="escrow_hold",
                    description=f"Batch escrow for task: {item.task_type or item.task_id or 'unspecified'}",
                )
            )

            created.append(
                EscrowResponse(
                    escrow_id=escrow.id,
                    requester_id=current["id"],
                    provider_id=item.provider_id,
                    amount=int(item.amount),
                    fee_amount=int(fee),
                    effective_fee_percent=_effective_fee_percent(item.amount, fee),
                    total_held=int(total_hold),
                    status=escrow.status,
                    expires_at=escrow.expires_at,
                    group_id=group_id,
                )
            )

        session.add(bal)

    for esc in created_escrows:
        fire_webhook_event(session, esc, "escrow.created")

    return BatchEscrowResponse(group_id=group_id, escrows=created)


# ---------------------------------------------------------------------------
# VI Attestation
# ---------------------------------------------------------------------------


@router.get(
    "/exchange/attestation/{account_id}",
    response_model=VIAttestation,
    tags=["Settlement"],
)
@limiter.limit(settings.rate_limit_public)
def get_vi_attestation(
    request: Request,
    account_id: str,
    session: Session = Depends(get_session),
) -> VIAttestation:
    """Return the current EMA reputation score formatted as a VI-compatible
    ``agent_attestation`` payload (Verifiable Intent spec section 9.2).

    This is a public endpoint — verifiers can fetch fresh attestations
    without authenticating as the target agent.
    """
    import hashlib
    import json as _json

    acct = session.get(Account, account_id)
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")

    total_completed = (
        session.execute(
            select(sa_func.count())
            .select_from(Escrow)
            .where(
                Escrow.provider_id == account_id,
                Escrow.status.in_(["released", "refunded", "disputed", "partially_released"]),
            )
        ).scalar_one()
    )

    dispute_count = (
        session.execute(
            select(sa_func.count())
            .select_from(Escrow)
            .where(
                Escrow.provider_id == account_id,
                Escrow.status.in_(["disputed", "refunded"]),
            )
        ).scalar_one()
    )

    dispute_rate = dispute_count / total_completed if total_completed else 0.0

    now = _now()
    value_payload = {
        "score": round(float(acct.reputation), 4),
        "lambda": 0.1,
        "task_count": total_completed,
        "dispute_rate": round(dispute_rate, 4),
        "window_days": 90,
        "exchange_id": settings.exchange_id if hasattr(settings, "exchange_id") else "a2a-se-default",
        "exchange_url": str(request.base_url).rstrip("/"),
        "issued_at": now.isoformat(),
    }

    canonical = _json.dumps(value_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    value_payload["signature"] = hashlib.sha256(canonical).hexdigest()

    return VIAttestation(
        type="urn:a2a-settlement:ema-reputation:v1",
        value=value_payload,
    )
