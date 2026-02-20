from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from exchange.models import Balance, Escrow, Transaction
from exchange.webhooks import fire_webhook_event

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _lock(stmt):
    return stmt.with_for_update()


def _refund_escrow(session: Session, escrow: Escrow, now: datetime, description: str) -> None:
    """Refund a single escrow's held amount back to the requester."""
    total_held = int(escrow.amount + escrow.fee_amount)
    bal = session.execute(
        _lock(select(Balance).where(Balance.account_id == escrow.requester_id))
    ).scalar_one_or_none()
    if bal is None:
        return
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
            description=description,
        )
    )


class PaymentTimeoutObserver:
    """Observes escrow deadlines and transitions timed-out escrows to expired."""

    def __init__(self, dispute_ttl_minutes: int, expiry_warning_minutes: int) -> None:
        self.dispute_ttl_minutes = dispute_ttl_minutes
        self.expiry_warning_minutes = expiry_warning_minutes

    def expire_stale_held(self, session: Session) -> list[Escrow]:
        """Expire held escrows past their TTL. Returns the expired escrow objects."""
        now = _now()
        stale = (
            session.execute(
                _lock(
                    select(Escrow).where(
                        and_(Escrow.status == "held", Escrow.expires_at < now)
                    )
                )
            )
            .scalars()
            .all()
        )
        expired: list[Escrow] = []
        for escrow in stale:
            _refund_escrow(session, escrow, now, "Auto-expired: TTL exceeded")
            expired.append(escrow)
        return expired

    def expire_stale_disputes(self, session: Session) -> list[Escrow]:
        """Expire disputed escrows past their dispute TTL."""
        now = _now()
        stale = (
            session.execute(
                _lock(
                    select(Escrow).where(
                        and_(
                            Escrow.status == "disputed",
                            Escrow.dispute_expires_at.isnot(None),
                            Escrow.dispute_expires_at < now,
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        expired: list[Escrow] = []
        for escrow in stale:
            _refund_escrow(session, escrow, now, "Auto-expired: dispute TTL exceeded")
            expired.append(escrow)
        return expired

    def warn_expiring_soon(self, session: Session) -> list[Escrow]:
        """Fire expiring-soon webhooks for held escrows approaching their deadline."""
        if self.expiry_warning_minutes <= 0:
            return []
        now = _now()
        warning_horizon = now + timedelta(minutes=self.expiry_warning_minutes)
        approaching = (
            session.execute(
                select(Escrow).where(
                    and_(
                        Escrow.status == "held",
                        Escrow.expires_at <= warning_horizon,
                        Escrow.expires_at > now,
                        Escrow.warning_sent_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        warned: list[Escrow] = []
        for escrow in approaching:
            escrow.warning_sent_at = now
            session.add(escrow)
            warned.append(escrow)
        return warned

    def sweep(self, session: Session) -> dict:
        """Run all timeout checks in a single pass. Returns counts by category."""
        expired_held = self.expire_stale_held(session)
        expired_disputes = self.expire_stale_disputes(session)
        warned = self.warn_expiring_soon(session)
        return {
            "expired_held": expired_held,
            "expired_disputes": expired_disputes,
            "warned": warned,
        }
