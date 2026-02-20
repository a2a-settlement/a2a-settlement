from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from exchange.config import SessionLocal, settings
from exchange.models import Balance, Escrow, Transaction
from exchange.webhooks import fire_webhook_event

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _lock(stmt):
    return stmt.with_for_update()


def expire_stale_escrows(session: Session) -> int:
    """Expire held escrows past their TTL, refunding tokens to the requester.

    Returns the number of escrows expired.
    """
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
    expired_count = 0
    for escrow in stale:
        total_held = int(escrow.amount + escrow.fee_amount)
        bal = session.execute(
            _lock(select(Balance).where(Balance.account_id == escrow.requester_id))
        ).scalar_one_or_none()
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


def run_expiry_sweep() -> int:
    """Run a single expiry sweep in its own session, firing webhooks for each."""
    session = SessionLocal()
    try:
        with session.begin():
            expired = expire_stale_escrows(session)
            if expired:
                expired_rows = (
                    session.execute(
                        select(Escrow).where(Escrow.status == "expired")
                        .order_by(Escrow.resolved_at.desc())
                        .limit(expired)
                    )
                    .scalars()
                    .all()
                )
                for escrow in expired_rows:
                    fire_webhook_event(session, escrow, "escrow.expired")
        return expired
    finally:
        session.close()


async def background_expiry_loop() -> None:
    """Periodically expire stale escrows in the background."""
    interval = settings.expiry_interval_seconds
    logger.info("Background expiry loop started (interval=%ds)", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            count = run_expiry_sweep()
            if count:
                logger.info("Background sweep expired %d escrow(s)", count)
        except Exception:
            logger.exception("Error in background expiry sweep")
