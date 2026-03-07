from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
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
        fabrication_detected = session.execute(
            select(func.count(Escrow.id)).where(
                Escrow.provenance_result.isnot(None),
                Escrow.provenance_result["verified"].as_boolean() == False,  # noqa: E712
            )
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
    )
