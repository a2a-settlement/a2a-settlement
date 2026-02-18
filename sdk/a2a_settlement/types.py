from __future__ import annotations

from typing import Any, TypedDict


class Deliverable(TypedDict, total=False):
    description: str
    artifact_hash: str
    acceptance_criteria: str


class RegisterResponse(TypedDict, total=False):
    message: str
    account: dict[str, Any]
    api_key: str
    starter_tokens: int


class EscrowResponse(TypedDict, total=False):
    escrow_id: str
    requester_id: str
    provider_id: str
    amount: int
    fee_amount: int
    effective_fee_percent: float
    total_held: int
    status: str
    expires_at: str
    group_id: str


class ReleaseResponse(TypedDict, total=False):
    escrow_id: str
    status: str
    amount_paid: int
    fee_collected: int
    provider_id: str


class RefundResponse(TypedDict, total=False):
    escrow_id: str
    status: str
    amount_returned: int
    requester_id: str


class DisputeResponse(TypedDict, total=False):
    escrow_id: str
    status: str
    reason: str


class ResolveResponse(TypedDict, total=False):
    escrow_id: str
    resolution: str
    status: str
    amount_paid: int
    fee_collected: int
    amount_returned: int
    provider_id: str
    requester_id: str


class BalanceResponse(TypedDict, total=False):
    account_id: str
    bot_name: str
    reputation: float
    account_status: str
    available: int
    held_in_escrow: int
    total_earned: int
    total_spent: int


class TransactionItem(TypedDict, total=False):
    id: str
    escrow_id: str
    from_account: str
    to_account: str
    amount: int
    type: str
    description: str
    created_at: str


class RotateKeyResponse(TypedDict, total=False):
    api_key: str
    grace_period_minutes: int


class WebhookResponse(TypedDict, total=False):
    webhook_url: str
    secret: str
    events: list[str]
    active: bool


class BatchEscrowResponse(TypedDict, total=False):
    group_id: str
    escrows: list[EscrowResponse]


class EscrowListResponse(TypedDict, total=False):
    escrows: list[dict[str, Any]]
    total: int
