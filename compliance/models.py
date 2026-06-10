from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


ESCROW_RELEASE_ATTESTATION_SCHEMA_ID = "urn:a2a-se:escrow-release-attestation:v1"
ESCROW_REFUND_ATTESTATION_SCHEMA_ID = "urn:a2a-se:escrow-refund-attestation:v1"
DISPUTE_RESOLUTION_ATTESTATION_SCHEMA_ID = "urn:a2a-se:dispute-resolution-attestation:v1"


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
    escrow_status: Literal[
        "held",
        "released",
        "refunded",
        "expired",
        "disputed",
        "partially_released",
        "evidence_pending",
        "settled",
    ]
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


def _canonical_bytes(model: BaseModel) -> bytes:
    data = model.model_dump(mode="json", exclude={"proof"})
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


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
        return _canonical_bytes(self)


class PartyRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    did: str
    account_id: str | None = None
    external_ids: dict[str, str] = Field(default_factory=dict)


class SettlementCore(BaseModel):
    model_config = ConfigDict(frozen=True)

    escrow_id: str
    settlement_kind: str = "a2a-se"
    requester: PartyRef
    provider: PartyRef
    amount: int
    fee_amount: int = 0
    currency: str = "ATE"
    rail: str = "a2a-se"
    task_id: str | None = None
    task_type: str | None = None
    self_dealing_class: str | None = None
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EscrowReleaseAttestation(BaseModel):
    model_config = ConfigDict(frozen=True)

    header: AttestationHeader
    settlement: SettlementCore
    release_kind: Literal["full", "partial", "holdback"] = "full"
    amount_paid: int
    fee_collected: int = 0
    reputation_attestation_type: str = "urn:a2a-settlement:ema-reputation:v1"
    proof: CryptographicProof | None = None

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self)


class EscrowRefundAttestation(BaseModel):
    model_config = ConfigDict(frozen=True)

    header: AttestationHeader
    settlement: SettlementCore
    refund_kind: Literal["full", "holdback", "auto_dependent"] = "full"
    amount_returned: int
    refund_reason: str | None = None
    reputation_attestation_type: str = "urn:a2a-settlement:ema-reputation:v1"
    proof: CryptographicProof | None = None

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self)


class DisputeResolutionAttestation(BaseModel):
    model_config = ConfigDict(frozen=True)

    header: AttestationHeader
    settlement: SettlementCore
    resolution: Literal["release", "refund"]
    mediator_id: str | None = None
    resolution_strategy: str | None = None
    dispute_reason: str | None = None
    stake_ruling: str | None = None
    amount_paid: int | None = None
    amount_returned: int | None = None
    fee_collected: int = 0
    reputation_attestation_type: str = "urn:a2a-settlement:ema-reputation:v1"
    proof: CryptographicProof | None = None

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self)
