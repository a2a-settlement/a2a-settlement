from __future__ import annotations

from datetime import datetime
from typing import Any, TypedDict


class SettlementMetadata(TypedDict, total=False):
    escrowId: str
    amount: int
    feeAmount: int
    exchangeUrl: str
    expiresAt: str
    settlementStatus: str


def build_settlement_metadata(
    *,
    escrow_id: str,
    amount: int,
    fee_amount: int,
    exchange_url: str,
    expires_at: str | datetime,
) -> dict[str, Any]:
    if isinstance(expires_at, datetime):
        expires_at_str = expires_at.isoformat()
    else:
        expires_at_str = expires_at

    return {
        "a2a-se": {
            "escrowId": escrow_id,
            "amount": int(amount),
            "feeAmount": int(fee_amount),
            "exchangeUrl": exchange_url,
            "expiresAt": expires_at_str,
        }
    }


def attach_settlement_metadata(message: Any, *, settlement_metadata: dict[str, Any]) -> Any:
    """Attach metadata to an A2A Message or to a plain dict."""
    if isinstance(message, dict):
        md = message.get("metadata") or {}
        md.update(settlement_metadata)
        message["metadata"] = md
        return message

    md = getattr(message, "metadata", None) or {}
    md.update(settlement_metadata)
    setattr(message, "metadata", md)
    return message


def get_settlement_block(obj: Any) -> dict[str, Any] | None:
    """Extract the `metadata['a2a-se']` block from a Message/Task/dict."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        md = obj.get("metadata") or {}
        block = md.get("a2a-se")
        return block if isinstance(block, dict) else None
    md = getattr(obj, "metadata", None) or {}
    block = md.get("a2a-se")
    return block if isinstance(block, dict) else None

