"""Data models for the Security Shim."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ToolDefinition:
    """A registered tool mapping: tool_id -> destination + credentials."""

    tool_id: str
    destination_url: str
    method: str = "POST"
    secret_id: Optional[str] = None
    inject_as: str = "header"
    """Where to inject the credential: 'header', 'bearer', 'query', 'body'."""

    inject_key: str = "Authorization"
    """Header name, query param, or body field for injection."""

    cost_override: Optional[float] = None
    """Per-call cost override (None = use default cost model)."""

    description: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class ProxyRequest:
    """Incoming proxy request from an agent."""

    escrow_id: str

    # tool_id path (full air gap)
    tool_id: Optional[str] = None

    # direct path (developer escape hatch)
    destination_url: Optional[str] = None
    method: str = "POST"
    headers: dict[str, str] = field(default_factory=dict)
    body: Optional[str] = None
    secret_id: Optional[str] = None

    agent_id: Optional[str] = None
    org_id: Optional[str] = None
    escrow_amount: Optional[float] = None
    counterparty_allowed: Optional[bool] = None


@dataclass
class ProxyResponse:
    """Response returned to the agent after proxying."""

    status_code: int
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""
    cost_charged: float = 0.0
    escrow_remaining: Optional[float] = None


@dataclass
class ShimAuditEntry:
    """Audit record for a proxied request.

    Feeds into the Merkle tree for SEC 17a-4 compliance.
    """

    escrow_id: str
    agent_id: str
    destination: str
    method: str
    secret_id: Optional[str]
    status_code: int
    cost: float
    timestamp: float = field(default_factory=time.time)
    tool_id: Optional[str] = None
    error: Optional[str] = None
