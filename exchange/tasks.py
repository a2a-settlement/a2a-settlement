from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from exchange.config import SessionLocal, settings
from exchange.models import Escrow
from exchange.observers import PaymentTimeoutObserver
from exchange.webhooks import fire_webhook_event

logger = logging.getLogger(__name__)

_observer = PaymentTimeoutObserver(
    dispute_ttl_minutes=settings.dispute_ttl_minutes,
    expiry_warning_minutes=settings.expiry_warning_minutes,
)


def expire_stale_escrows(session: Session) -> int:
    """Backward-compatible wrapper: expire held escrows past their TTL.

    Returns the number of escrows expired (held only, not disputes).
    """
    expired = _observer.expire_stale_held(session)
    return len(expired)


def run_expiry_sweep() -> dict:
    """Run a full sweep in its own session, firing webhooks for each event."""
    session = SessionLocal()
    try:
        with session.begin():
            results = _observer.sweep(session)

        for escrow in results["expired_held"]:
            fire_webhook_event(session, escrow, "escrow.expired")
        for escrow in results["expired_disputes"]:
            fire_webhook_event(session, escrow, "escrow.expired")
        for escrow in results["warned"]:
            fire_webhook_event(session, escrow, "escrow.expiring_soon")

        return {
            "expired_held": len(results["expired_held"]),
            "expired_disputes": len(results["expired_disputes"]),
            "warned": len(results["warned"]),
        }
    finally:
        session.close()


async def background_expiry_loop() -> None:
    """Periodically expire stale escrows in the background."""
    interval = settings.expiry_interval_seconds
    logger.info("Background expiry loop started (interval=%ds)", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            counts = run_expiry_sweep()
            total_expired = counts["expired_held"] + counts["expired_disputes"]
            if total_expired:
                logger.info(
                    "Background sweep expired %d escrow(s) (held=%d, disputed=%d)",
                    total_expired,
                    counts["expired_held"],
                    counts["expired_disputes"],
                )
            if counts["warned"]:
                logger.info("Background sweep sent %d expiry warning(s)", counts["warned"])
        except Exception:
            logger.exception("Error in background expiry sweep")
