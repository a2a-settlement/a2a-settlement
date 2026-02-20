from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, func as sa_func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from exchange.auth import authenticate_bot
from exchange.config import get_session, settings
from exchange.models import Account, Balance, Escrow, Transaction
from exchange.schemas import (
    BalanceResponse,
    BatchEscrowItem,
    BatchEscrowRequest,
    BatchEscrowResponse,
    DepositRequest,
    DepositResponse,
    DisputeRequest,
    DisputeResponse,
    EscrowDetailResponse,
    EscrowListResponse,
    EscrowRequest,
    EscrowResponse,
    RefundRequest,
    RefundResponse,
    ReleaseRequest,
    ReleaseResponse,
    ResolveRefundResponse,
    ResolveReleaseResponse,
    ResolveRequest,
    TransactionItem,
    TransactionsResponse,
)
from exchange.tasks import expire_stale_escrows as _expire_stale_escrows
from exchange.webhooks import fire_webhook_event


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
    return float((Decimal(fee) / Decimal(amount) * Decimal("100")).quantize(Decimal("0.0001")))


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
        effective_fee_percent=_effective_fee_percent(int(escrow.amount), int(escrow.fee_amount)),
        status=escrow.status,
        dispute_reason=escrow.dispute_reason,
        resolution_strategy=escrow.resolution_strategy,
        expires_at=escrow.expires_at,
        task_id=escrow.task_id,
        task_type=escrow.task_type,
        group_id=escrow.group_id,
        depends_on=escrow.depends_on,
        deliverables=deliverables,
        created_at=escrow.created_at,
        resolved_at=escrow.resolved_at,
    )


def _lock(stmt):
    return stmt.with_for_update()


def _auto_refund_dependents(session: Session, upstream_escrow_id: str) -> None:
    """Auto-refund any held escrows that depend on the given (now-refunded) escrow."""
    dependents = session.execute(
        select(Escrow).where(and_(Escrow.status == "held", Escrow.depends_on.isnot(None)))
    ).scalars().all()
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


def _check_daily_spend_limit(session: Session, account_id: str, new_hold: int) -> None:
    """Raise 400 if the new hold would exceed the account's daily spending limit."""
    acct = session.execute(
        select(Account).where(Account.id == account_id)
    ).scalar_one_or_none()
    if acct is None:
        return

    limit = acct.daily_spend_limit
    if limit is None or limit <= 0:
        return

    today_start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    spent_today = session.execute(
        select(sa_func.coalesce(sa_func.sum(Transaction.amount), 0)).where(
            and_(
                Transaction.from_account == account_id,
                Transaction.tx_type == "escrow_hold",
                Transaction.created_at >= today_start,
            )
        )
    ).scalar_one()

    if int(spent_today) + new_hold > limit:
        raise HTTPException(
            status_code=400,
            detail=f"Daily spend limit exceeded. Limit: {limit}, spent today: {spent_today}, requested: {new_hold}",
        )


@router.post("/exchange/deposit", status_code=201, response_model=DepositResponse, tags=["Settlement"])
def deposit(
    req: DepositRequest,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> DepositResponse:
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Deposit amount must be positive")

    with session.begin():
        bal = session.execute(_lock(select(Balance).where(Balance.account_id == current["id"]))).scalar_one_or_none()
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


@router.post("/exchange/escrow", status_code=201, response_model=EscrowResponse, tags=["Settlement"])
def create_escrow(
    req: EscrowRequest,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> EscrowResponse:
    if req.amount < settings.min_escrow or req.amount > settings.max_escrow:
        raise HTTPException(status_code=400, detail=f"Amount must be between {settings.min_escrow} and {settings.max_escrow}")
    if current["id"] == req.provider_id:
        raise HTTPException(status_code=400, detail="Cannot escrow to yourself")

    fee_amount = _fee_amount(req.amount)
    total_hold = req.amount + fee_amount
    ttl = req.ttl_minutes or settings.default_ttl_minutes
    expires_at = _now() + timedelta(minutes=ttl)

    with session.begin():
        _expire_stale_escrows(session)

        bal = session.execute(_lock(select(Balance).where(Balance.account_id == current["id"]))).scalar_one_or_none()
        if bal is None:
            raise HTTPException(status_code=404, detail="Requester account not found")
        if bal.available < total_hold:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient balance. Need {total_hold} ({req.amount} + {fee_amount} fee), have {bal.available}",
            )

        _check_daily_spend_limit(session, current["id"], total_hold)

        provider = session.execute(select(Account).where(Account.id == req.provider_id)).scalar_one_or_none()
        if provider is None:
            raise HTTPException(status_code=404, detail="Provider account not found")
        if provider.status != "active":
            raise HTTPException(status_code=400, detail="Provider account is not active")

        bal.available -= total_hold
        bal.held_in_escrow += total_hold
        session.add(bal)

        if req.depends_on:
            deps = session.execute(
                select(Escrow).where(
                    and_(Escrow.id.in_(req.depends_on), Escrow.requester_id == current["id"])
                )
            ).scalars().all()
            if len(deps) != len(req.depends_on):
                raise HTTPException(
                    status_code=400,
                    detail="One or more depends_on escrow IDs not found or not owned by requester",
                )

        deliverables_json = [d.model_dump() for d in req.deliverables] if req.deliverables else None

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
            status="held",
            expires_at=expires_at,
        )
        session.add(escrow)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
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
            eid = existing.id if existing else "unknown"
            raise HTTPException(
                status_code=409,
                detail=f"An active escrow already exists for this task_id (escrow_id={eid})",
            )

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


@router.post("/exchange/release", response_model=ReleaseResponse, tags=["Settlement"])
def release(
    req: ReleaseRequest,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> ReleaseResponse:
    with session.begin():
        _expire_stale_escrows(session)

        escrow = session.execute(_lock(select(Escrow).where(Escrow.id == req.escrow_id))).scalar_one_or_none()
        if escrow is None:
            raise HTTPException(status_code=404, detail="Escrow not found")
        if escrow.requester_id != current["id"]:
            raise HTTPException(status_code=403, detail="Only the requester can release an escrow")
        if escrow.status != "held":
            raise HTTPException(status_code=400, detail=f"Escrow is already {escrow.status}")

        if escrow.depends_on:
            unresolved = session.execute(
                select(Escrow).where(
                    and_(Escrow.id.in_(escrow.depends_on), Escrow.status != "released")
                )
            ).scalars().all()
            if unresolved:
                ids = [e.id for e in unresolved]
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot release: upstream escrows not yet released: {ids}",
                )

        total_held = int(escrow.amount + escrow.fee_amount)

        requester_bal = session.execute(_lock(select(Balance).where(Balance.account_id == escrow.requester_id))).scalar_one_or_none()
        provider_bal = session.execute(_lock(select(Balance).where(Balance.account_id == escrow.provider_id))).scalar_one_or_none()
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
                description="Task completed - payment released",
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
                    description="Platform transaction fee",
                )
            )

        provider = session.execute(select(Account).where(Account.id == escrow.provider_id)).scalar_one_or_none()
        if provider is not None:
            provider.reputation = min(1.0, float(provider.reputation) * 0.9 + 1.0 * 0.1)
            session.add(provider)

    fire_webhook_event(session, escrow, "escrow.released")

    return ReleaseResponse(
        escrow_id=req.escrow_id,
        status="released",
        amount_paid=int(escrow.amount),
        fee_collected=int(escrow.fee_amount),
        provider_id=escrow.provider_id,
    )


@router.post("/exchange/refund", response_model=RefundResponse, tags=["Settlement"])
def refund(
    req: RefundRequest,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> RefundResponse:
    with session.begin():
        _expire_stale_escrows(session)

        escrow = session.execute(_lock(select(Escrow).where(Escrow.id == req.escrow_id))).scalar_one_or_none()
        if escrow is None:
            raise HTTPException(status_code=404, detail="Escrow not found")
        if escrow.requester_id != current["id"]:
            raise HTTPException(status_code=403, detail="Only the requester can refund an escrow")
        if escrow.status != "held":
            raise HTTPException(status_code=400, detail=f"Escrow is already {escrow.status}")

        total_held = int(escrow.amount + escrow.fee_amount)

        requester_bal = session.execute(_lock(select(Balance).where(Balance.account_id == escrow.requester_id))).scalar_one_or_none()
        if requester_bal is None:
            raise HTTPException(status_code=404, detail="Requester balance not found")

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
                description=req.reason or "Task failed or cancelled",
            )
        )

        provider = session.execute(select(Account).where(Account.id == escrow.provider_id)).scalar_one_or_none()
        if provider is not None:
            provider.reputation = max(0.0, float(provider.reputation) * 0.9 + 0.0 * 0.1)
            session.add(provider)

        _auto_refund_dependents(session, escrow.id)

    fire_webhook_event(session, escrow, "escrow.refunded")

    return RefundResponse(
        escrow_id=req.escrow_id,
        status="refunded",
        amount_returned=total_held,
        requester_id=escrow.requester_id,
    )


@router.post("/exchange/dispute", response_model=DisputeResponse, tags=["Settlement"])
def dispute(
    req: DisputeRequest,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> DisputeResponse:
    with session.begin():
        escrow = session.execute(_lock(select(Escrow).where(Escrow.id == req.escrow_id))).scalar_one_or_none()
        if escrow is None:
            raise HTTPException(status_code=404, detail="Escrow not found")
        if current["id"] not in (escrow.requester_id, escrow.provider_id):
            raise HTTPException(status_code=403, detail="Only the requester or provider can dispute an escrow")
        if escrow.status != "held":
            raise HTTPException(status_code=400, detail=f"Escrow cannot be disputed (status: {escrow.status})")

        escrow.status = "disputed"
        escrow.dispute_reason = req.reason
        session.add(escrow)

    fire_webhook_event(session, escrow, "escrow.disputed")
    fire_webhook_event(session, escrow, "escrow.dispute_pending_mediation")

    return DisputeResponse(
        escrow_id=req.escrow_id,
        status="disputed",
        reason=req.reason,
    )


@router.post("/exchange/resolve", tags=["Settlement"])
def resolve(
    req: ResolveRequest,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> ResolveReleaseResponse | ResolveRefundResponse:
    if req.resolution not in ("release", "refund"):
        raise HTTPException(status_code=400, detail="resolution must be 'release' or 'refund'")

    if current.get("status") != "operator":
        raise HTTPException(status_code=403, detail="Only the exchange operator can resolve disputes")

    with session.begin():
        escrow = session.execute(_lock(select(Escrow).where(Escrow.id == req.escrow_id))).scalar_one_or_none()
        if escrow is None:
            raise HTTPException(status_code=404, detail="Escrow not found")
        if escrow.status != "disputed":
            raise HTTPException(status_code=400, detail=f"Escrow is not disputed (status: {escrow.status})")

        escrow.resolution_strategy = req.strategy
        total_held = int(escrow.amount + escrow.fee_amount)

        if req.resolution == "release":
            requester_bal = session.execute(_lock(select(Balance).where(Balance.account_id == escrow.requester_id))).scalar_one_or_none()
            provider_bal = session.execute(_lock(select(Balance).where(Balance.account_id == escrow.provider_id))).scalar_one_or_none()
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

            provider = session.execute(select(Account).where(Account.id == escrow.provider_id)).scalar_one_or_none()
            if provider is not None:
                provider.reputation = min(1.0, float(provider.reputation) * 0.9 + 1.0 * 0.1)
                session.add(provider)

        else:
            requester_bal = session.execute(_lock(select(Balance).where(Balance.account_id == escrow.requester_id))).scalar_one_or_none()
            if requester_bal is None:
                raise HTTPException(status_code=404, detail="Requester balance not found")

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

            provider = session.execute(select(Account).where(Account.id == escrow.provider_id)).scalar_one_or_none()
            if provider is not None:
                provider.reputation = max(0.0, float(provider.reputation) * 0.9 + 0.0 * 0.1)
                session.add(provider)

    fire_webhook_event(session, escrow, "escrow.resolved")

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
def balance(
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


@router.get("/exchange/transactions", response_model=TransactionsResponse, tags=["Settlement"])
def transactions(
    limit: int = 50,
    offset: int = 0,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> TransactionsResponse:
    with session.begin():
        txs = (
            session.execute(
                select(Transaction)
                .where(or_(Transaction.from_account == current["id"], Transaction.to_account == current["id"]))
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


@router.get("/exchange/escrows/{escrow_id}", response_model=EscrowDetailResponse, tags=["Settlement"])
def get_escrow(
    escrow_id: str,
    _current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> EscrowDetailResponse:
    with session.begin():
        escrow = session.execute(select(Escrow).where(Escrow.id == escrow_id)).scalar_one_or_none()
        if escrow is None:
            raise HTTPException(status_code=404, detail="Escrow not found")
        return _escrow_detail(escrow)


@router.get("/exchange/escrows", response_model=EscrowListResponse, tags=["Settlement"])
def list_escrows(
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
            or_(Escrow.requester_id == current["id"], Escrow.provider_id == current["id"])
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

        rows = session.execute(
            stmt.order_by(Escrow.created_at.desc()).limit(limit).offset(offset)
        ).scalars().all()

    return EscrowListResponse(
        escrows=[_escrow_detail(e) for e in rows],
        total=count,
    )


@router.post("/exchange/escrow/batch", status_code=201, response_model=BatchEscrowResponse, tags=["Settlement"])
def batch_create_escrow(
    req: BatchEscrowRequest,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> BatchEscrowResponse:
    group_id = req.group_id or str(uuid.uuid4())
    created: list[EscrowResponse] = []

    with session.begin():
        _expire_stale_escrows(session)

        bal = session.execute(_lock(select(Balance).where(Balance.account_id == current["id"]))).scalar_one_or_none()
        if bal is None:
            raise HTTPException(status_code=404, detail="Requester account not found")

        total_needed = 0
        for item in req.escrows:
            if item.amount < settings.min_escrow or item.amount > settings.max_escrow:
                raise HTTPException(status_code=400, detail=f"Amount must be between {settings.min_escrow} and {settings.max_escrow}")
            if current["id"] == item.provider_id:
                raise HTTPException(status_code=400, detail="Cannot escrow to yourself")
            fee = _fee_amount(item.amount)
            total_needed += item.amount + fee

        if bal.available < total_needed:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient balance for batch. Need {total_needed}, have {bal.available}",
            )

        _check_daily_spend_limit(session, current["id"], total_needed)

        created_escrows: list[Escrow] = []
        for idx, item in enumerate(req.escrows):
            fee = _fee_amount(item.amount)
            total_hold = item.amount + fee
            ttl = item.ttl_minutes or settings.default_ttl_minutes
            expires_at = _now() + timedelta(minutes=ttl)

            provider = session.execute(select(Account).where(Account.id == item.provider_id)).scalar_one_or_none()
            if provider is None:
                raise HTTPException(status_code=404, detail=f"Provider account not found: {item.provider_id}")
            if provider.status != "active":
                raise HTTPException(status_code=400, detail=f"Provider account is not active: {item.provider_id}")

            resolved_deps: list[str] | None = None
            if item.depends_on:
                resolved_deps = []
                for dep_ref in item.depends_on:
                    if dep_ref.startswith("$"):
                        dep_idx = int(dep_ref[1:])
                        if dep_idx >= idx:
                            raise HTTPException(status_code=400, detail=f"depends_on '${dep_idx}' must reference an earlier batch item")
                        resolved_deps.append(created_escrows[dep_idx].id)
                    else:
                        resolved_deps.append(dep_ref)

            deliverables_json = [d.model_dump() for d in item.deliverables] if item.deliverables else None

            bal.available -= total_hold
            bal.held_in_escrow += total_hold

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
                status="held",
                expires_at=expires_at,
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

            created.append(EscrowResponse(
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
            ))

        session.add(bal)

    for esc in created_escrows:
        fire_webhook_event(session, esc, "escrow.created")

    return BatchEscrowResponse(group_id=group_id, escrows=created)
