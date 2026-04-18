from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from exchange.config import get_session
from exchange.models import Account, Balance, Escrow, Transaction
from exchange.schemas import (
    StatsActivity,
    StatsComplianceInfo,
    StatsNetworkInfo,
    StatsProvenanceInfo,
    StatsResponse,
    StatsSettlementOutcomes,
    StatsTokenSupply,
    StatsTreasury,
)


router = APIRouter()


@router.get("/stats", response_model=StatsResponse, tags=["Stats"])
def stats(session: Session = Depends(get_session)) -> StatsResponse:
    with session.begin():
        total_bots = session.execute(select(func.count(Account.id))).scalar_one()
        active_bots = session.execute(
            select(func.count(Account.id)).where(Account.status == "active")
        ).scalar_one()

        circulating = session.execute(
            select(func.coalesce(func.sum(Balance.available), 0))
        ).scalar_one()
        in_escrow = session.execute(
            select(func.coalesce(func.sum(Balance.held_in_escrow), 0))
        ).scalar_one()
        total_supply = session.execute(
            select(
                func.coalesce(func.sum(Balance.available + Balance.held_in_escrow), 0)
            )
        ).scalar_one()

        since = datetime.now(timezone.utc) - timedelta(hours=24)
        tx_count_24h = session.execute(
            select(func.count(Transaction.id)).where(Transaction.created_at > since)
        ).scalar_one()
        tx_volume_24h = session.execute(
            select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                Transaction.created_at > since
            )
        ).scalar_one()

        fees_collected = session.execute(
            select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                Transaction.tx_type == "fee"
            )
        ).scalar_one()

        active_escrows = session.execute(
            select(func.count(Escrow.id)).where(Escrow.status == "held")
        ).scalar_one()

        total_delivered = session.execute(
            select(func.count(Escrow.id)).where(Escrow.delivered_at.isnot(None))
        ).scalar_one()
        with_provenance = session.execute(
            select(func.count(Escrow.id)).where(Escrow.provenance.isnot(None))
        ).scalar_one()
        total_verified = session.execute(
            select(func.count(Escrow.id)).where(Escrow.provenance_result.isnot(None))
        ).scalar_one()
        from exchange.config import settings

        if settings.database_url.startswith("sqlite"):
            fab_filter = func.json_extract(Escrow.provenance_result, "$.verified") == False  # noqa: E712
        else:
            fab_filter = Escrow.provenance_result.op("->>")("verified") == "false"
        fabrication_detected = session.execute(
            select(func.count(Escrow.id)).where(
                Escrow.provenance_result.isnot(None),
                fab_filter,
            )
        ).scalar_one()

        partial_releases = session.execute(
            select(func.count(Escrow.id)).where(Escrow.released_amount.isnot(None))
        ).scalar_one()
        pending_efficacy = session.execute(
            select(func.count(Escrow.id)).where(
                Escrow.status == "partially_released",
                Escrow.efficacy_check_at.isnot(None),
            )
        ).scalar_one()

        tx_released = session.execute(
            select(func.count(Transaction.id)).where(Transaction.tx_type == "escrow_release")
        ).scalar_one()
        tx_refunded = session.execute(
            select(func.count(Transaction.id)).where(Transaction.tx_type == "escrow_refund")
        ).scalar_one()
        tx_partial = session.execute(
            select(func.count(Transaction.id)).where(Transaction.tx_type == "escrow_partial_release")
        ).scalar_one()
        tx_held = session.execute(
            select(func.count(Transaction.id)).where(Transaction.tx_type == "escrow_hold")
        ).scalar_one()

    denom = int(total_supply) or 1
    velocity = float(tx_volume_24h) / float(denom)

    from exchange.compliance_log import get_tree_status

    compliance_data = get_tree_status()
    compliance = StatsComplianceInfo(
        enabled=compliance_data.get("enabled", False),
        leaf_count=compliance_data.get("leaf_count", 0),
        root_hash=compliance_data.get("root_hash"),
    )

    provenance = StatsProvenanceInfo(
        total_delivered=int(total_delivered),
        with_provenance=int(with_provenance),
        total_verified=int(total_verified),
        fabrication_detected=int(fabrication_detected),
        partial_releases=int(partial_releases),
        pending_efficacy_reviews=int(pending_efficacy),
    )

    outcomes = StatsSettlementOutcomes(
        released=int(tx_released),
        refunded=int(tx_refunded),
        partial=int(tx_partial),
        held=int(tx_held),
    )

    return StatsResponse(
        network=StatsNetworkInfo(
            total_bots=int(total_bots), active_bots=int(active_bots)
        ),
        token_supply=StatsTokenSupply(
            circulating=int(circulating),
            in_escrow=int(in_escrow),
            total=int(total_supply),
        ),
        activity_24h=StatsActivity(
            transaction_count=int(tx_count_24h),
            token_volume=int(tx_volume_24h),
            velocity=float(f"{velocity:.4f}"),
        ),
        treasury=StatsTreasury(fees_collected=int(fees_collected)),
        active_escrows=int(active_escrows),
        compliance=compliance,
        provenance=provenance,
        settlement_outcomes=outcomes,
    )


_ESCROW_STATUS_TO_OUTCOME = {
    "released": "approve",
    "refunded": "block",
    "partially_released": "flag",
    "held": "pending",
    "expired": "block",
    "disputed": "flag",
}


@router.get("/stats/recent-activity", tags=["Stats"])
def recent_activity(
    limit: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Public endpoint: recent escrow settlements with bot names resolved."""
    with session.begin():
        rows = session.execute(
            select(Escrow)
            .order_by(Escrow.created_at.desc())
            .limit(limit)
        ).scalars().all()

        agent_ids = set()
        for e in rows:
            agent_ids.add(e.requester_id)
            agent_ids.add(e.provider_id)

        names: dict[str, str] = {}
        if agent_ids:
            accts = session.execute(
                select(Account.id, Account.bot_name).where(Account.id.in_(agent_ids))
            ).all()
            names = {str(a.id): a.bot_name for a in accts}

    entries = []
    for e in rows:
        entries.append({
            "id": str(e.id),
            "timestamp": (e.resolved_at or e.created_at).isoformat() if (e.resolved_at or e.created_at) else "",
            "source_agent": names.get(e.requester_id, e.requester_id),
            "target_agent": names.get(e.provider_id, e.provider_id),
            "outcome": _ESCROW_STATUS_TO_OUTCOME.get(e.status, e.status),
            "escrow_id": str(e.id),
            "amount": int(e.amount),
            "status": e.status,
            "task_id": e.task_id,
        })
    return {"entries": entries, "total": len(entries)}
