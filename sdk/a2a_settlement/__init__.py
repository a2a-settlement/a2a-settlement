"""Python SDK for the A2A Settlement Extension (A2A-SE).

This package is intentionally small:
- Exchange HTTP client helpers (escrow/release/refund/balance).
- AgentCard settlement extension block generation.
- A2A message/task metadata helpers for `metadata["a2a-se"]`.
"""

from __future__ import annotations

__all__ = [
    "A2A_SE_EXTENSION_URI",
    "SettlementExchangeClient",
    "sign_request",
]

from a2a_settlement.client import SettlementExchangeClient, sign_request

A2A_SE_EXTENSION_URI = "https://a2a-settlement.org/extensions/settlement/v1"

