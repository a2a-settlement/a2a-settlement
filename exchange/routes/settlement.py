from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from exchange.auth import authenticate_bot
from exchange.config import get_session, settings
from exchange.models import Account, Balance, Escrow, Transaction


router = APIRouter()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fee_amount(amount: int) -> int:
    return int(math.ceil(amount * (settings.fee_percent / 100.0)))


def _lock(stmt):
    # SQLite ignores FOR UPDATE; Postgres uses row-level locks.
    return stmt.with_for_update()


def _expire_stale_escrows(session: Session) -> int:
    now = _now()
    stale = session.execute(_lock(select(Escrow).where(and_(Escrow.status == "held", Escrow.expires_at < now)))).scalars().all()
    expired_count = 0
    for escrow in stale:
        total_held = int(escrow.amount + escrow.fee_amount)
        bal = session.execute(_lock(select(Balance).where(Balance.account_id == escrow.requester_id))).scalar_one_or_none()
        if bal is None:
            continue
        bal.available += total_held
        bal.held_in_escrow -= total_held
        session.add(bal)

        escrow.status = "expired"
        escrow.resolved_at = now
        session.add(escrow)

        session.add(
            Transaction(
                escrow_id=escrow.id,
                from_account=None,
                to_account=escrow.requester_id,
                amount=total_held,
                tx_type="escrow_refund",
                description="Auto-expired: TTL exceeded",
            )
        )
        expired_count += 1
    return expired_count


class EscrowRequest(BaseModel):
    provider_id: str
    amount: int
    task_id: str | None = None
    task_type: str | None = None
    ttl_minutes: int | None = None


@router.post("/exchange/escrow", status_code=201)
def create_escrow(
    req: EscrowRequest,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
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

        provider = session.execute(select(Account).where(Account.id == req.provider_id)).scalar_one_or_none()
        if provider is None:
            raise HTTPException(status_code=404, detail="Provider account not found")
        if provider.status != "active":
            raise HTTPException(status_code=400, detail="Provider account is not active")

        bal.available -= total_hold
        bal.held_in_escrow += total_hold
        session.add(bal)

        escrow = Escrow(
            requester_id=current["id"],
            provider_id=req.provider_id,
            amount=req.amount,
            fee_amount=fee_amount,
            task_id=req.task_id,
            task_type=req.task_type,
            status="held",
            expires_at=expires_at,
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

    return {
        "escrow_id": escrow.id,
        "requester_id": current["id"],
        "provider_id": req.provider_id,
        "amount": int(req.amount),
        "fee_amount": int(fee_amount),
        "total_held": int(total_hold),
        "status": escrow.status,
        "expires_at": escrow.expires_at,
    }


class ReleaseRequest(BaseModel):
    escrow_id: str


@router.post("/exchange/release")
def release(
    req: ReleaseRequest,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    with session.begin():
        _expire_stale_escrows(session)

        escrow = session.execute(_lock(select(Escrow).where(Escrow.id == req.escrow_id))).scalar_one_or_none()
        if escrow is None:
            raise HTTPException(status_code=404, detail="Escrow not found")
        if escrow.requester_id != current["id"]:
            raise HTTPException(status_code=403, detail="Only the requester can release an escrow")
        if escrow.status != "held":
            raise HTTPException(status_code=400, detail=f"Escrow is already {escrow.status}")

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

    return {
        "escrow_id": req.escrow_id,
        "status": "released",
        "amount_paid": int(escrow.amount),
        "fee_collected": int(escrow.fee_amount),
        "provider_id": escrow.provider_id,
    }


class RefundRequest(BaseModel):
    escrow_id: str
    reason: str | None = None


@router.post("/exchange/refund")
def refund(
    req: RefundRequest,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
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

    return {
        "escrow_id": req.escrow_id,
        "status": "refunded",
        "amount_returned": total_held,
        "requester_id": escrow.requester_id,
    }


@router.get("/exchange/balance")
def balance(
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    with session.begin():
        row = session.execute(
            select(Balance, Account)
            .join(Account, Account.id == Balance.account_id)
            .where(Balance.account_id == current["id"])
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Account not found")
        bal, acct = row
        return {
            "account_id": acct.id,
            "bot_name": acct.bot_name,
            "reputation": float(acct.reputation),
            "account_status": acct.status,
            "available": int(bal.available),
            "held_in_escrow": int(bal.held_in_escrow),
            "total_earned": int(bal.total_earned),
            "total_spent": int(bal.total_spent),
        }


@router.get("/exchange/transactions")
def transactions(
    limit: int = 50,
    offset: int = 0,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
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
    return {
        "transactions": [
            {
                "id": tx.id,
                "escrow_id": tx.escrow_id,
                "from_account": tx.from_account,
                "to_account": tx.to_account,
                "amount": int(tx.amount),
                "tx_type": tx.tx_type,
                "description": tx.description,
                "created_at": tx.created_at,
            }
            for tx in txs
        ]
    }


@router.get("/exchange/escrows/{escrow_id}")
def get_escrow(
    escrow_id: str,
    _current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    with session.begin():
        escrow = session.execute(select(Escrow).where(Escrow.id == escrow_id)).scalar_one_or_none()
        if escrow is None:
            raise HTTPException(status_code=404, detail="Escrow not found")
        return {
            "id": escrow.id,
            "requester_id": escrow.requester_id,
            "provider_id": escrow.provider_id,
            "amount": int(escrow.amount),
            "fee_amount": int(escrow.fee_amount),
            "status": escrow.status,
            "expires_at": escrow.expires_at,
            "task_id": escrow.task_id,
            "task_type": escrow.task_type,
            "created_at": escrow.created_at,
            "resolved_at": escrow.resolved_at,
        }

