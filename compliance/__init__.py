"""Cryptographic compliance primitives for A2A Settlement.

This package is intentionally isolated from the exchange service.
It contains only: Pydantic models, SHA-256 hashing, an append-only
Merkle tree (SQLite-backed), and an RFC 3161 TSA client wrapper.
"""

from compliance.merkle import EMPTY_ROOT, MerkleTree
from compliance.models import (
    AP2MandateBinding,
    AttestationHeader,
    CryptographicProof,
    MediationState,
    PreDisputeAttestationPayload,
)
from compliance.tsa import TimestampAuthority, TimestampResponse

__all__ = [
    "AP2MandateBinding",
    "AttestationHeader",
    "CryptographicProof",
    "EMPTY_ROOT",
    "MediationState",
    "MerkleTree",
    "PreDisputeAttestationPayload",
    "TimestampAuthority",
    "TimestampResponse",
]
