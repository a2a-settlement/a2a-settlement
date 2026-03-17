"""Capability manifest generation for the federation protocol.

Generates the five-field MVP manifest exchanged during peering.
"""

from __future__ import annotations

from typing import Optional

from exchange.config import settings


def generate_capability_manifest(
    node_did: str,
    base_url: Optional[str] = None,
) -> dict:
    """Generate this exchange's capability manifest.

    Parameters
    ----------
    node_did:
        The exchange's own DID (``did:web:...``).
    base_url:
        Base URL for federation endpoints. Defaults to settings.
    """
    url = (base_url or settings.base_url).rstrip("/")

    return {
        "node_did": node_did,
        "supported_assets": ["a2a:ledger:ate"],
        "attestation_types": [
            "IdentityAttestation",
            "CapabilityAttestation",
            "ReputationAttestation",
            "EvidenceAttestation",
            "TransactionAttestation",
        ],
        "policy_uri": f"{url}/.well-known/a2a-trust-policy.json",
        "endpoints": {
            "verify": f"{url}/federation/verify",
            "peer": f"{url}/federation/peer",
            "health": f"{url}/.well-known/a2a-federation-health",
            "attestation_import": f"{url}/federation/attestation/import",
        },
    }
