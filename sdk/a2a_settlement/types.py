from __future__ import annotations

from typing import Any, TypedDict


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
    total_held: int
    status: str
    expires_at: str


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


class RotateKeyResponse(TypedDict, total=False):
    api_key: str
    grace_period_minutes: int


class WebhookResponse(TypedDict, total=False):
    webhook_url: str
    secret: str
    events: list[str]
    active: bool
