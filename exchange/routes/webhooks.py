from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from exchange.auth import authenticate_bot
from exchange.config import get_session
from exchange.models import WebhookConfig
from exchange.schemas import WebhookDeleteResponse, WebhookResponse, WebhookSetRequest
from exchange.webhooks import ALL_EVENTS

router = APIRouter()


@router.put("/accounts/webhook", response_model=WebhookResponse, tags=["Webhooks"])
def set_webhook(
    req: WebhookSetRequest,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> WebhookResponse:
    events = req.events if req.events else ALL_EVENTS

    with session.begin():
        existing = session.execute(
            select(WebhookConfig).where(WebhookConfig.account_id == current["id"])
        ).scalar_one_or_none()

        if existing is not None:
            existing.url = req.url
            existing.events = events
            existing.active = True
            session.add(existing)
            return WebhookResponse(
                webhook_url=existing.url,
                secret=None,
                events=existing.events,
                active=True,
            )

        webhook_secret = f"whsec_{secrets.token_hex(24)}"
        cfg = WebhookConfig(
            account_id=current["id"],
            url=req.url,
            secret=webhook_secret,
            events=events,
            active=True,
        )
        session.add(cfg)

    return WebhookResponse(
        webhook_url=cfg.url,
        secret=webhook_secret,
        events=cfg.events,
        active=True,
    )


@router.delete("/accounts/webhook", response_model=WebhookDeleteResponse, tags=["Webhooks"])
def delete_webhook(
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> WebhookDeleteResponse:
    with session.begin():
        existing = session.execute(
            select(WebhookConfig).where(WebhookConfig.account_id == current["id"])
        ).scalar_one_or_none()
        if existing is None:
            raise HTTPException(status_code=404, detail="No webhook configured")
        session.delete(existing)
    return WebhookDeleteResponse(status="removed")
