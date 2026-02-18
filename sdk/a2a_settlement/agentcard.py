from __future__ import annotations

from typing import Any

from a2a_settlement import A2A_SE_EXTENSION_URI


def build_settlement_extension(
    *,
    exchange_urls: list[str] | str,
    account_ids: dict[str, str] | str,
    preferred_exchange: str | None = None,
    pricing: dict[str, Any] | None = None,
    currency: str = "ATE",
    reputation: float | None = None,
    availability: float | None = None,
    required: bool = False,
    description: str = "Accepts token-based payment via A2A Settlement Exchange",
) -> dict[str, Any]:
    """Build an AgentCard `capabilities.extensions[]` entry for A2A-SE.

    Supports both v0.5 multi-exchange format and v0.2 single-exchange format.
    Returns a plain dict so callers can use it with or without `a2a-sdk` installed.

    Args:
        exchange_urls: Single URL string or list of exchange URLs.
        account_ids: Dict mapping exchange URLs to account IDs, or a single account ID string.
        preferred_exchange: Preferred exchange URL (defaults to first in list).
        pricing: Pricing config keyed by skill ID.
        currency: Default currency (default: "ATE").
        reputation: Agent's reputation score (0.0-1.0).
        availability: Agent's availability score (0.0-1.0).
        required: Whether settlement is required for this agent.
        description: Human-readable description for the extension.
    """
    if isinstance(exchange_urls, str):
        exchange_urls = [exchange_urls]
    if isinstance(account_ids, str):
        account_ids = {exchange_urls[0]: account_ids}

    params: dict[str, Any] = {
        "exchangeUrls": exchange_urls,
        "preferredExchange": preferred_exchange or exchange_urls[0],
        "accountIds": account_ids,
        "currency": currency,
    }
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
