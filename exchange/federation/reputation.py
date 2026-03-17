"""Federated reputation engine extension.

Extends the core EMA reputation model to accept cross-exchange VCs
with Trust Discount applied.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from exchange.models import Account


EMA_LAMBDA = 0.1
LOCAL_RHO = 1.0  # local reputation always weighted at full parity


def compute_federated_reputation(
    local_reputation: float,
    federated_attestations: list[dict],
    local_weight: float = 0.7,
) -> float:
    """Compute a blended reputation from local and federated sources.

    Parameters
    ----------
    local_reputation:
        The agent's locally-computed EMA reputation (0.0–1.0).
    federated_attestations:
        List of dicts with keys ``effective_reputation`` and ``weight``
        (e.g., from FederatedAttestation records).
    local_weight:
        Weighting factor for local reputation vs federated.
        Default 0.7 means 70% local, 30% federated.
    """
    if not federated_attestations:
        return local_reputation

    fed_scores = []
    fed_weights = []
    for att in federated_attestations:
        eff_rep = att.get("effective_reputation")
        weight = att.get("weight", 1.0)
        if eff_rep is not None:
            fed_scores.append(eff_rep * weight)
            fed_weights.append(weight)

    if not fed_scores or sum(fed_weights) == 0:
        return local_reputation

    fed_avg = sum(fed_scores) / sum(fed_weights)

    blended = (local_reputation * local_weight) + (
        fed_avg * (1.0 - local_weight)
    )
    return max(0.0, min(1.0, blended))


def apply_ema_update(
    current_reputation: float,
    outcome: float,
    lam: float = EMA_LAMBDA,
) -> float:
    """Apply a single EMA update step.

    Parameters
    ----------
    current_reputation:
        Current reputation score (0.0–1.0).
    outcome:
        1.0 for success, 0.0 for failure.
    lam:
        EMA smoothing factor.
    """
    return current_reputation * (1.0 - lam) + outcome * lam


def get_federated_reputation_for_agent(
    session: Session,
    agent_did: str,
) -> list[dict]:
    """Retrieve active federated attestations for an agent.

    Returns list of dicts with effective_reputation and weight.
    """
    from exchange.federation.models import FederatedAttestation

    results = session.execute(
        select(FederatedAttestation).where(
            FederatedAttestation.agent_did == agent_did,
            FederatedAttestation.is_active == True,
            FederatedAttestation.attestation_type == "ReputationAttestation",
        )
    ).scalars().all()

    return [
        {
            "effective_reputation": att.effective_reputation,
            "native_reputation": att.native_reputation,
            "trust_discount_rho": att.trust_discount_rho,
            "source_exchange": att.source_exchange_did,
            "weight": 1.0,
        }
        for att in results
        if att.effective_reputation is not None
    ]
