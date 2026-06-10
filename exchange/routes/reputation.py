from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from exchange.config import get_session, settings
from exchange.models import Account
from exchange.ratelimit import limiter
from exchange.reputation_metrics import EMA_LAMBDA, compute_reputation_metrics
from exchange.schemas import SettlementReputationResponse

router = APIRouter()


@router.get(
    "/reputation/{agent_id}",
    response_model=SettlementReputationResponse,
    tags=["Reputation"],
)
@limiter.limit(settings.rate_limit_public)
def get_reputation(
    request: Request,
    agent_id: str,
    session: Session = Depends(get_session),
) -> SettlementReputationResponse:
    """Return settlement-grounded reputation for an agent.

    Public endpoint — no authentication required. Composite reputation
    systems may ingest this payload as one input among many.
    """
    with session.begin():
        acct = session.execute(
            select(Account).where(Account.id == agent_id)
        ).scalar_one_or_none()
        if acct is None:
            raise HTTPException(status_code=404, detail="Account not found")

        metrics = compute_reputation_metrics(session, acct)
        base = str(request.base_url).rstrip("/")
        exchange_id = getattr(settings, "exchange_id", "a2a-se-default")

        return SettlementReputationResponse(
            agent_id=acct.id,
            bot_name=acct.bot_name,
            score=metrics.score,
            lambda_=EMA_LAMBDA,
            task_count=metrics.task_count,
            dispute_rate=metrics.dispute_rate,
            settlement_volume=metrics.settlement_volume,
            window_days=metrics.window_days,
            window_start=metrics.window_start,
            source="settlement-grounded",
            attestation_type="urn:a2a-settlement:ema-reputation:v1",
            attestation_url=f"{base}/v1/exchange/attestation/{acct.id}",
            issued_at=metrics.issued_at,
            exchange_id=exchange_id,
            exchange_url=base,
        )
