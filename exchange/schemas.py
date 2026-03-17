from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# --- Error ---


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str = ""
    details: dict | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


# --- Accounts ---


class RegisterRequest(BaseModel):
    bot_name: str = Field(..., min_length=1)
    developer_id: str = Field(..., min_length=1)
    developer_name: str = Field(..., min_length=1)
    contact_email: str = Field(..., pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    description: str | None = None
    skills: list[str] | None = None
    invite_code: str | None = None
    daily_spend_limit: int | None = None


class RegisterAccountInfo(BaseModel):
    id: str
    bot_name: str
    developer_id: str
    developer_name: str
    contact_email: str
    description: str | None = None
    skills: list[str] = []
    status: str = "active"
    reputation: float = 0.5
    daily_spend_limit: int | None = None
    created_at: datetime | None = None


class RegisterResponse(BaseModel):
    message: str = (
        "Bot registered successfully. Save your API key - it will not be shown again."
    )
    account: RegisterAccountInfo
    api_key: str
    starter_tokens: int


class AccountResponse(BaseModel):
    id: str
    bot_name: str
    developer_id: str
    developer_name: str
    contact_email: str
    description: str | None = None
    skills: list[str] = []
    status: str
    reputation: float
    daily_spend_limit: int | None = None
    created_at: datetime | None = None


class DirectoryResponse(BaseModel):
    bots: list[AccountResponse]
    count: int


class SuspendRequest(BaseModel):
    account_id: str = Field(..., min_length=1)
    reason: str | None = None


class SuspendResponse(BaseModel):
    account_id: str
    status: str = "suspended"
    reason: str | None = None


class UpdateSkillsRequest(BaseModel):
    skills: list[str]


class UpdateSkillsResponse(BaseModel):
    account_id: str
    skills: list[str]


class RotateKeyResponse(BaseModel):
    api_key: str
    grace_period_minutes: int


# --- Deposit ---


class DepositRequest(BaseModel):
    amount: int = Field(..., gt=0)
    currency: str = "ATE"
    reference: str | None = None


class DepositResponse(BaseModel):
    deposit_id: str
    account_id: str
    amount: int
    currency: str
    new_balance: int
    reference: str | None = None


# --- Settlement ---


class Deliverable(BaseModel):
    description: str
    artifact_hash: str | None = None
    acceptance_criteria: str | None = None


class SourceRef(BaseModel):
    uri: str
    method: str | None = None
    timestamp: datetime
    content_hash: str | None = None


class GroundingChunk(BaseModel):
    uri: str
    title: str | None = None


class GroundingSegment(BaseModel):
    text: str
    start_index: int
    end_index: int


class GroundingSupport(BaseModel):
    segment: GroundingSegment
    chunk_indices: list[int]


class GroundingMetadata(BaseModel):
    chunks: list[GroundingChunk] = []
    supports: list[GroundingSupport] = []
    search_queries: list[str] = []
    coverage: float | None = None


class Provenance(BaseModel):
    source_type: Literal["api", "database", "web", "generated", "hybrid"]
    source_refs: list[SourceRef] = []
    attestation_level: Literal["self_declared", "signed", "verifiable"]
    signature: str | None = None
    grounding_metadata: GroundingMetadata | None = None


AttestationLevel = Literal["self_declared", "signed", "verifiable"]


# --- Verifiable Intent (VI) ---


class VICredentialChain(BaseModel):
    """Optional Verifiable Intent credential chain bound to an escrow.

    Carries the SD-JWT delegation chain (L1 -> L2 -> L3) from the VI spec.
    In Immediate mode only L1+L2 are present; in Autonomous mode L3a/L3b
    prove the agent acted within L2 constraints.
    """

    l1_sd_jwt: str
    l2_kb_sd_jwt: str
    l3a_kb_sd_jwt: str | None = None
    l3b_kb_sd_jwt: str | None = None
    mode: Literal["immediate", "autonomous"]
    sd_hash_verified: bool = False


class VIAttestation(BaseModel):
    """A2A-SE attestation formatted for VI's agent_attestation claim (spec section 9.2)."""

    type: str
    value: dict


# --- Attestation Lifecycle ---


class AttestationType(str, Enum):
    IDENTITY = "identity"
    REPUTATION = "reputation"
    TRANSACTION = "transaction"
    CAPABILITY = "capability"


class AttestationStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    RENEWED = "renewed"


class RevocationReason(str, Enum):
    KEY_COMPROMISE = "key_compromise"
    ERRONEOUS_ISSUANCE = "erroneous_issuance"
    DEREGISTRATION = "deregistration"
    POLICY_VIOLATION = "policy_violation"


class AttestationCreate(BaseModel):
    attestation_type: AttestationType
    metadata: dict | None = None


class AttestationResponse(BaseModel):
    id: str
    account_id: str
    attestation_type: str
    status: str
    issued_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    revocation_reason: str | None = None
    parent_attestation_id: str | None = None
    payload_hash: str | None = None
    ttl_remaining_seconds: float | None = None


class AttestationStatusResponse(BaseModel):
    """OCSP-style lightweight status check response."""
    id: str
    status: str
    attestation_type: str
    issued_at: datetime
    expires_at: datetime | None = None
    ttl_remaining_seconds: float | None = None
    revoked_at: datetime | None = None
    revocation_reason: str | None = None
    in_flight_grace: bool = False


class RevokeAttestationRequest(BaseModel):
    reason: RevocationReason
    signatures: list[str] = Field(default_factory=list)


class RevokeAttestationResponse(BaseModel):
    id: str
    status: str = "revoked"
    revoked_at: datetime
    revocation_reason: str


class RenewAttestationResponse(BaseModel):
    old_attestation_id: str
    new_attestation: AttestationResponse
    fee_charged: int


class AttestationListResponse(BaseModel):
    attestations: list[AttestationResponse]
    total: int


# --- Settlement ---


class EscrowRequest(BaseModel):
    provider_id: str
    amount: int
    task_id: str | None = None
    task_type: str | None = None
    ttl_minutes: int | None = None
    group_id: str | None = None
    depends_on: list[str] | None = None
    deliverables: list[Deliverable] | None = None
    required_attestation_level: AttestationLevel | None = None
    vi_credential_chain: VICredentialChain | None = None


class EscrowResponse(BaseModel):
    escrow_id: str
    requester_id: str
    provider_id: str
    amount: int
    fee_amount: int
    effective_fee_percent: float
    total_held: int
    status: str
    expires_at: datetime
    group_id: str | None = None


class ReleaseRequest(BaseModel):
    escrow_id: str


class ReleaseResponse(BaseModel):
    escrow_id: str
    status: str = "released"
    amount_paid: int
    fee_collected: int
    provider_id: str


class RefundRequest(BaseModel):
    escrow_id: str
    reason: str | None = None


class RefundResponse(BaseModel):
    escrow_id: str
    status: str = "refunded"
    amount_returned: int
    requester_id: str


class DisputeRequest(BaseModel):
    escrow_id: str
    reason: str
    stake_amount: int = Field(..., gt=0)


class DisputeResponse(BaseModel):
    escrow_id: str
    status: str = "evidence_pending"
    reason: str
    stake_amount: int = 0
    evidence_window_closes_at: datetime | None = None


# --- Evidence ---


class EvidenceType(str, Enum):
    COMPUTE = "compute"
    CONTENT = "content"
    SERVICE = "service"
    BOUNTY = "bounty"
    THIRD_PARTY_ATTESTATION = "third_party_attestation"


MAX_INLINE_EVIDENCE_BYTES = 5 * 1024 * 1024  # 5 MB


class EvidenceArtifact(BaseModel):
    artifact_type: Literal["inline", "uri"]
    content: str | None = None
    uri: str | None = None
    content_hash: str = Field(..., min_length=64, max_length=64)
    mime_type: str | None = None


class SubmitEvidenceRequest(BaseModel):
    evidence_type: EvidenceType
    summary: str = Field(..., min_length=1, max_length=4096)
    artifacts: list[EvidenceArtifact] = []
    encrypted: bool = False
    encryption_key_id: str | None = None
    attestor_id: str | None = None
    attestor_signature: str | None = None


class EvidenceSubmissionResponse(BaseModel):
    id: str
    escrow_id: str
    submitter_id: str
    evidence_type: str
    summary: str
    artifact_count: int
    encrypted: bool = False
    submitted_at: datetime


class EvidenceListResponse(BaseModel):
    evidence: list[EvidenceSubmissionResponse]
    total: int


class ComplianceBundleResponse(BaseModel):
    escrow_id: str
    contract: dict
    evidence_submissions: list[dict]
    mediator_rationale: dict | None = None
    mediator_context: dict | None = None
    merkle_proof: dict | None = None
    rfc3161_timestamp: str | None = None
    exported_at: datetime


class DeliverRequest(BaseModel):
    content: str
    provenance: Provenance | None = None


class DeliverResponse(BaseModel):
    escrow_id: str
    status: str
    delivered_at: datetime


class PartialReleaseRequest(BaseModel):
    escrow_id: str
    release_percent: int = Field(..., ge=1, le=99)
    score: int | None = Field(None, ge=0, le=100)
    efficacy_check_at: datetime | None = None
    efficacy_criteria: str | None = None


class PartialReleaseResponse(BaseModel):
    escrow_id: str
    status: str
    released_amount: int
    fee_collected: int
    holdback_amount: int
    holdback_fee: int
    provider_id: str
    efficacy_check_at: datetime | None = None


class ResolveRequest(BaseModel):
    escrow_id: str
    resolution: str
    strategy: str | None = None
    provenance_result: dict | None = None
    mediator_context: dict | None = None
    stake_ruling: Literal["return", "forfeit"] | None = None


class ResolveReleaseResponse(BaseModel):
    escrow_id: str
    resolution: str = "release"
    status: str = "released"
    amount_paid: int
    fee_collected: int
    provider_id: str


class ResolveRefundResponse(BaseModel):
    escrow_id: str
    resolution: str = "refund"
    status: str = "refunded"
    amount_returned: int
    requester_id: str


class BalanceResponse(BaseModel):
    account_id: str
    bot_name: str
    reputation: float
    account_status: str
    available: int
    held_in_escrow: int
    total_earned: int
    total_spent: int


class TransactionItem(BaseModel):
    id: str
    escrow_id: str | None = None
    from_account: str | None = None
    to_account: str | None = None
    amount: int
    type: str
    description: str | None = None
    created_at: datetime | None = None


class TransactionsResponse(BaseModel):
    transactions: list[TransactionItem]


class EscrowDetailResponse(BaseModel):
    id: str
    requester_id: str
    provider_id: str
    amount: int
    fee_amount: int
    effective_fee_percent: float
    status: str
    dispute_reason: str | None = None
    dispute_filed_by: str | None = None
    dispute_stake_amount: int | None = None
    dispute_stake_status: str | None = None
    evidence_window_closes_at: datetime | None = None
    resolution_strategy: str | None = None
    expires_at: datetime
    task_id: str | None = None
    task_type: str | None = None
    group_id: str | None = None
    depends_on: list[str] | None = None
    deliverables: list[Deliverable] | None = None
    required_attestation_level: str | None = None
    delivered_content: str | None = None
    provenance: dict | None = None
    provenance_result: dict | None = None
    delivered_at: datetime | None = None
    released_amount: int | None = None
    released_fee: int | None = None
    holdback_amount: int | None = None
    holdback_fee: int | None = None
    score: int | None = None
    efficacy_check_at: datetime | None = None
    efficacy_criteria: str | None = None
    vi_credential_chain: dict | None = None
    created_at: datetime | None = None
    resolved_at: datetime | None = None


class EscrowListResponse(BaseModel):
    escrows: list[EscrowDetailResponse]
    total: int


class BatchEscrowItem(BaseModel):
    provider_id: str
    amount: int
    task_id: str | None = None
    task_type: str | None = None
    ttl_minutes: int | None = None
    depends_on: list[str] | None = None
    deliverables: list[Deliverable] | None = None
    required_attestation_level: AttestationLevel | None = None
    vi_credential_chain: VICredentialChain | None = None


class BatchEscrowRequest(BaseModel):
    group_id: str | None = None
    escrows: list[BatchEscrowItem] = Field(..., min_length=1)


class BatchEscrowResponse(BaseModel):
    group_id: str
    escrows: list[EscrowResponse]


# --- Webhooks ---


class WebhookSetRequest(BaseModel):
    url: str
    events: list[str] | None = None


class WebhookResponse(BaseModel):
    webhook_url: str
    secret: str | None = None
    events: list[str]
    active: bool


class WebhookDeleteResponse(BaseModel):
    status: str = "removed"


class WebhookEventPayload(BaseModel):
    event: str
    timestamp: datetime
    data: dict


# --- Stats ---


class StatsNetworkInfo(BaseModel):
    total_bots: int
    active_bots: int


class StatsTokenSupply(BaseModel):
    circulating: int
    in_escrow: int
    total: int


class StatsActivity(BaseModel):
    transaction_count: int
    token_volume: int
    velocity: float


class StatsTreasury(BaseModel):
    fees_collected: int


class StatsComplianceInfo(BaseModel):
    enabled: bool = False
    leaf_count: int = 0
    root_hash: str | None = None


class StatsProvenanceInfo(BaseModel):
    total_delivered: int = 0
    with_provenance: int = 0
    total_verified: int = 0
    fabrication_detected: int = 0
    partial_releases: int = 0
    pending_efficacy_reviews: int = 0


class StatsResponse(BaseModel):
    network: StatsNetworkInfo
    token_supply: StatsTokenSupply
    activity_24h: StatsActivity
    treasury: StatsTreasury
    active_escrows: int
    compliance: StatsComplianceInfo | None = None
    provenance: StatsProvenanceInfo | None = None


# --- KYA ---


class KYAVerificationDetail(BaseModel):
    credential_claim: str | None = None
    issuer_did: str | None = None
    status: str


class KYARegisterResponse(BaseModel):
    message: str = "Agent registered with KYA verification."
    account: RegisterAccountInfo
    api_key: str
    starter_tokens: int
    kya_level_claimed: int
    kya_level_verified: int
    card_signature_valid: bool = False
    did_resolved: bool = False
    credential_results: list[KYAVerificationDetail] = []
    error_summary: str | None = None


class AgentCardResponse(BaseModel):
    agent_id: str
    kya_level_verified: int
    card: dict


class VerificationStatusResponse(BaseModel):
    agent_id: str
    kya_level_verified: int
    did: str | None = None
    card_verified_at: datetime | None = None
    attestation_expires_at: datetime | None = None


# --- Health ---


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "a2a-settlement-exchange"
    version: str = "1.0.0"
    database: str = "ok"
