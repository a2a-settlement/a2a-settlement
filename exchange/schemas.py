from __future__ import annotations

from datetime import datetime

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
    created_at: datetime | None = None


class RegisterResponse(BaseModel):
    message: str = "Bot registered successfully. Save your API key - it will not be shown again."
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


class EscrowRequest(BaseModel):
    provider_id: str
    amount: int
    task_id: str | None = None
    task_type: str | None = None
    ttl_minutes: int | None = None
    group_id: str | None = None
    depends_on: list[str] | None = None
    deliverables: list[Deliverable] | None = None


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


class ResolveRequest(BaseModel):
    escrow_id: str
    resolution: str
    strategy: str | None = None


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


class StatsResponse(BaseModel):
    network: StatsNetworkInfo
    token_supply: StatsTokenSupply
    activity_24h: StatsActivity
    treasury: StatsTreasury
    active_escrows: int


# --- Health ---


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "a2a-settlement-exchange"
    version: str = "0.8.1"
