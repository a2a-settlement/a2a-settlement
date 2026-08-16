from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, select
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


def run_diversity_sweep() -> dict:
    """Update counterparty diversity counters and payment-graph principal links.

    Two passes:
      1. For every account, compute unique_counterparties_90d, counterparty_hhi,
         and diversity_score from the transactions table.
      2. Walk each account's transaction graph to N hops (default 2). Pairs of
         accounts that share token flow within those hops receive an
         AgentPrincipalLink with link_source='payment_graph'.
    """
    from exchange.models import Account, AgentPrincipalLink, Transaction
    from exchange.principal_resolver import link_agent_to_principal

    session = SessionLocal()
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    updated = 0
    linked = 0

    try:
        with session.begin():
            accounts = session.execute(select(Account)).scalars().all()

            for acct in accounts:
                txns = session.execute(
                    select(Transaction).where(
                        and_(
                            Transaction.created_at >= cutoff,
                            Transaction.tx_type.in_([
                                "escrow_release", "escrow_refund",
                                "instant_settlement", "escrow_hold",
                            ]),
                        )
                    )
                ).scalars().all()

                # Collect counterparty IDs for this account
                counterparties: list[str] = []
                for tx in txns:
                    if tx.from_account == acct.id and tx.to_account:
                        counterparties.append(tx.to_account)
                    elif tx.to_account == acct.id and tx.from_account:
                        counterparties.append(tx.from_account)

                unique_count = len(set(counterparties))
                total = len(counterparties)

                if total > 0:
                    counts = Counter(counterparties)
                    hhi = sum((c / total) ** 2 for c in counts.values())
                    diversity = max(0.0, 1.0 - hhi)
                else:
                    hhi = None
                    diversity = None

                acct.unique_counterparties_90d = unique_count
                acct.counterparty_hhi = hhi
                acct.diversity_score = diversity
                session.add(acct)
                updated += 1

        # Payment-graph pass: link agents whose flows converge within N hops.
        # Currently uses hop distance 1 (direct transaction partners share flow).
        # Increase A2A_EXCHANGE_PAYMENT_GRAPH_HOPS for deeper analysis.
        hops = settings.payment_graph_hops
        if hops >= 1:
            with session.begin():
                txns_all = session.execute(
                    select(Transaction).where(
                        and_(
                            Transaction.created_at >= cutoff,
                            Transaction.from_account.isnot(None),
                            Transaction.to_account.isnot(None),
                        )
                    )
                ).scalars().all()

                # Build adjacency: account → set of accounts it transacted with
                adjacency: dict[str, set[str]] = {}
                for tx in txns_all:
                    adjacency.setdefault(tx.from_account, set()).add(tx.to_account)
                    adjacency.setdefault(tx.to_account, set()).add(tx.from_account)

                # BFS up to `hops` depth; pairs reachable within hops share a wallet flow.
                from exchange.models import Principal
                from exchange.principal_resolver import get_or_create_principal

                for origin, neighbors in adjacency.items():
                    reachable = set(neighbors)
                    if hops >= 2:
                        for n in list(neighbors):
                            reachable |= adjacency.get(n, set())
                        reachable.discard(origin)

                    if len(reachable) < 2:
                        continue

                    # Find or create a payment-graph principal for this cluster origin.
                    # Confidence scales with proximity: direct (hop 1) = 0.4, hop 2 = 0.25.
                    acct_obj = session.get(Account, origin)
                    if acct_obj is None:
                        continue

                    principal_id = get_or_create_principal(
                        acct_obj.developer_id,
                        acct_obj.kya_level_verified,
                        session,
                    )
                    for peer in reachable:
                        peer_obj = session.get(Account, peer)
                        if peer_obj is None:
                            continue
                        # Confidence hygiene: payment_graph caps at 0.4 and must never
                        # unlock reputation inheritance (see principal_resolver module doc).
                        confidence = 0.4 if peer in neighbors else 0.25
                        link_agent_to_principal(
                            peer, principal_id, "payment_graph", confidence, session
                        )
                        linked += 1

        # Pass 3: Sybil-smell risk_score on principals from low-confidence clusters.
        with session.begin():
            risked = _update_principal_risk_scores(session)

        logger.info(
            "Diversity sweep complete: %d accounts updated, %d payment-graph links written, "
            "%d principals risk-scored",
            updated, linked, risked,
        )
        return {
            "accounts_updated": updated,
            "payment_graph_links": linked,
            "principals_risk_scored": risked,
        }
    finally:
        session.close()


def _update_principal_risk_scores(session: Session) -> int:
    """Write Principal.risk_score from low-confidence peer Sybil smell.

    Heuristic (fixed for this phase — no reputation inheritance):
    - Count agents linked to the principal with confidence < 0.5.
    - If that count >= 5 and mean diversity_score of those agents is < 0.3,
      set risk_score = min(1.0, 0.2 * count).
    - Otherwise decay existing risk_score toward 0.
    """
    from exchange.models import Account, AgentPrincipalLink, Principal

    principals = session.execute(select(Principal)).scalars().all()
    updated = 0
    for principal in principals:
        rows = session.execute(
            select(AgentPrincipalLink, Account)
            .join(Account, Account.id == AgentPrincipalLink.agent_id)
            .where(
                and_(
                    AgentPrincipalLink.principal_id == principal.id,
                    AgentPrincipalLink.confidence < 0.5,
                )
            )
        ).all()
        low_conf_count = len(rows)
        diversities = [
            row.Account.diversity_score
            for row in rows
            if row.Account.diversity_score is not None
        ]
        mean_diversity = (
            sum(diversities) / len(diversities) if diversities else None
        )

        if (
            low_conf_count >= 5
            and mean_diversity is not None
            and mean_diversity < 0.3
        ):
            principal.risk_score = min(1.0, 0.2 * low_conf_count)
        else:
            current = float(principal.risk_score or 0.0)
            principal.risk_score = (
                round(current * 0.5, 4) if current > 0.001 else 0.0
            )
        session.add(principal)
        updated += 1
    return updated


async def background_diversity_loop() -> None:
    """Nightly: update counterparty diversity counters and payment-graph links."""
    interval = settings.diversity_sweep_interval_seconds
    logger.info("Background diversity loop started (interval=%ds)", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            counts = run_diversity_sweep()
            logger.info(
                "Diversity loop: accounts=%d payment_graph_links=%d",
                counts["accounts_updated"],
                counts["payment_graph_links"],
            )
        except Exception:
            logger.exception("Error in background diversity sweep")
