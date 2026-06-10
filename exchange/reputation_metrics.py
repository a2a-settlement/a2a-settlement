"""Settlement-grounded reputation metrics shared by public reputation endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func as sa_func, select
from sqlalchemy.orm import Session

from exchange.config import settings
from exchange.models import Account, Escrow

EMA_LAMBDA = 0.1
_COMPLETED_STATUSES = ("released", "refunded", "disputed", "partially_released")
_DISPUTE_STATUSES = ("disputed", "refunded")
_SETTLED_STATUSES = ("released", "partially_released")


@dataclass(frozen=True)
class ReputationMetrics:
    score: float
    task_count: int
    dispute_rate: float
    settlement_volume: int
    window_days: int
    window_start: datetime
    issued_at: datetime


def _now() -> datetime:
    return datetime.now(timezone.utc)


def compute_reputation_metrics(
    session: Session,
    account: Account,
    *,
    window_days: int | None = None,
) -> ReputationMetrics:
    """Compute EMA reputation and windowed settlement stats for an agent."""
    now = _now()
    days = window_days if window_days is not None else settings.attestation_ttl_reputation_days
    window_start = now - timedelta(days=days)

    total_completed = session.execute(
        select(sa_func.count())
        .select_from(Escrow)
        .where(
            Escrow.provider_id == account.id,
            Escrow.status.in_(_COMPLETED_STATUSES),
            Escrow.created_at >= window_start,
        )
    ).scalar_one()

    dispute_count = session.execute(
        select(sa_func.count())
        .select_from(Escrow)
        .where(
            Escrow.provider_id == account.id,
            Escrow.status.in_(_DISPUTE_STATUSES),
            Escrow.created_at >= window_start,
        )
    ).scalar_one()

    settlement_volume = session.execute(
        select(sa_func.coalesce(sa_func.sum(Escrow.amount), 0))
        .select_from(Escrow)
        .where(
            Escrow.provider_id == account.id,
            Escrow.status.in_(_SETTLED_STATUSES),
            Escrow.created_at >= window_start,
        )
    ).scalar_one()

    dispute_rate = dispute_count / total_completed if total_completed else 0.0

    return ReputationMetrics(
        score=round(float(account.reputation), 4),
        task_count=int(total_completed),
        dispute_rate=round(dispute_rate, 4),
        settlement_volume=int(settlement_volume or 0),
        window_days=days,
        window_start=window_start,
        issued_at=now,
    )
