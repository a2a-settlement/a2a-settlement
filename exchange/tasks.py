from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session

from exchange.config import SessionLocal, settings
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

        from exchange.compliance_log import log_settlement_event

        for escrow in results["expired_held"]:
            fire_webhook_event(session, escrow, "escrow.expired")
            log_settlement_event(
                escrow_id=escrow.id,
                event_type="escrow.expired",
                requester_id=escrow.requester_id,
                provider_id=escrow.provider_id,
                amount=int(escrow.amount),
                status="expired",
            )
        for escrow in results["expired_disputes"]:
            fire_webhook_event(session, escrow, "escrow.expired")
            log_settlement_event(
                escrow_id=escrow.id,
                event_type="escrow.expired",
                requester_id=escrow.requester_id,
                provider_id=escrow.provider_id,
                amount=int(escrow.amount),
                status="expired",
                dispute_reason=escrow.dispute_reason,
            )
        for escrow in results["defaulted_evidence"]:
            fire_webhook_event(session, escrow, "escrow.evidence_window_expired")
            fire_webhook_event(session, escrow, "escrow.default_judgment")
            log_settlement_event(
                escrow_id=escrow.id,
                event_type="escrow.default_judgment",
                requester_id=escrow.requester_id,
                provider_id=escrow.provider_id,
                amount=int(escrow.amount),
                status=escrow.status,
                dispute_reason=escrow.dispute_reason,
            )
        for escrow in results["warned"]:
            fire_webhook_event(session, escrow, "escrow.expiring_soon")

        from exchange.webhooks import fire_account_webhook_event

        for att in results.get("expired_attestations", []):
            fire_account_webhook_event(
                att.account_id,
                "attestation.expired",
                {
                    "attestation_id": att.id,
                    "attestation_type": att.attestation_type,
                },
            )
        for att in results.get("warned_attestations", []):
            fire_account_webhook_event(
                att.account_id,
                "attestation.expiring_soon",
                {
                    "attestation_id": att.id,
                    "attestation_type": att.attestation_type,
                    "expires_at": att.expires_at.isoformat() if att.expires_at else None,
                },
            )

        return {
            "expired_held": len(results["expired_held"]),
            "expired_disputes": len(results["expired_disputes"]),
            "defaulted_evidence": len(results["defaulted_evidence"]),
            "warned": len(results["warned"]),
            "expired_attestations": len(results.get("expired_attestations", [])),
            "warned_attestations": len(results.get("warned_attestations", [])),
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
            if counts["defaulted_evidence"]:
                logger.info(
                    "Background sweep applied %d default judgment(s) from evidence window expiry",
                    counts["defaulted_evidence"],
                )
            if counts["warned"]:
                logger.info("Background sweep sent %d expiry warning(s)", counts["warned"])
            if counts.get("expired_attestations"):
                logger.info(
                    "Background sweep expired %d attestation(s)",
                    counts["expired_attestations"],
                )
            if counts.get("warned_attestations"):
                logger.info(
                    "Background sweep sent %d attestation expiry warning(s)",
                    counts["warned_attestations"],
                )
        except Exception:
            logger.exception("Error in background expiry sweep")
