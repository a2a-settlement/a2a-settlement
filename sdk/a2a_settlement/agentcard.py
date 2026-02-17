from __future__ import annotations

from typing import Any

from a2a_settlement import A2A_SE_EXTENSION_URI


def build_settlement_extension(
    *,
    exchange_url: str,
    account_id: str,
    pricing: dict[str, Any] | None = None,
    reputation: float | None = None,
    availability: float | None = None,
    required: bool = False,
    description: str = "Accepts token-based payment via A2A Settlement Exchange",
) -> dict[str, Any]:
    """Build an AgentCard `capabilities.extensions[]` entry for A2A-SE.

    Returns a plain dict so callers can use it with or without `a2a-sdk` installed.
    """

    params: dict[str, Any] = {"exchangeUrl": exchange_url, "accountId": account_id}
    if pricing is not None:
        params["pricing"] = pricing
    if reputation is not None:
        params["reputation"] = float(reputation)
    if availability is not None:
        params["availability"] = float(availability)

    return {
        "uri": A2A_SE_EXTENSION_URI,
        "description": description,
        "required": bool(required),
        "params": params,
    }

