from __future__ import annotations

from datetime import datetime
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


class DisputeResponse(BaseModel):
    escrow_id: str
    status: str = "disputed"
    reason: str


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
