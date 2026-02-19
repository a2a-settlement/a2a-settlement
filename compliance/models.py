from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class AttestationHeader(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: str = "1.0"
    schema_id: str = "urn:a2a-se:pre-dispute-attestation:v1"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    issuer_id: str
    nonce: str = Field(default_factory=lambda: str(uuid4()))


class AP2MandateBinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    intent_did: str
    cart_did: str
    payment_did: str


class MediationState(BaseModel):
    model_config = ConfigDict(frozen=True)

    escrow_id: str
    escrow_status: Literal["held", "released", "refunded", "expired", "disputed"]
    dispute_reason: str | None = None
    resolution_strategy: str | None = None
    mediator_id: str | None = None


class CryptographicProof(BaseModel):
    model_config = ConfigDict(frozen=True)

    payload_hash: str
    merkle_root: str
    merkle_leaf_index: int
    tsa_timestamp_token: bytes | None = None
    tsa_authority_url: str | None = None


class PreDisputeAttestationPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    header: AttestationHeader
    mandate: AP2MandateBinding
    mediation: MediationState
    proof: CryptographicProof | None = None

    def canonical_bytes(self) -> bytes:
        """Deterministic JSON serialization for hashing.

        Excludes the ``proof`` section so the hash covers only the
        attestation content, not the proof-of-that-content (which would
        create a circular dependency).
        """
        data = self.model_dump(mode="json", exclude={"proof"})
        return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
