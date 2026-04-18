"""Principal resolution for anti-self-dealing enforcement.

Maps multiple agent identities to a single principal entity and classifies
transactions before they enter the reputation/fee/mediator pipelines.

Resolution pipeline (in trust order):
  1. Registration-time attestation — cheapest signal, always available
  2. Payment-graph analysis — runs nightly via background_diversity_loop()
  3. Manual linkage — operator asserted, confidence 1.0
  4. Behavioral clustering — enum value reserved, no job built yet

The is_same_principal() result is cached in-process with a 5-minute TTL to
keep escrow creation sub-100ms on cache hits.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from exchange.models import Account, AgentPrincipalLink, Principal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-process cache: (agent_a, agent_b, time_bucket) → verdict dict
# time_bucket is floor(unix_time / CACHE_TTL_SECONDS), so entries expire
# naturally when the bucket changes. No background thread needed.
# ---------------------------------------------------------------------------

CACHE_TTL_SECONDS = 300  # 5 minutes
_CACHE: dict[tuple[str, str, int], dict] = {}


def _cache_key(agent_a: str, agent_b: str) -> tuple[str, str, int]:
    a, b = (agent_a, agent_b) if agent_a < agent_b else (agent_b, agent_a)
    bucket = int(time.monotonic() / CACHE_TTL_SECONDS)
    return (a, b, bucket)


def _cache_get(agent_a: str, agent_b: str) -> Optional[dict]:
    return _CACHE.get(_cache_key(agent_a, agent_b))


def _cache_set(agent_a: str, agent_b: str, result: dict) -> None:
    key = _cache_key(agent_a, agent_b)
    _CACHE[key] = result
    # Evict stale buckets to prevent unbounded growth.
    current_bucket = key[2]
    stale = [k for k in _CACHE if k[2] < current_bucket - 1]
    for k in stale:
        del _CACHE[k]


def invalidate_cache(agent_id: str) -> None:
    """Remove all cache entries involving agent_id (call after link changes)."""
    stale = [k for k in _CACHE if k[0] == agent_id or k[1] == agent_id]
    for k in stale:
        del _CACHE[k]


# ---------------------------------------------------------------------------
# KYA level → confidence mapping
# ---------------------------------------------------------------------------

_KYA_CONFIDENCE: dict[int, float] = {
    0: 0.3,   # none
    1: 0.5,   # basic (email verified)
    2: 0.9,   # attested (VC / DID)
    3: 0.9,   # verified (full KYA)
}


def kya_to_confidence(kya_level: int) -> float:
    return _KYA_CONFIDENCE.get(kya_level, 0.3)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_or_create_principal(
    developer_id: str,
    kya_level: int,
    session: Session,
) -> str:
    """Find or create a Principal keyed on developer_id.

    Returns the principal_id. Must be called inside an active transaction.
    """
    # Use a deterministic lookup: one principal per developer_id.
    # We store developer_id on the principal via a separate lookup through
    # agent_principal_links — find any existing link for an account with
    # this developer_id that has link_source='registration'.
    result = session.execute(
        select(AgentPrincipalLink.principal_id)
        .join(Account, Account.id == AgentPrincipalLink.agent_id)
        .where(
            and_(
                Account.developer_id == developer_id,
                AgentPrincipalLink.link_source == "registration",
            )
        )
        .limit(1)
    ).scalar_one_or_none()

    if result is not None:
        return result

    kya_str = _kya_int_to_str(kya_level)
    principal = Principal(
        principal_type="unknown",
        kya_level=kya_str,
    )
    session.add(principal)
    session.flush()
    logger.debug("Created principal %s for developer_id=%s", principal.id, developer_id)
    return principal.id


def _kya_int_to_str(kya_level: int) -> str:
    mapping = {0: "none", 1: "basic", 2: "attested", 3: "verified"}
    return mapping.get(kya_level, "none")


def link_agent_to_principal(
    agent_id: str,
    principal_id: str,
    source: str,
    confidence: float,
    session: Session,
) -> None:
    """Write or update an AgentPrincipalLink row.

    If a link for (agent_id, principal_id) already exists, its confidence
    is updated only if the new value is higher. Must be called inside an
    active transaction.
    """
    existing = session.execute(
        select(AgentPrincipalLink).where(
            and_(
                AgentPrincipalLink.agent_id == agent_id,
                AgentPrincipalLink.principal_id == principal_id,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        if confidence > existing.confidence:
            existing.confidence = confidence
            existing.link_source = source
            session.add(existing)
    else:
        link = AgentPrincipalLink(
            agent_id=agent_id,
            principal_id=principal_id,
            link_source=source,
            confidence=confidence,
        )
        session.add(link)

    invalidate_cache(agent_id)
    logger.debug(
        "Agent %s linked to principal %s via %s (confidence=%.2f)",
        agent_id, principal_id, source, confidence,
    )


def is_same_principal(
    agent_a: str,
    agent_b: str,
    session: Session,
) -> dict:
    """Determine whether two agents share a controlling principal.

    Returns:
        {
          'verdict': 'hard_match' | 'soft_match' | 'no_match',
          'confidence': float,
          'basis': str,
        }

    Decision function:
      strongest_link = max over all shared principals of
                       min(link_a.confidence, link_b.confidence)
      >= 0.8  → hard_match  (enforcement)
      >= 0.5  → soft_match  (analytics + escalation)
      else    → no_match
    """
    cached = _cache_get(agent_a, agent_b)
    if cached is not None:
        return cached

    # Fetch all (principal_id, confidence) pairs for each agent.
    links_a = {
        row.principal_id: row.confidence
        for row in session.execute(
            select(AgentPrincipalLink).where(AgentPrincipalLink.agent_id == agent_a)
        ).scalars()
    }
    links_b = {
        row.principal_id: row.confidence
        for row in session.execute(
            select(AgentPrincipalLink).where(AgentPrincipalLink.agent_id == agent_b)
        ).scalars()
    }

    shared = set(links_a) & set(links_b)
    if not shared:
        result = {"verdict": "no_match", "confidence": 0.0, "basis": "no_shared_principal"}
        _cache_set(agent_a, agent_b, result)
        return result

    strongest = max(min(links_a[p], links_b[p]) for p in shared)
    best_principal = max(shared, key=lambda p: min(links_a[p], links_b[p]))

    if strongest >= 0.8:
        verdict = "hard_match"
    elif strongest >= 0.5:
        verdict = "soft_match"
    else:
        verdict = "no_match"

    result = {
        "verdict": verdict,
        "confidence": round(strongest, 4),
        "basis": f"principal:{best_principal}",
    }
    _cache_set(agent_a, agent_b, result)
    logger.debug(
        "is_same_principal(%s, %s) → %s (confidence=%.2f)",
        agent_a, agent_b, verdict, strongest,
    )
    return result


def classify_transaction(
    requester_id: str,
    provider_id: str,
    session: Session,
) -> str:
    """Return the self_dealing_class for a proposed transaction.

    Returns one of: 'arms_length', 'suspected_self_dealing', 'self_dealing'
    """
    match = is_same_principal(requester_id, provider_id, session)
    verdict = match["verdict"]
    if verdict == "hard_match":
        return "self_dealing"
    if verdict == "soft_match":
        return "suspected_self_dealing"
    return "arms_length"
