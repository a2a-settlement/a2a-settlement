from __future__ import annotations

import hashlib
import json as _json
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
from exchange.models import Account, Balance, Escrow, EvidenceSubmission, Transaction
from exchange.schemas import (
    BalanceResponse,
    BatchEscrowRequest,
    BatchEscrowResponse,
    ComplianceBundleResponse,
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
    EvidenceListResponse,
    EvidenceSubmissionResponse,
    InstantSettleRequest,
    InstantSettleResponse,
    MAX_INLINE_EVIDENCE_BYTES,
    OracleEvidenceSubmissionResponse,
    PartialReleaseRequest,
    PartialReleaseResponse,
    RefundRequest,
    RefundResponse,
    ReleaseRequest,
    ReleaseResponse,
    ResolveRefundResponse,
    ResolveReleaseResponse,
    ResolveRequest,
    SubmitEvidenceRequest,
    SubmitOracleEvidenceRequest,
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
        dispute_filed_by=escrow.dispute_filed_by,
        dispute_stake_amount=int(escrow.dispute_stake_amount) if escrow.dispute_stake_amount else None,
        dispute_stake_status=escrow.dispute_stake_status,
        evidence_window_closes_at=escrow.evidence_window_closes_at,
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


def _settle_dispute_stake(
    session: Session, escrow: Escrow, ruling: str | None
) -> None:
    """Return or forfeit the dispute stake based on the mediator's ruling."""
    stake = escrow.dispute_stake_amount
    filer = escrow.dispute_filed_by
    if not stake or not filer or escrow.dispute_stake_status != "held":
        return

    counterparty = (
        escrow.provider_id if filer == escrow.requester_id else escrow.requester_id
    )

    filer_bal = session.execute(
        _lock(select(Balance).where(Balance.account_id == filer))
    ).scalar_one_or_none()

    if ruling == "forfeit" and filer_bal is not None:
        filer_bal.held_in_escrow -= stake
        session.add(filer_bal)

        counter_bal = session.execute(
            _lock(select(Balance).where(Balance.account_id == counterparty))
        ).scalar_one_or_none()
        if counter_bal is not None:
            counter_bal.available += stake
            counter_bal.total_earned += stake
            session.add(counter_bal)

        session.add(
            Transaction(
                escrow_id=escrow.id,
                from_account=filer,
                to_account=counterparty,
                amount=stake,
                tx_type="dispute_stake_forfeit",
                description="Dispute stake forfeited to counterparty",
            )
        )
        escrow.dispute_stake_status = "forfeited"
    elif filer_bal is not None:
        filer_bal.held_in_escrow -= stake
        filer_bal.available += stake
        session.add(filer_bal)

        session.add(
            Transaction(
                escrow_id=escrow.id,
                from_account=None,
                to_account=filer,
                amount=stake,
                tx_type="dispute_stake_return",
                description="Dispute stake returned",
            )
        )
        escrow.dispute_stake_status = "returned"

    session.add(escrow)


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
    "/exchange/instant-settle",
    status_code=200,
    response_model=InstantSettleResponse,
    tags=["Settlement"],
)
@limiter.limit(settings.rate_limit_authenticated)
def instant_settle(
    request: Request,
    req: InstantSettleRequest,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> InstantSettleResponse:
    """Atomic single-call settlement for synchronous micro-transactions.

    Skips the escrow lifecycle entirely — funds move directly from requester
    to provider in one DB transaction.  Only available to agents whose EMA
    reputation score is at or above ``INSTANT_SETTLE_MIN_REPUTATION``
    (default 0.65, ~4 successful escrow releases from the 0.5 baseline).
    The WORM audit trail still fires so compliance coverage is identical to
    the escrow path.
    """
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    if req.amount > settings.instant_settle_max_amount:
        raise HTTPException(
            status_code=400,
            detail=f"Instant settle amount cannot exceed {settings.instant_settle_max_amount}",
        )
    if current["id"] == req.provider_id:
        raise HTTPException(status_code=400, detail="Cannot settle to yourself")

    fee_amount = _fee_amount(req.amount)
    total_cost = req.amount + fee_amount

    with session.begin():
        requester_acct = session.execute(
            _lock(select(Account).where(Account.id == current["id"]))
        ).scalar_one_or_none()
        if requester_acct is None:
            raise HTTPException(status_code=404, detail="Requester account not found")

        if float(requester_acct.reputation) < settings.instant_settle_min_reputation:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Instant settlement requires a minimum reputation of "
                    f"{settings.instant_settle_min_reputation:.2f}. "
                    f"Current reputation: {float(requester_acct.reputation):.2f}. "
                    "Build reputation through successful escrow transactions."
                ),
            )

        provider_acct = session.execute(
            select(Account).where(Account.id == req.provider_id)
        ).scalar_one_or_none()
        if provider_acct is None:
            raise HTTPException(status_code=404, detail="Provider account not found")
        if provider_acct.status != "active":
            raise HTTPException(status_code=400, detail="Provider account is not active")

        kya_gate = _check_kya_gate(session, current["id"], req.provider_id, req.amount)
        if not kya_gate["allowed"]:
            raise HTTPException(status_code=403, detail=kya_gate["rejection_reason"])

        requester_bal = session.execute(
            _lock(select(Balance).where(Balance.account_id == current["id"]))
        ).scalar_one_or_none()
        if requester_bal is None:
            raise HTTPException(status_code=404, detail="Requester balance not found")
        if requester_bal.available < total_cost:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient balance. Need {total_cost} ({req.amount} + {fee_amount} fee), have {requester_bal.available}",
            )

        provider_bal = session.execute(
            _lock(select(Balance).where(Balance.account_id == req.provider_id))
        ).scalar_one_or_none()
        if provider_bal is None:
            raise HTTPException(status_code=404, detail="Provider balance not found")

        _check_spending_limits(session, current["id"], total_cost)

        now = _now()
        tx_id = str(uuid.uuid4())

        requester_bal.available -= total_cost
        requester_bal.total_spent += total_cost
        session.add(requester_bal)

        provider_bal.available += req.amount
        provider_bal.total_earned += req.amount
        session.add(provider_bal)

        session.add(
            Transaction(
                id=tx_id,
                escrow_id=None,
                from_account=current["id"],
                to_account=req.provider_id,
                amount=req.amount,
                tx_type="instant_settlement",
                description=req.description or f"Instant settle: {req.task_type or req.task_id or 'unspecified'}",
            )
        )
        if fee_amount > 0:
            session.add(
                Transaction(
                    escrow_id=None,
                    from_account=current["id"],
                    to_account=None,
                    amount=fee_amount,
                    tx_type="fee",
                    description="Platform fee (instant settlement)",
                )
            )

        provider_acct.reputation = min(1.0, float(provider_acct.reputation) * 0.9 + 1.0 * 0.1)
        session.add(provider_acct)

    log_settlement_event(
        escrow_id=tx_id,
        event_type="instant.settled",
        requester_id=current["id"],
        provider_id=req.provider_id,
        amount=req.amount,
        status="settled",
    )

    return InstantSettleResponse(
        transaction_id=tx_id,
        requester_id=current["id"],
        provider_id=req.provider_id,
        amount=req.amount,
        fee_amount=fee_amount,
        effective_fee_percent=_effective_fee_percent(req.amount, fee_amount),
        new_balance=int(requester_bal.available),
        task_id=req.task_id,
        settled_at=now,
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
    if req.stake_amount < settings.dispute_stake_min:
        raise HTTPException(
            status_code=400,
            detail=f"Dispute stake must be at least {settings.dispute_stake_min} ATE",
        )

    now = _now()
    evidence_window_closes = now + timedelta(hours=settings.evidence_window_hours)

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

        disputer_bal = session.execute(
            _lock(select(Balance).where(Balance.account_id == current["id"]))
        ).scalar_one_or_none()
        if disputer_bal is None or disputer_bal.available < req.stake_amount:
            raise HTTPException(
                status_code=400,
                detail="Insufficient balance for dispute stake",
            )

        disputer_bal.available -= req.stake_amount
        disputer_bal.held_in_escrow += req.stake_amount
        session.add(disputer_bal)

        session.add(
            Transaction(
                escrow_id=escrow.id,
                from_account=current["id"],
                to_account=None,
                amount=req.stake_amount,
                tx_type="dispute_stake_hold",
                description="Dispute stake held pending resolution",
            )
        )

        escrow.status = "evidence_pending"
        escrow.dispute_reason = req.reason
        escrow.dispute_filed_by = current["id"]
        escrow.dispute_stake_amount = req.stake_amount
        escrow.dispute_stake_status = "held"
        escrow.evidence_window_closes_at = evidence_window_closes
        escrow.dispute_expires_at = evidence_window_closes + timedelta(
            minutes=settings.dispute_ttl_minutes
        )
        session.add(escrow)

    fire_webhook_event(session, escrow, "escrow.disputed")
    fire_webhook_event(session, escrow, "escrow.evidence_window_opened")
    log_settlement_event(
        escrow_id=req.escrow_id,
        event_type="escrow.disputed",
        requester_id=escrow.requester_id,
        provider_id=escrow.provider_id,
        amount=int(escrow.amount),
        status="evidence_pending",
        dispute_reason=req.reason,
    )

    return DisputeResponse(
        escrow_id=req.escrow_id,
        status="evidence_pending",
        reason=req.reason,
        stake_amount=req.stake_amount,
        evidence_window_closes_at=evidence_window_closes,
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
        if escrow.status not in ("disputed", "evidence_pending"):
            raise HTTPException(
                status_code=400,
                detail=f"Escrow is not in a disputable state (status: {escrow.status})",
            )

        escrow.resolution_strategy = req.strategy
        if req.provenance_result:
            escrow.provenance_result = req.provenance_result
        if req.mediator_context:
            escrow.mediator_context = req.mediator_context
        total_held = int(escrow.amount + escrow.fee_amount)

        _settle_dispute_stake(session, escrow, req.stake_ruling)

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

    now = _now()
    window_days = settings.attestation_ttl_reputation_days
    window_start = now - timedelta(days=window_days)

    total_completed = (
        session.execute(
            select(sa_func.count())
            .select_from(Escrow)
            .where(
                Escrow.provider_id == account_id,
                Escrow.status.in_(["released", "refunded", "disputed", "partially_released"]),
                Escrow.created_at >= window_start,
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
                Escrow.created_at >= window_start,
            )
        ).scalar_one()
    )

    dispute_rate = dispute_count / total_completed if total_completed else 0.0

    value_payload = {
        "score": round(float(acct.reputation), 4),
        "lambda": 0.1,
        "task_count": total_completed,
        "dispute_rate": round(dispute_rate, 4),
        "window_days": window_days,
        "window_start": window_start.isoformat(),
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


# ---------------------------------------------------------------------------
# Evidence Submission
# ---------------------------------------------------------------------------


@router.post(
    "/exchange/escrow/{escrow_id}/evidence",
    response_model=EvidenceSubmissionResponse,
    tags=["Evidence"],
)
@limiter.limit(settings.rate_limit_authenticated)
def submit_evidence(
    request: Request,
    escrow_id: str,
    req: SubmitEvidenceRequest,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> EvidenceSubmissionResponse:
    now = _now()
    with session.begin():
        escrow = session.execute(
            _lock(select(Escrow).where(Escrow.id == escrow_id))
        ).scalar_one_or_none()
        if escrow is None:
            raise HTTPException(status_code=404, detail="Escrow not found")
        if current["id"] not in (escrow.requester_id, escrow.provider_id):
            raise HTTPException(
                status_code=403,
                detail="Only the requester or provider can submit evidence",
            )
        if escrow.status != "evidence_pending":
            raise HTTPException(
                status_code=400,
                detail=f"Evidence cannot be submitted (status: {escrow.status})",
            )
        if escrow.evidence_window_closes_at and now > escrow.evidence_window_closes_at:
            raise HTTPException(
                status_code=400, detail="Evidence window has closed"
            )

        for artifact in req.artifacts:
            if artifact.artifact_type == "inline":
                if not artifact.content:
                    raise HTTPException(
                        status_code=400,
                        detail="Inline artifact must include content",
                    )
                if len(artifact.content.encode("utf-8")) > settings.max_inline_evidence_bytes:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Inline artifact exceeds {settings.max_inline_evidence_bytes} byte limit",
                    )
                computed = hashlib.sha256(artifact.content.encode("utf-8")).hexdigest()
                if computed != artifact.content_hash:
                    raise HTTPException(
                        status_code=400,
                        detail="Artifact content_hash does not match inline content",
                    )
            elif artifact.artifact_type == "uri":
                if not artifact.uri:
                    raise HTTPException(
                        status_code=400,
                        detail="URI artifact must include uri field",
                    )

        bundle_json = _json.dumps(
            [a.model_dump() for a in req.artifacts], sort_keys=True
        )
        content_hash = hashlib.sha256(bundle_json.encode("utf-8")).hexdigest()

        submission = EvidenceSubmission(
            escrow_id=escrow_id,
            submitter_id=current["id"],
            evidence_type=req.evidence_type.value,
            summary=req.summary,
            artifacts=[a.model_dump() for a in req.artifacts],
            encrypted=req.encrypted,
            encryption_key_id=req.encryption_key_id,
            content_hash=content_hash,
            attestor_id=req.attestor_id,
            attestor_signature=req.attestor_signature,
        )
        session.add(submission)
        session.flush()
        sub_id = submission.id

    fire_webhook_event(session, escrow, "escrow.evidence_submitted")
    log_settlement_event(
        escrow_id=escrow_id,
        event_type="escrow.evidence_submitted",
        requester_id=escrow.requester_id,
        provider_id=escrow.provider_id,
        amount=int(escrow.amount),
        status=escrow.status,
    )

    return EvidenceSubmissionResponse(
        id=sub_id,
        escrow_id=escrow_id,
        submitter_id=current["id"],
        evidence_type=req.evidence_type.value,
        summary=req.summary,
        artifact_count=len(req.artifacts),
        encrypted=req.encrypted,
        source_type="party",
        oracle_id=None,
        submitted_at=now,
    )


@router.get(
    "/exchange/escrow/{escrow_id}/evidence",
    response_model=EvidenceListResponse,
    tags=["Evidence"],
)
@limiter.limit(settings.rate_limit_authenticated)
def list_evidence(
    request: Request,
    escrow_id: str,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> EvidenceListResponse:
    with session.begin():
        escrow = session.execute(
            select(Escrow).where(Escrow.id == escrow_id)
        ).scalar_one_or_none()
        if escrow is None:
            raise HTTPException(status_code=404, detail="Escrow not found")
        if (
            current["id"] not in (escrow.requester_id, escrow.provider_id)
            and current.get("status") != "operator"
        ):
            raise HTTPException(status_code=403, detail="Not authorized")

        rows = (
            session.execute(
                select(EvidenceSubmission)
                .where(EvidenceSubmission.escrow_id == escrow_id)
                .order_by(EvidenceSubmission.submitted_at)
            )
            .scalars()
            .all()
        )

    items = [
        EvidenceSubmissionResponse(
            id=r.id,
            escrow_id=r.escrow_id,
            submitter_id=r.submitter_id,
            evidence_type=r.evidence_type,
            summary=r.summary,
            artifact_count=len(r.artifacts) if r.artifacts else 0,
            encrypted=r.encrypted,
            source_type=r.source_type,
            oracle_id=r.oracle_id,
            submitted_at=r.submitted_at,
        )
        for r in rows
    ]
    return EvidenceListResponse(evidence=items, total=len(items))


# ---------------------------------------------------------------------------
# Oracle Evidence Submission
# ---------------------------------------------------------------------------


@router.post(
    "/exchange/escrow/{escrow_id}/oracle-evidence",
    response_model=OracleEvidenceSubmissionResponse,
    tags=["Evidence"],
)
@limiter.limit(settings.rate_limit_authenticated)
def submit_oracle_evidence(
    request: Request,
    escrow_id: str,
    req: SubmitOracleEvidenceRequest,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> OracleEvidenceSubmissionResponse:
    """Submit third-party corroborating evidence from a registered oracle account.

    Oracle evidence is labelled ``source_type="oracle"`` in the evidence store
    so the AI mediator can weight it independently from self-reported party
    evidence.  Only accounts with ``is_oracle=True`` and reputation >=
    ``ORACLE_MIN_REPUTATION`` (default 0.6) may submit via this endpoint.
    Oracle accounts can submit evidence while the escrow is in either
    ``evidence_pending`` or ``disputed`` state.
    """
    now = _now()

    with session.begin():
        oracle_acct = session.execute(
            select(Account).where(Account.id == current["id"])
        ).scalar_one_or_none()
        if oracle_acct is None:
            raise HTTPException(status_code=404, detail="Account not found")
        if not oracle_acct.is_oracle:
            raise HTTPException(
                status_code=403,
                detail="Only registered oracle accounts can submit oracle evidence",
            )
        if float(oracle_acct.reputation) < settings.oracle_min_reputation:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Oracle account reputation {float(oracle_acct.reputation):.2f} is below "
                    f"the minimum required {settings.oracle_min_reputation:.2f}"
                ),
            )

        escrow = session.execute(
            _lock(select(Escrow).where(Escrow.id == escrow_id))
        ).scalar_one_or_none()
        if escrow is None:
            raise HTTPException(status_code=404, detail="Escrow not found")
        if escrow.status not in ("evidence_pending", "disputed"):
            raise HTTPException(
                status_code=400,
                detail=f"Oracle evidence cannot be submitted for escrow in status: {escrow.status}",
            )

        for artifact in req.artifacts:
            if artifact.artifact_type == "inline":
                if not artifact.content:
                    raise HTTPException(
                        status_code=400, detail="Inline artifact must include content"
                    )
                if len(artifact.content.encode("utf-8")) > settings.max_inline_evidence_bytes:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Inline artifact exceeds {settings.max_inline_evidence_bytes} byte limit",
                    )
                computed = hashlib.sha256(artifact.content.encode("utf-8")).hexdigest()
                if computed != artifact.content_hash:
                    raise HTTPException(
                        status_code=400,
                        detail="Artifact content_hash does not match inline content",
                    )
            elif artifact.artifact_type == "uri":
                if not artifact.uri:
                    raise HTTPException(
                        status_code=400, detail="URI artifact must include uri field"
                    )

        bundle_json = _json.dumps(
            [a.model_dump() for a in req.artifacts], sort_keys=True
        )
        content_hash = hashlib.sha256(bundle_json.encode("utf-8")).hexdigest()

        submission = EvidenceSubmission(
            escrow_id=escrow_id,
            submitter_id=current["id"],
            evidence_type=req.evidence_type.value,
            summary=req.summary,
            artifacts=[a.model_dump() for a in req.artifacts],
            encrypted=False,
            content_hash=content_hash,
            attestor_id=current["id"],
            attestor_signature=req.attestor_signature,
            source_type="oracle",
            oracle_id=current["id"],
        )
        session.add(submission)
        session.flush()
        sub_id = submission.id

    fire_webhook_event(session, escrow, "escrow.evidence_submitted")
    log_settlement_event(
        escrow_id=escrow_id,
        event_type="escrow.oracle_evidence_submitted",
        requester_id=escrow.requester_id,
        provider_id=escrow.provider_id,
        amount=int(escrow.amount),
        status=escrow.status,
    )

    return OracleEvidenceSubmissionResponse(
        id=sub_id,
        escrow_id=escrow_id,
        submitter_id=current["id"],
        evidence_type=req.evidence_type.value,
        summary=req.summary,
        artifact_count=len(req.artifacts),
        encrypted=False,
        source_type="oracle",
        oracle_id=current["id"],
        submitted_at=now,
    )


# ---------------------------------------------------------------------------
# Compliance Bundle
# ---------------------------------------------------------------------------


@router.get(
    "/exchange/escrow/{escrow_id}/compliance-bundle",
    response_model=ComplianceBundleResponse,
    tags=["Evidence"],
)
@limiter.limit(settings.rate_limit_authenticated)
def compliance_bundle(
    request: Request,
    escrow_id: str,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> ComplianceBundleResponse:
    with session.begin():
        escrow = session.execute(
            select(Escrow).where(Escrow.id == escrow_id)
        ).scalar_one_or_none()
        if escrow is None:
            raise HTTPException(status_code=404, detail="Escrow not found")
        if (
            current["id"] not in (escrow.requester_id, escrow.provider_id)
            and current.get("status") != "operator"
        ):
            raise HTTPException(status_code=403, detail="Not authorized")

        evidence_rows = (
            session.execute(
                select(EvidenceSubmission)
                .where(EvidenceSubmission.escrow_id == escrow_id)
                .order_by(EvidenceSubmission.submitted_at)
            )
            .scalars()
            .all()
        )

    contract = {
        "escrow_id": escrow.id,
        "requester_id": escrow.requester_id,
        "provider_id": escrow.provider_id,
        "amount": int(escrow.amount),
        "fee_amount": int(escrow.fee_amount),
        "status": escrow.status,
        "task_id": escrow.task_id,
        "task_type": escrow.task_type,
        "deliverables": escrow.deliverables,
        "dispute_reason": escrow.dispute_reason,
        "dispute_filed_by": escrow.dispute_filed_by,
        "dispute_stake_amount": int(escrow.dispute_stake_amount) if escrow.dispute_stake_amount else None,
        "dispute_stake_status": escrow.dispute_stake_status,
        "resolution_strategy": escrow.resolution_strategy,
        "created_at": escrow.created_at.isoformat() if escrow.created_at else None,
        "resolved_at": escrow.resolved_at.isoformat() if escrow.resolved_at else None,
    }

    evidence_list = [
        {
            "id": e.id,
            "submitter_id": e.submitter_id,
            "evidence_type": e.evidence_type,
            "summary": e.summary,
            "artifacts": e.artifacts,
            "encrypted": e.encrypted,
            "content_hash": e.content_hash,
            "attestor_id": e.attestor_id,
            "source_type": e.source_type,
            "oracle_id": e.oracle_id,
            "submitted_at": e.submitted_at.isoformat() if e.submitted_at else None,
        }
        for e in evidence_rows
    ]

    mediator_rationale = None
    if escrow.provenance_result:
        mediator_rationale = escrow.provenance_result

    merkle_proof = None
    if settings.compliance_enabled:
        try:
            from compliance.merkle import MerkleTree

            tree = MerkleTree()
            root = tree.root_hash
            merkle_proof = {"root_hash": root, "leaf_count": tree.leaf_count}
        except Exception:
            pass

    return ComplianceBundleResponse(
        escrow_id=escrow_id,
        contract=contract,
        evidence_submissions=evidence_list,
        mediator_rationale=mediator_rationale,
        mediator_context=escrow.mediator_context,
        merkle_proof=merkle_proof,
        exported_at=_now(),
    )
