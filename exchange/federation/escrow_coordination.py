"""Federation escrow coordination — Designated Escrow model.

Notifies peer exchanges of escrow state transitions via signed HTTP messages.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any

import httpx

from exchange.config import settings

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 15


def _to_iso(val: Any) -> str | None:
    """Convert datetime to ISO string, else return None."""
    if val is None:
        return None
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return str(val)


def _escrow_to_payload(escrow: Any) -> dict:
    """Build a JSON-serializable payload from escrow data (model or dict)."""
    if hasattr(escrow, "__dict__") and not isinstance(escrow, dict):
        # SQLAlchemy model
        return {
            "id": getattr(escrow, "id", None),
            "requester_id": getattr(escrow, "requester_id", None),
            "provider_id": getattr(escrow, "provider_id", None),
            "amount": int(getattr(escrow, "amount", 0)),
            "fee_amount": int(getattr(escrow, "fee_amount", 0)),
            "status": getattr(escrow, "status", None),
            "task_id": getattr(escrow, "task_id", None),
            "task_type": getattr(escrow, "task_type", None),
            "group_id": getattr(escrow, "group_id", None),
            "expires_at": _to_iso(getattr(escrow, "expires_at", None)),
            "is_federated": bool(getattr(escrow, "is_federated", False)),
            "designated_exchange_did": getattr(
                escrow, "designated_exchange_did", None
            ),
            "remote_peer_did": getattr(escrow, "remote_peer_did", None),
            "remote_agent_did": getattr(escrow, "remote_agent_did", None),
            "requester_did": getattr(escrow, "requester_did", None),
            "provider_did": getattr(escrow, "provider_did", None),
            "delivered_at": _to_iso(getattr(escrow, "delivered_at", None)),
            "dispute_reason": getattr(escrow, "dispute_reason", None),
            "dispute_filed_by": getattr(escrow, "dispute_filed_by", None),
        }
    if isinstance(escrow, dict):
        out = dict(escrow)
        for k, v in list(out.items()):
            if hasattr(v, "isoformat"):
                out[k] = v.isoformat()
        return out
    return {}


def _sign_payload(secret: str, body: bytes) -> str:
    """Produce HMAC-SHA256 signature of the body."""
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


class FederatedEscrowCoordinator:
    """Handles notifying peer exchanges of escrow state transitions via signed HTTP messages."""

    def __init__(
        self,
        *,
        signing_secret: str | None = None,
        node_did: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ):
        self._signing_secret = signing_secret or getattr(
            settings, "federation_escrow_signing_secret", ""
        )
        self._node_did = node_did or getattr(
            settings, "federation_node_did", ""
        )
        self._timeout = timeout

    def _build_notification(
        self,
        event_type: str,
        escrow: Any,
        extra: dict | None = None,
    ) -> tuple[dict, bytes]:
        """Build notification payload and raw body for signing."""
        payload = _escrow_to_payload(escrow)
        body = {
            "type": event_type,
            "source_exchange_did": self._node_did,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "nonce": secrets.token_hex(16),
            "escrow": payload,
        }
        if extra:
            body["extra"] = extra
        raw = json.dumps(body, sort_keys=True).encode("utf-8")
        return body, raw

    def _send(
        self,
        peer_url: str,
        event_type: str,
        body: dict,
        raw_body: bytes,
    ) -> bool:
        """Send signed POST to peer exchange."""
        url = peer_url.rstrip("/")
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        # Resolve escrow notification endpoint from base URL
        endpoint = f"{url}/federation/escrow/notify"
        headers = {
            "Content-Type": "application/json",
            "X-A2ASE-Federation-Event": event_type,
            "X-A2ASE-Source-Exchange": self._node_did or "",
        }

        if self._signing_secret:
            headers["X-A2ASE-Signature"] = _sign_payload(
                self._signing_secret, raw_body
            )
        else:
            logger.warning(
                "Federation escrow notification sent without signature "
                "(federation_escrow_signing_secret not configured)"
            )

        try:
            resp = httpx.post(
                endpoint,
                content=raw_body,
                headers=headers,
                timeout=self._timeout,
            )
            if 200 <= resp.status_code < 300:
                return True
            logger.warning(
                "Federation escrow notify to %s returned %s for %s",
                endpoint,
                resp.status_code,
                event_type,
            )
        except Exception as exc:
            logger.warning(
                "Federation escrow notify to %s failed: %s",
                endpoint,
                exc,
                exc_info=True,
            )
        return False

    def notify_escrow_created(
        self,
        escrow: Any,
        peer_exchange_url: str,
    ) -> bool:
        """Notify peer exchange that an escrow was created."""
        body, raw = self._build_notification("escrow.created", escrow)
        return self._send(
            peer_exchange_url,
            "escrow.created",
            body,
            raw,
        )

    def notify_delivery_submitted(
        self,
        escrow: Any,
        peer_exchange_url: str,
        *,
        delivered_content: str | None = None,
        provenance: dict | None = None,
    ) -> bool:
        """Notify peer exchange that delivery was submitted."""
        extra = {}
        if delivered_content is not None:
            extra["delivered_content"] = delivered_content
        if provenance is not None:
            extra["provenance"] = provenance
        body, raw = self._build_notification(
            "delivery.submitted",
            escrow,
            extra=extra if extra else None,
        )
        return self._send(
            peer_exchange_url,
            "delivery.submitted",
            body,
            raw,
        )

    def notify_escrow_released(
        self,
        escrow: Any,
        peer_exchange_url: str,
        *,
        released_amount: int | None = None,
        released_fee: int | None = None,
    ) -> bool:
        """Notify peer exchange that escrow was released."""
        extra = {}
        if released_amount is not None:
            extra["released_amount"] = released_amount
        if released_fee is not None:
            extra["released_fee"] = released_fee
        body, raw = self._build_notification(
            "escrow.released",
            escrow,
            extra=extra if extra else None,
        )
        return self._send(
            peer_exchange_url,
            "escrow.released",
            body,
            raw,
        )

    def notify_escrow_refunded(
        self,
        escrow: Any,
        peer_exchange_url: str,
    ) -> bool:
        """Notify peer exchange that escrow was refunded."""
        body, raw = self._build_notification("escrow.refunded", escrow)
        return self._send(
            peer_exchange_url,
            "escrow.refunded",
            body,
            raw,
        )

    def notify_dispute_filed(
        self,
        escrow: Any,
        peer_exchange_url: str,
        *,
        dispute_reason: str | None = None,
        filed_by: str | None = None,
    ) -> bool:
        """Notify peer exchange that a dispute was filed."""
        extra = {}
        if dispute_reason is not None:
            extra["dispute_reason"] = dispute_reason
        if filed_by is not None:
            extra["filed_by"] = filed_by
        body, raw = self._build_notification(
            "dispute.filed",
            escrow,
            extra=extra if extra else None,
        )
        return self._send(
            peer_exchange_url,
            "dispute.filed",
            body,
            raw,
        )
