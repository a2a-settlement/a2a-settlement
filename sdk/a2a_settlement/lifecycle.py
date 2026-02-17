from __future__ import annotations

from typing import Any

from a2a_settlement.client import SettlementExchangeClient


TERMINAL_SUCCESS = {"TASK_STATE_COMPLETED", "completed"}
TERMINAL_FAILURE = {
    "TASK_STATE_FAILED",
    "TASK_STATE_CANCELED",
    "TASK_STATE_REJECTED",
    "failed",
    "canceled",
    "rejected",
}


def settlement_action_for_task_state(task_state: str | None) -> str | None:
    if not task_state:
        return None
    if task_state in TERMINAL_SUCCESS:
        return "release"
    if task_state in TERMINAL_FAILURE:
        return "refund"
    return None


def settle_for_task_state(
    exchange: SettlementExchangeClient,
    *,
    task_state: str,
    escrow_id: str,
    refund_reason: str | None = None,
) -> dict[str, Any] | None:
    action = settlement_action_for_task_state(task_state)
    if action == "release":
        return exchange.release_escrow(escrow_id=escrow_id)
    if action == "refund":
        return exchange.refund_escrow(escrow_id=escrow_id, reason=refund_reason)
    return None

