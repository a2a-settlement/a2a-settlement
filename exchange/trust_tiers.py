"""Shadow-mode trust tier classification for escrow creation.

Thresholds are provisional (see docs/trust-tiers.md). Shadow mode records
labels on escrows and logs them; it never rejects transactions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from exchange.models import Account
from exchange.reputation_metrics import ReputationMetrics, compute_reputation_metrics

logger = logging.getLogger(__name__)

STATE_REGISTERED = "registered"
STATE_PROVEN = "proven"


@dataclass(frozen=True)
class TrustTierSnapshot:
    requester_trust_state: str
    provider_trust_state: str
    requester_task_count: int
    requester_dispute_rate: float
    provider_task_count: int
    provider_dispute_rate: float


def classify_trust_state(metrics: ReputationMetrics) -> str:
    """Map windowed reputation metrics to a shadow trust label."""
    if (
        metrics.task_count >= 10
        and metrics.score >= 0.6
        and metrics.dispute_rate < 0.2
    ):
        return STATE_PROVEN
    return STATE_REGISTERED


def snapshot_trust_tiers(
    session: Session,
    requester: Account,
    provider: Account,
) -> TrustTierSnapshot:
    req_m = compute_reputation_metrics(session, requester)
    prov_m = compute_reputation_metrics(session, provider)
    return TrustTierSnapshot(
        requester_trust_state=classify_trust_state(req_m),
        provider_trust_state=classify_trust_state(prov_m),
        requester_task_count=req_m.task_count,
        requester_dispute_rate=req_m.dispute_rate,
        provider_task_count=prov_m.task_count,
        provider_dispute_rate=prov_m.dispute_rate,
    )


def log_trust_tier_shadow(
    *,
    escrow_id: str,
    amount: int,
    snapshot: TrustTiersSnapshot,
) -> None:
    logger.info(
        "trust_tier_shadow escrow_id=%s amount=%s "
        "requester_state=%s requester_tasks=%s requester_dispute_rate=%s "
        "provider_state=%s provider_tasks=%s provider_dispute_rate=%s",
        escrow_id,
        amount,
        snapshot.requester_trust_state,
        snapshot.requester_task_count,
        snapshot.requester_dispute_rate,
        snapshot.provider_trust_state,
        snapshot.provider_task_count,
        snapshot.provider_dispute_rate,
    )
