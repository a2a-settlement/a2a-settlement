"""The only agent-visible payment tool.

The gateway holds the requester secret. The destination is always
POST {exchange}/exchange/release.
"""

from __future__ import annotations

from .models import ToolDefinition
from .tool_registry import ToolRegistry

PAYMENT_TOOL_ID = "request_payment"
RELEASE_PATH = "/exchange/release"


def register_request_payment(registry: ToolRegistry, exchange_base_url: str, secret_id: str) -> ToolDefinition:
    tool = ToolDefinition(
        tool_id=PAYMENT_TOOL_ID,
        destination_url=exchange_base_url.rstrip("/") + RELEASE_PATH,
        method="POST",
        secret_id=secret_id,
        inject_as="bearer",
        description="Release a held escrow. The actor does not see the URL or the key.",
    )
    registry.register(tool)
    return tool
