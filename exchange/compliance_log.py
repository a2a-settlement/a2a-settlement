"""Compliance audit logging — wires the compliance Merkle tree into the exchange.

Records key settlement events (escrow creation, release, refund, dispute,
resolution) as tamper-evident Merkle tree leaves. The tree provides
cryptographic proof that the audit trail has not been modified.

Enabled via A2A_EXCHANGE_COMPLIANCE_ENABLED=true.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from exchange.config import settings

logger = logging.getLogger("exchange.compliance")

_tree = None


def _get_tree():
    global _tree
    if _tree is None:
        try:
            from compliance.merkle import MerkleTree
            from compliance.models import (
                AttestationHeader,
                AP2MandateBinding,
                MediationState,
                PreDisputeAttestationPayload,
            )

            db_path = Path("compliance_merkle.db")
            _tree = MerkleTree(db_path)
            logger.info("Compliance Merkle tree initialized at %s", db_path)
        except ImportError:
            logger.warning("compliance package not available — audit logging disabled")
    return _tree


def log_settlement_event(
    *,
    escrow_id: str,
    event_type: str,
    requester_id: str,
    provider_id: str,
    amount: int,
    status: str,
    dispute_reason: Optional[str] = None,
    resolution_strategy: Optional[str] = None,
    grounding_chain: Optional[dict] = None,
) -> Optional[dict]:
    """Record a settlement event in the compliance Merkle tree.

    Args:
        grounding_chain: Optional web grounding summary to include in the
            audit trail for ``escrow.delivered`` events.  Expected keys:
            ``search_queries``, ``chunk_count``, ``coverage``,
            ``source_uris``.

    Returns the proof dict on success, None if compliance is disabled.
    """
    if not getattr(settings, "compliance_enabled", False):
        return None

    tree = _get_tree()
    if tree is None:
        return None

    try:
        from compliance.models import (
            AttestationHeader,
            AP2MandateBinding,
            MediationState,
            PreDisputeAttestationPayload,
        )

        extra_strategy = resolution_strategy
        if grounding_chain and event_type == "escrow.delivered":
            gc_summary = json.dumps(grounding_chain, separators=(",", ":"))
            extra_strategy = (
                f"{resolution_strategy}|grounding:{gc_summary}"
                if resolution_strategy
                else f"grounding:{gc_summary}"
            )

        payload = PreDisputeAttestationPayload(
            header=AttestationHeader(
                issuer_id="exchange",
            ),
            mandate=AP2MandateBinding(
                intent_did=f"did:a2a:{requester_id}",
                cart_did=f"urn:escrow:{escrow_id}",
                payment_did=f"did:a2a:{provider_id}",
            ),
            mediation=MediationState(
                escrow_id=escrow_id,
                escrow_status=status,
                dispute_reason=dispute_reason,
                resolution_strategy=extra_strategy,
            ),
        )

        root_hash, leaf_index = tree.append(payload)
        data_hash = hashlib.sha256(payload.canonical_bytes()).hexdigest()

        logger.info(
            "Compliance log: event=%s escrow=%s leaf=%d root=%s",
            event_type,
            escrow_id,
            leaf_index,
            root_hash[:16],
        )

        return {
            "merkle_root": root_hash,
            "leaf_index": leaf_index,
            "data_hash": data_hash,
        }
    except Exception:
        logger.exception("Failed to log compliance event for escrow %s", escrow_id)
        return None


def get_tree_status() -> dict:
    """Return current Merkle tree status for the /stats endpoint."""
    if not getattr(settings, "compliance_enabled", False):
        return {"enabled": False}
    tree = _get_tree()
    if tree is None:
        return {"enabled": False}
    return {
        "enabled": True,
        "leaf_count": tree.leaf_count,
        "root_hash": tree.root,
    }
