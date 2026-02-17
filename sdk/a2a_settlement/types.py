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

