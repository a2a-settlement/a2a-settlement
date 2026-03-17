"""Dashboard administration routes.

Provides the API surface consumed by the a2a-settlement-dashboard:
- Overview metrics, spending time series, alerts
- Agent listing, detail, suspend/unsuspend
- Escrow listing, detail, force-refund
- Dispute listing, detail, override resolution
- Token revocation

All endpoints require operator-level authentication (either an account with
status == "operator" or a matching dashboard API key).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import and_, func as sa_func, or_, select
from sqlalchemy.orm import Session

from exchange.auth import authenticate_bot
from exchange.config import get_session, settings
from exchange.models import Account, Balance, Escrow, Transaction
from exchange.webhooks import fire_webhook_event

router = APIRouter(tags=["Dashboard"])


# ---------------------------------------------------------------------------
# Auth helper: require operator-level access
# ---------------------------------------------------------------------------


def _require_operator(current: dict) -> None:
    if current.get("status") != "operator":
        raise HTTPException(status_code=403, detail="Dashboard endpoints require operator privileges")


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


@router.get("/dashboard/overview")
def dashboard_overview(
    activity_limit: int = 20,
    session: Session = Depends(get_session),
    authorization: str | None = Header(default=None),
):
    """Aggregate overview for the dashboard home page."""
    _check_dashboard_key(authorization)

    with session.begin():
        total_agents = session.execute(select(sa_func.count(Account.id))).scalar_one()
        active_escrows = session.execute(
            select(sa_func.count(Escrow.id)).where(Escrow.status.in_(("held", "disputed")))
        ).scalar_one()

        since_24h = datetime.now(timezone.utc) - timedelta(hours=24)
        volume_24h = session.execute(
            select(sa_func.coalesce(sa_func.sum(Transaction.amount), 0)).where(
                Transaction.created_at > since_24h
            )
        ).scalar_one()

        total_escrows = session.execute(select(sa_func.count(Escrow.id))).scalar_one()
        disputed_count = session.execute(
            select(sa_func.count(Escrow.id)).where(Escrow.status.in_(("disputed",)))
        ).scalar_one()
        dispute_rate = float(disputed_count) / float(total_escrows) if total_escrows > 0 else 0.0

        recent_txs = (
            session.execute(
                select(Transaction)
                .order_by(Transaction.created_at.desc())
                .limit(activity_limit)
            )
            .scalars()
            .all()
        )

    recent_activity = [
        {
            "id": tx.id,
            "type": tx.tx_type,
            "timestamp": tx.created_at.isoformat() if tx.created_at else "",
            "agent_id": tx.from_account or tx.to_account or "",
            "amount": int(tx.amount),
            "escrow_id": tx.escrow_id,
            "description": tx.description or "",
        }
        for tx in recent_txs
    ]

    return {
        "total_agents": int(total_agents),
        "active_escrows": int(active_escrows),
        "volume_24h": int(volume_24h),
        "dispute_rate": round(dispute_rate, 4),
        "recent_activity": recent_activity,
    }


# ---------------------------------------------------------------------------
# Spending time series
# ---------------------------------------------------------------------------


@router.get("/dashboard/spending")
def dashboard_spending(
    range: str = "7d",
    agent_id: str | None = None,
    session: Session = Depends(get_session),
    authorization: str | None = Header(default=None),
):
    _check_dashboard_key(authorization)

    days = {"24h": 1, "7d": 7, "30d": 30}.get(range, 7)
    now = datetime.now(timezone.utc)
    points = []

    for i in range(days, 0, -1):
        day_start = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)

        with session.begin():
            stmt = select(
                sa_func.coalesce(sa_func.sum(Transaction.amount), 0),
                sa_func.count(Transaction.id),
            ).where(
                and_(Transaction.created_at >= day_start, Transaction.created_at < day_end)
            )
            if agent_id:
                stmt = stmt.where(
                    or_(Transaction.from_account == agent_id, Transaction.to_account == agent_id)
                )
            row = session.execute(stmt).one()

        points.append({
            "date": day_start.strftime("%Y-%m-%d"),
            "volume": int(row[0]),
            "count": int(row[1]),
        })

    return points


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


@router.get("/dashboard/alerts")
def dashboard_alerts(
    session: Session = Depends(get_session),
    authorization: str | None = Header(default=None),
):
    _check_dashboard_key(authorization)

    alerts = []
    with session.begin():
        disputed = session.execute(
            select(Escrow).where(Escrow.status == "disputed").order_by(Escrow.created_at.desc()).limit(10)
        ).scalars().all()
        for esc in disputed:
            alerts.append({
                "id": f"alert-dispute-{esc.id[:8]}",
                "type": "dispute",
                "severity": "critical",
                "agent_id": esc.requester_id,
                "message": f"Dispute on escrow {esc.id[:8]}… — {esc.dispute_reason or 'No reason'}",
                "timestamp": (esc.created_at or datetime.now(timezone.utc)).isoformat(),
                "link": f"/escrows/{esc.id}",
            })

        low_rep = session.execute(
            select(Account).where(and_(Account.status == "active", Account.reputation < 0.3))
        ).scalars().all()
        for acct in low_rep:
            alerts.append({
                "id": f"alert-rep-{acct.id[:8]}",
                "type": "reputation_drop",
                "severity": "warning",
                "agent_id": acct.id,
                "message": f"Agent {acct.bot_name} reputation dropped to {acct.reputation:.2f}",
                "timestamp": (acct.updated_at or datetime.now(timezone.utc)).isoformat(),
                "link": f"/agents/{acct.id}",
            })

    return alerts


# ---------------------------------------------------------------------------
# Agent management
# ---------------------------------------------------------------------------


@router.get("/agents")
def list_agents(
    org: str | None = None,
    status: str | None = None,
    search: str | None = None,
    min_reputation: float | None = None,
    max_reputation: float | None = None,
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_session),
    authorization: str | None = Header(default=None),
):
    _check_dashboard_key(authorization)

    with session.begin():
        stmt = select(Account, Balance).outerjoin(Balance, Balance.account_id == Account.id)
        if status:
            stmt = stmt.where(Account.status == status)
        if org:
            stmt = stmt.where(Account.developer_id == org)
        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(or_(Account.bot_name.ilike(pattern), Account.id.ilike(pattern)))
        if min_reputation is not None:
            stmt = stmt.where(Account.reputation >= min_reputation)
        if max_reputation is not None:
            stmt = stmt.where(Account.reputation <= max_reputation)

        count = session.execute(
            select(sa_func.count()).select_from(stmt.subquery())
        ).scalar_one()

        rows = session.execute(
            stmt.order_by(Account.created_at.desc()).limit(limit).offset(offset)
        ).all()

    agents = []
    for acct, bal in rows:
        agents.append({
            "id": acct.id,
            "org_id": acct.developer_id,
            "balance": int(bal.available) if bal else 0,
            "reputation": float(acct.reputation),
            "total_transactions": int((bal.total_earned or 0) + (bal.total_spent or 0)) if bal else 0,
            "status": acct.status,
            "last_active": (acct.updated_at or acct.created_at or datetime.now(timezone.utc)).isoformat(),
            "created_at": acct.created_at.isoformat() if acct.created_at else None,
        })

    return {"agents": agents, "total": int(count)}


@router.get("/agents/{agent_id}")
def get_agent_detail(
    agent_id: str,
    session: Session = Depends(get_session),
    authorization: str | None = Header(default=None),
):
    _check_dashboard_key(authorization)

    with session.begin():
        acct = session.execute(select(Account).where(Account.id == agent_id)).scalar_one_or_none()
        if acct is None:
            raise HTTPException(status_code=404, detail="Agent not found")
        bal = session.execute(select(Balance).where(Balance.account_id == agent_id)).scalar_one_or_none()

    return {
        "id": acct.id,
        "org_id": acct.developer_id,
        "balance": int(bal.available) if bal else 0,
        "reputation": float(acct.reputation),
        "total_transactions": int((bal.total_earned or 0) + (bal.total_spent or 0)) if bal else 0,
        "status": acct.status,
        "last_active": (acct.updated_at or acct.created_at or datetime.now(timezone.utc)).isoformat(),
        "created_at": acct.created_at.isoformat() if acct.created_at else None,
        "skills": acct.skills or [],
        "description": acct.description,
        "daily_spend_limit": acct.daily_spend_limit,
    }


@router.post("/dashboard/agents/{agent_id}/suspend")
def suspend_agent(
    agent_id: str,
    session: Session = Depends(get_session),
    authorization: str | None = Header(default=None),
):
    _check_dashboard_key(authorization)

    with session.begin():
        acct = session.execute(select(Account).where(Account.id == agent_id)).scalar_one_or_none()
        if acct is None:
            raise HTTPException(status_code=404, detail="Agent not found")
        if acct.status == "operator":
            raise HTTPException(status_code=400, detail="Cannot suspend an operator account")
        acct.status = "suspended"
        session.add(acct)

    return {"agent_id": agent_id, "status": "suspended"}


@router.post("/dashboard/agents/{agent_id}/unsuspend")
def unsuspend_agent(
    agent_id: str,
    session: Session = Depends(get_session),
    authorization: str | None = Header(default=None),
):
    _check_dashboard_key(authorization)

    with session.begin():
        acct = session.execute(select(Account).where(Account.id == agent_id)).scalar_one_or_none()
        if acct is None:
            raise HTTPException(status_code=404, detail="Agent not found")
        if acct.status != "suspended":
            raise HTTPException(status_code=400, detail="Agent is not suspended")
        acct.status = "active"
        session.add(acct)

    return {"agent_id": agent_id, "status": "active"}


# ---------------------------------------------------------------------------
# Escrow management
# ---------------------------------------------------------------------------


@router.get("/escrows")
def list_all_escrows(
    status: str | None = None,
    agent_id: str | None = None,
    min_amount: int | None = None,
    max_amount: int | None = None,
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_session),
    authorization: str | None = Header(default=None),
):
    """List all escrows (admin view, not scoped to a single agent)."""
    _check_dashboard_key(authorization)

    with session.begin():
        stmt = select(Escrow)
        if status:
            stmt = stmt.where(Escrow.status == status)
        if agent_id:
            stmt = stmt.where(or_(Escrow.requester_id == agent_id, Escrow.provider_id == agent_id))
        if min_amount is not None:
            stmt = stmt.where(Escrow.amount >= min_amount)
        if max_amount is not None:
            stmt = stmt.where(Escrow.amount <= max_amount)

        count = session.execute(
            select(sa_func.count()).select_from(stmt.subquery())
        ).scalar_one()

        rows = session.execute(
            stmt.order_by(Escrow.created_at.desc()).limit(limit).offset(offset)
        ).scalars().all()

    escrows = [
        {
            "id": e.id,
            "requester_id": e.requester_id,
            "provider_id": e.provider_id,
            "amount": int(e.amount),
            "fee_amount": int(e.fee_amount),
            "status": e.status,
            "created_at": e.created_at.isoformat() if e.created_at else "",
            "updated_at": (e.resolved_at or e.created_at or datetime.now(timezone.utc)).isoformat(),
            "expires_at": e.expires_at.isoformat() if e.expires_at else None,
            "task_id": e.task_id,
        }
        for e in rows
    ]
    return {"escrows": escrows, "total": int(count)}


@router.get("/escrows/{escrow_id}")
def get_escrow_detail(
    escrow_id: str,
    session: Session = Depends(get_session),
    authorization: str | None = Header(default=None),
):
    _check_dashboard_key(authorization)

    with session.begin():
        escrow = session.execute(select(Escrow).where(Escrow.id == escrow_id)).scalar_one_or_none()
        if escrow is None:
            raise HTTPException(status_code=404, detail="Escrow not found")

    return {
        "id": escrow.id,
        "requester_id": escrow.requester_id,
        "provider_id": escrow.provider_id,
        "amount": int(escrow.amount),
        "fee_amount": int(escrow.fee_amount),
        "status": escrow.status,
        "dispute_reason": escrow.dispute_reason,
        "resolution_strategy": escrow.resolution_strategy,
        "created_at": escrow.created_at.isoformat() if escrow.created_at else "",
        "updated_at": (escrow.resolved_at or escrow.created_at or datetime.now(timezone.utc)).isoformat(),
        "resolved_at": escrow.resolved_at.isoformat() if escrow.resolved_at else None,
        "expires_at": escrow.expires_at.isoformat() if escrow.expires_at else None,
        "task_id": escrow.task_id,
        "group_id": escrow.group_id,
        "depends_on": escrow.depends_on,
    }


@router.post("/dashboard/escrows/{escrow_id}/force-refund")
def force_refund(
    escrow_id: str,
    session: Session = Depends(get_session),
    authorization: str | None = Header(default=None),
):
    """Force-refund an escrow as operator (regardless of who is the requester)."""
    _check_dashboard_key(authorization)

    with session.begin():
        escrow = session.execute(select(Escrow).where(Escrow.id == escrow_id)).scalar_one_or_none()
        if escrow is None:
            raise HTTPException(status_code=404, detail="Escrow not found")
        if escrow.status not in ("held", "disputed"):
            raise HTTPException(status_code=400, detail=f"Cannot refund escrow with status: {escrow.status}")

        total_held = int(escrow.amount + escrow.fee_amount)
        requester_bal = session.execute(
            select(Balance).where(Balance.account_id == escrow.requester_id)
        ).scalar_one_or_none()
        if requester_bal is None:
            raise HTTPException(status_code=404, detail="Requester balance not found")

        requester_bal.available += total_held
        requester_bal.held_in_escrow -= total_held
        session.add(requester_bal)

        escrow.status = "refunded"
        escrow.resolved_at = datetime.now(timezone.utc)
        escrow.resolution_strategy = "operator_force_refund"
        session.add(escrow)

        session.add(
            Transaction(
                escrow_id=escrow.id,
                from_account=None,
                to_account=escrow.requester_id,
                amount=total_held,
                tx_type="escrow_refund",
                description="Force refund by operator",
            )
        )

    return {"escrow_id": escrow_id, "status": "refunded", "amount_returned": total_held}


# ---------------------------------------------------------------------------
# Disputes
# ---------------------------------------------------------------------------


@router.get("/disputes")
def list_disputes(
    status: str | None = None,
    agent_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_session),
    authorization: str | None = Header(default=None),
):
    _check_dashboard_key(authorization)

    with session.begin():
        stmt = select(Escrow).where(Escrow.status.in_(("disputed", "released", "refunded")))
        stmt = stmt.where(Escrow.dispute_reason.isnot(None))

        if status:
            status_map = {"open": "disputed", "resolved": ("released", "refunded")}
            mapped = status_map.get(status, status)
            if isinstance(mapped, tuple):
                stmt = stmt.where(Escrow.status.in_(mapped))
            else:
                stmt = stmt.where(Escrow.status == mapped)
        if agent_id:
            stmt = stmt.where(or_(Escrow.requester_id == agent_id, Escrow.provider_id == agent_id))

        count = session.execute(
            select(sa_func.count()).select_from(stmt.subquery())
        ).scalar_one()

        rows = session.execute(
            stmt.order_by(Escrow.created_at.desc()).limit(limit).offset(offset)
        ).scalars().all()

    disputes = []
    for esc in rows:
        d_status = "open" if esc.status == "disputed" else "resolved"
        resolution = None
        if esc.status == "released":
            resolution = "release"
        elif esc.status == "refunded":
            resolution = "refund"

        disputes.append({
            "id": f"dispute-{esc.id}",
            "escrow_id": esc.id,
            "filed_by": esc.requester_id,
            "against": esc.provider_id,
            "reason": esc.dispute_reason or "",
            "status": d_status,
            "filed_at": esc.created_at.isoformat() if esc.created_at else "",
            "resolution": resolution,
            "resolved_at": esc.resolved_at.isoformat() if esc.resolved_at else None,
        })

    return {"disputes": disputes, "total": int(count)}


@router.get("/disputes/{dispute_id}")
def get_dispute_detail(
    dispute_id: str,
    session: Session = Depends(get_session),
    authorization: str | None = Header(default=None),
):
    _check_dashboard_key(authorization)

    escrow_id = dispute_id.removeprefix("dispute-")

    with session.begin():
        esc = session.execute(select(Escrow).where(Escrow.id == escrow_id)).scalar_one_or_none()
        if esc is None:
            raise HTTPException(status_code=404, detail="Dispute not found")

    d_status = "open" if esc.status == "disputed" else "resolved"
    resolution = None
    if esc.status == "released":
        resolution = "release"
    elif esc.status == "refunded":
        resolution = "refund"

    return {
        "id": f"dispute-{esc.id}",
        "escrow_id": esc.id,
        "filed_by": esc.requester_id,
        "against": esc.provider_id,
        "reason": esc.dispute_reason or "",
        "status": d_status,
        "filed_at": esc.created_at.isoformat() if esc.created_at else "",
        "resolution": resolution,
        "resolved_at": esc.resolved_at.isoformat() if esc.resolved_at else None,
    }


@router.post("/disputes/{dispute_id}/resolve")
def override_dispute_resolution(
    dispute_id: str,
    body: dict,
    session: Session = Depends(get_session),
    authorization: str | None = Header(default=None),
):
    _check_dashboard_key(authorization)

    escrow_id = dispute_id.removeprefix("dispute-")
    resolution = body.get("resolution")
    if resolution not in ("release", "refund"):
        raise HTTPException(status_code=400, detail="resolution must be 'release' or 'refund'")

    with session.begin():
        escrow = session.execute(select(Escrow).where(Escrow.id == escrow_id)).scalar_one_or_none()
        if escrow is None:
            raise HTTPException(status_code=404, detail="Escrow not found")
        if escrow.status != "disputed":
            raise HTTPException(status_code=400, detail=f"Escrow is not disputed (status: {escrow.status})")

        total_held = int(escrow.amount + escrow.fee_amount)
        escrow.resolution_strategy = "dashboard_override"

        if resolution == "release":
            requester_bal = session.execute(select(Balance).where(Balance.account_id == escrow.requester_id)).scalar_one_or_none()
            provider_bal = session.execute(select(Balance).where(Balance.account_id == escrow.provider_id)).scalar_one_or_none()
            if not requester_bal or not provider_bal:
                raise HTTPException(status_code=404, detail="Balance not found")

            requester_bal.held_in_escrow -= total_held
            requester_bal.total_spent += total_held
            provider_bal.available += int(escrow.amount)
            provider_bal.total_earned += int(escrow.amount)
            escrow.status = "released"
            escrow.resolved_at = datetime.now(timezone.utc)

            session.add(Transaction(
                escrow_id=escrow.id, from_account=escrow.requester_id,
                to_account=escrow.provider_id, amount=int(escrow.amount),
                tx_type="escrow_release", description="Dashboard override — released",
            ))
        else:
            requester_bal = session.execute(select(Balance).where(Balance.account_id == escrow.requester_id)).scalar_one_or_none()
            if not requester_bal:
                raise HTTPException(status_code=404, detail="Balance not found")

            requester_bal.available += total_held
            requester_bal.held_in_escrow -= total_held
            escrow.status = "refunded"
            escrow.resolved_at = datetime.now(timezone.utc)

            session.add(Transaction(
                escrow_id=escrow.id, from_account=None,
                to_account=escrow.requester_id, amount=total_held,
                tx_type="escrow_refund", description="Dashboard override — refunded",
            ))

    return {"escrow_id": escrow_id, "resolution": resolution, "status": escrow.status}


# ---------------------------------------------------------------------------
# Token revocation
# ---------------------------------------------------------------------------


@router.post("/dashboard/tokens/{jti}/revoke")
def revoke_token(
    jti: str,
    authorization: str | None = Header(default=None),
):
    """Revoke a settlement token by JTI.

    If the SettlementMiddleware is active and has a SpendingTracker with
    revocation support, the token is revoked there as well.
    """
    _check_dashboard_key(authorization)

    revoked_in_store = False
    try:
        from exchange.app import app as _app
        for mw in getattr(_app, "user_middleware", []):
            if hasattr(mw, "cls") and mw.cls.__name__ == "SettlementMiddleware":
                import asyncio
                tracker = None
                for m in _app.middleware_stack.__dict__.get("app", _app).__dict__.values():
                    if hasattr(m, "spending_tracker"):
                        tracker = m.spending_tracker
                        break
                if tracker:
                    asyncio.get_event_loop().run_until_complete(tracker.revoke(jti))
                    revoked_in_store = True
                break
    except Exception:
        pass

    return {"jti": jti, "status": "revoked", "revoked_in_store": revoked_in_store}


# ---------------------------------------------------------------------------
# Federation (dashboard view — requires federation_enabled)
# ---------------------------------------------------------------------------


@router.get("/federation/peers")
def list_federation_peers(
    session: Session = Depends(get_session),
    authorization: str | None = Header(default=None),
):
    """List federation peers for the dashboard. Returns empty when federation disabled."""
    _check_dashboard_key(authorization)

    if not getattr(settings, "federation_enabled", False):
        return {"peers": [], "total": 0}

    try:
        from exchange.federation.models import FederationPeer

        with session.begin():
            rows = session.execute(
                select(FederationPeer).order_by(FederationPeer.peered_at.desc())
            ).scalars().all()

        peers = []
        for p in rows:
            peers.append({
                "id": p.id,
                "peer_did": p.peer_did,
                "name": p.name,
                "operator": p.operator,
                "peering_id": p.peering_id,
                "peered_at": p.peered_at.isoformat() if p.peered_at else "",
                "status": p.status,
                "current_rho": float(p.current_rho or 0.15),
                "rho_updated_at": p.rho_updated_at.isoformat() if p.rho_updated_at else None,
                "health_status": p.health_status or "unknown",
                "last_health_check": p.last_health_check.isoformat() if p.last_health_check else None,
                "uptime_90d": float(p.uptime_90d) if p.uptime_90d is not None else None,
                "avg_attestation_latency_ms": p.avg_attestation_latency_ms,
            })
        return {"peers": peers, "total": len(peers)}
    except Exception:
        return {"peers": [], "total": 0}


@router.get("/federation/health")
def get_federation_health(
    session: Session = Depends(get_session),
    authorization: str | None = Header(default=None),
):
    """Return local exchange federation health for the dashboard."""
    _check_dashboard_key(authorization)

    node_did = getattr(settings, "federation_node_did", "")
    enabled = getattr(settings, "federation_enabled", False)
    active_peers = 0

    if enabled:
        try:
            from exchange.federation.models import FederationPeer

            with session.begin():
                result = session.execute(
                    select(sa_func.count(FederationPeer.id)).where(
                        FederationPeer.status == "active"
                    )
                )
                active_peers = result.scalar_one() or 0
        except Exception:
            pass

    return {
        "status": "unavailable" if not enabled else "operational",
        "node_did": node_did,
        "avg_attestation_latency_ms": 0,
        "uptime_90d": 1.0,
        "version": getattr(settings, "version", "0.10.0"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "active_peers": active_peers,
        "federation_protocol_version": "0.1.0",
    }


@router.get("/federation/trust-policy")
def get_trust_policy(authorization: str | None = Header(default=None)):
    """Return local exchange Trust Discount policy for the dashboard."""
    _check_dashboard_key(authorization)

    algorithm_id = getattr(
        settings,
        "trust_discount_algorithm",
        "urn:a2a:trust:discount:linear-volume-weighted-v1",
    )
    params = getattr(settings, "trust_discount_params", {}) or {}
    initial_rho = getattr(settings, "trust_discount_initial_rho", 0.15)

    return {
        "algorithm_id": algorithm_id,
        "initial_rho": initial_rho,
        "max_rho": params.get("max_rho", 1.0),
        "attestation_success_floor": params.get("attestation_success_floor"),
        "review_cadence_days": params.get("review_cadence_days"),
        "parameters": params,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _check_dashboard_key(authorization: str | None) -> None:
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    token = authorization.removeprefix("Bearer ").strip()
    if settings.dashboard_api_key:
        if token == settings.dashboard_api_key:
            return
    if settings.operator_api_key and token == settings.operator_api_key:
        return
    raise HTTPException(status_code=403, detail="Invalid dashboard credentials")
