"""Pydantic models for the KYA-Enhanced Agent Card (RFC-001).

Used for request validation, serialization, and OpenAPI doc generation.
"""

from __future__ import annotations

from datetime import datetime
from enum import IntEnum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class KYALevel(IntEnum):
    SANDBOX = 0
    ORGANIZATIONAL = 1
    AUDITABLE = 2


# --- Identity ---


class VerificationMethodModel(BaseModel):
    id: str
    type: str
    controller: str
    public_key_multibase: str


class IdentityModel(BaseModel):
    type: str
    did_document_url: Optional[str] = None
    verification_method: Optional[VerificationMethodModel] = None
    key_id: Optional[str] = None


# --- Attestation / VC ---


class CredentialProofModel(BaseModel):
    type: str
    created: datetime
    verification_method: str
    proof_value: str


class AttestationModel(BaseModel):
    type: str = "VerifiableCredential"
    issuer: str
    issuer_name: str
    claim: str
    credential_subject: str
    valid_from: datetime
    expires_at: datetime
    proof: CredentialProofModel

    @field_validator("expires_at")
    @classmethod
    def expires_after_valid_from(cls, v, info):
        if "valid_from" in info.data and v <= info.data["valid_from"]:
            raise ValueError("expires_at must be after valid_from")
        return v


# --- Settlement ---


class HITLThresholdModel(BaseModel):
    enabled: bool = False
    amount: Optional[float] = None
    currency: str = "USD"
    approval_credential_type: str = "HumanApprovalVC"


class SettlementModel(BaseModel):
    supported_methods: list[str]
    exchange_url: str
    token_types: list[str] = Field(default_factory=lambda: ["SETTLE"])
    merkle_proof_enabled: bool = False
    audit_root_frequency_seconds: Optional[int] = None
    hitl_threshold: Optional[HITLThresholdModel] = None
    max_escrow_duration_seconds: Optional[int] = None
    partial_completion: bool = False


# --- Capabilities ---


class RateLimitModel(BaseModel):
    requests_per_minute: int = 60
    concurrent_tasks: int = 5


class CapabilitiesModel(BaseModel):
    skills: list[str] = Field(default_factory=list)
    input_formats: list[str] = Field(default_factory=lambda: ["application/json"])
    output_formats: list[str] = Field(default_factory=lambda: ["application/json"])
    rate_limit: Optional[RateLimitModel] = None


# --- Policies ---


class PoliciesModel(BaseModel):
    data_retention: Optional[str] = None
    jurisdiction: Optional[str] = None
    pii_handling: Optional[str] = None
    dispute_resolution: Optional[str] = None


# --- Metadata & Signature ---


class CardSignatureModel(BaseModel):
    type: str = "Ed25519Signature2020"
    verification_method: str
    proof_value: str


class MetadataModel(BaseModel):
    created: datetime
    updated: datetime
    card_signature: Optional[CardSignatureModel] = None


# --- Top-level Agent Card ---


class AgentCardModel(BaseModel):
    """Complete KYA-Enhanced Agent Card per RFC-001."""

    protocol_version: str = "2026.1"
    name: str
    id: str
    description: str
    kya_level: KYALevel
    identity: IdentityModel
    attestations: list[AttestationModel] = Field(default_factory=list)
    settlement: SettlementModel
    capabilities: CapabilitiesModel = Field(default_factory=CapabilitiesModel)
    policies: PoliciesModel = Field(default_factory=PoliciesModel)
    metadata: MetadataModel
