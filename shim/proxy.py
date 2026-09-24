"""Core proxy logic -- resolves tool_id or destination_url, injects credentials, forwards."""

from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

from .config import shim_settings
from .credential_injector import CredentialInjector
from .escrow_gate import EscrowGate, InsufficientEscrowError, EscrowNotFoundError
from .models import ProxyRequest, ProxyResponse, ShimAuditEntry, ToolDefinition
from .tool_registry import ToolRegistry, ToolNotFoundError

logger = logging.getLogger("shim.proxy")


class ProxyError(Exception):
    """Base error for proxy operations."""

    def __init__(self, message: str, status_code: int = 500):
        self.status_code = status_code
        super().__init__(message)


class DestinationDeniedError(ProxyError):
    """Destination URL is blocked by the shim's destination policy."""

    def __init__(self, url: str):
        super().__init__(
            f"Destination '{url}' is not permitted by the shim's destination policy",
            status_code=403,
        )


class ShimProxy:
    """Orchestrates the full proxy pipeline: resolve -> gate -> inject -> forward -> audit."""

    def __init__(
        self,
        escrow_gate: EscrowGate,
        tool_registry: ToolRegistry,
        credential_injector: CredentialInjector,
        audit_log: Optional[list[ShimAuditEntry]] = None,
    ):
        self._gate = escrow_gate
        self._registry = tool_registry
        self._injector = credential_injector
        self._audit_log: list[ShimAuditEntry] = audit_log if audit_log is not None else []

    @property
    def audit_log(self) -> list[ShimAuditEntry]:
        return self._audit_log

    async def handle(self, request: ProxyRequest) -> ProxyResponse:
        """Process a proxy request through the full pipeline.

        1. Resolve tool_id to destination (if provided)
        2. Validate destination against policy
        3. Check escrow balance and deduct cost
        4. Resolve secret and inject credential
        5. Forward request to destination
        6. Record audit entry
        7. Return response to agent
        """
        tool_def: Optional[ToolDefinition] = None
        destination_url = request.destination_url
        method = request.method
        secret_id = request.secret_id
        cost_override: Optional[float] = None

        # The payment tool is registered by the gateway. An actor cannot
        # choose the release URL or substitute another account.
        if request.tool_id == "request_payment" and request.destination_url:
            return self._error_response(
                request, 403, "request_payment does not accept an actor destination"
            )

        # Step 1: Resolve tool_id if provided
        if request.tool_id:
            try:
                tool_def = self._registry.get(request.tool_id)
            except ToolNotFoundError as e:
                return self._error_response(request, 404, str(e))

            destination_url = tool_def.destination_url
            method = tool_def.method
            secret_id = tool_def.secret_id or secret_id
            cost_override = tool_def.cost_override

        if not destination_url:
            return self._error_response(
                request, 400, "Either tool_id or destination_url is required"
            )

        # Step 2: Check destination policy
        if not shim_settings.is_destination_allowed(destination_url):
            return self._error_response(
                request, 403,
                f"Destination '{destination_url}' is not permitted",
                destination=destination_url,
            )

        # Step 3: Check escrow and deduct cost
        try:
            cost = self._gate.check_and_deduct(
                request, destination_url, cost_override
            )
        except EscrowNotFoundError as e:
            return self._error_response(
                request, 404, str(e), destination=destination_url,
            )
        except InsufficientEscrowError as e:
            return self._error_response(
                request, 402, str(e), destination=destination_url, cost=e.required,
            )

        # Step 4: Resolve secret and inject credential
        headers = dict(request.headers)
        url = destination_url
        body = request.body

        if secret_id:
            try:
                headers, url, body = await self._injector.resolve_and_inject(
                    secret_id=secret_id,
                    agent_id=request.agent_id or "unknown",
                    headers=headers,
                    url=url,
                    body=body,
                    escrow_id=request.escrow_id,
                    org_id=request.org_id,
                    tool_def=tool_def,
                )
            except Exception as e:
                return self._error_response(
                    request, 502, f"Credential resolution failed: {e}",
                    destination=destination_url, cost=cost,
                )

        # Step 5: Forward request
        try:
            resp_status, resp_headers, resp_body = await self._forward(
                method=method,
                url=url,
                headers=headers,
                body=body,
            )
        except Exception as e:
            logger.error("Forward failed: %s", e)
            return self._error_response(
                request, 502, f"Upstream request failed: {e}",
                destination=destination_url, cost=cost,
            )

        # Step 6: Audit
        escrow_status = self._gate.get_status(request.escrow_id)
        remaining = escrow_status.remaining if escrow_status else None

        audit = ShimAuditEntry(
            escrow_id=request.escrow_id,
            agent_id=request.agent_id or "unknown",
            destination=destination_url,
            method=method,
            secret_id=secret_id,
            status_code=resp_status,
            cost=cost,
            tool_id=request.tool_id,
        )
        self._audit_log.append(audit)

        # Step 7: Return
        return ProxyResponse(
            status_code=resp_status,
            headers=resp_headers,
            body=resp_body,
            cost_charged=cost,
            escrow_remaining=remaining,
        )

    async def _forward(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: Optional[str],
    ) -> tuple[int, dict[str, str], str]:
        """Forward the request to the destination via httpx."""
        async with httpx.AsyncClient(timeout=shim_settings.request_timeout) as client:
            response = await client.request(
                method=method.upper(),
                url=url,
                headers=headers,
                content=body.encode("utf-8") if body else None,
            )
        resp_headers = dict(response.headers)
        return response.status_code, resp_headers, response.text

    def _error_response(
        self,
        request: ProxyRequest,
        status_code: int,
        message: str,
        destination: str = "",
        cost: float = 0.0,
    ) -> ProxyResponse:
        import json

        audit = ShimAuditEntry(
            escrow_id=request.escrow_id,
            agent_id=request.agent_id or "unknown",
            destination=destination,
            method=request.method,
            secret_id=request.secret_id,
            status_code=status_code,
            cost=cost,
            tool_id=request.tool_id,
            error=message,
        )
        self._audit_log.append(audit)

        return ProxyResponse(
            status_code=status_code,
            body=json.dumps({"error": message}),
            cost_charged=cost,
        )
