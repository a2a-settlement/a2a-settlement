from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone
from threading import Thread
from time import sleep

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from exchange.config import SessionLocal, settings
from exchange.models import Escrow, WebhookConfig

logger = logging.getLogger(__name__)

ALL_EVENTS = [
    "escrow.created",
    "escrow.released",
    "escrow.refunded",
    "escrow.expired",
    "escrow.disputed",
    "escrow.resolved",
]

RETRY_BACKOFF = [5, 25, 125]


def _sign_payload(secret: str, body: bytes) -> str:
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def _deliver(url: str, secret: str, event: str, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    signature = _sign_payload(secret, body)
    delivery_id = f"evt_{uuid.uuid4().hex[:12]}"
    headers = {
        "Content-Type": "application/json",
        "X-A2ASE-Signature": signature,
        "X-A2ASE-Event": event,
        "X-A2ASE-Delivery": delivery_id,
    }

    retries = settings.webhook_max_retries
    for attempt in range(1 + retries):
        try:
            resp = httpx.post(url, content=body, headers=headers, timeout=settings.webhook_timeout_seconds)
            if 200 <= resp.status_code < 300:
                return
            logger.warning("Webhook delivery to %s returned %s (attempt %d)", url, resp.status_code, attempt + 1)
        except Exception:
            logger.warning("Webhook delivery to %s failed (attempt %d)", url, attempt + 1, exc_info=True)
        if attempt < retries:
            backoff = RETRY_BACKOFF[attempt] if attempt < len(RETRY_BACKOFF) else RETRY_BACKOFF[-1]
            sleep(backoff)


def _build_escrow_payload(escrow: Escrow, event: str) -> dict:
    return {
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {
            "escrow_id": escrow.id,
            "requester_id": escrow.requester_id,
            "provider_id": escrow.provider_id,
            "amount": int(escrow.amount),
            "fee_amount": int(escrow.fee_amount),
            "status": escrow.status,
        },
    }


def fire_webhook_event(session: Session, escrow: Escrow, event: str) -> None:
    """Fire a webhook event for both requester and provider if they have webhooks configured."""
    account_ids = [escrow.requester_id, escrow.provider_id]

    db = SessionLocal()
    try:
        with db.begin():
            configs = (
                db.execute(
                    select(WebhookConfig).where(
                        WebhookConfig.account_id.in_(account_ids),
                        WebhookConfig.active.is_(True),
                    )
                )
                .scalars()
                .all()
            )

        payload = _build_escrow_payload(escrow, event)
        for cfg in configs:
            if cfg.events and event not in cfg.events:
                continue
            thread = Thread(target=_deliver, args=(cfg.url, cfg.secret, event, payload), daemon=True)
            thread.start()
    finally:
        db.close()
