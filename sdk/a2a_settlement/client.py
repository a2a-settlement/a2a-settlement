from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx


def _join(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


@dataclass
class SettlementExchangeClient:
    """Synchronous client for the Settlement Exchange REST API."""

    base_url: str
    api_key: str | None = None
    timeout_s: float = 10.0
    default_headers: dict[str, str] = field(default_factory=dict)

    def _headers(self, *, idempotency_key: str | None = None) -> dict[str, str]:
        h: dict[str, str] = {**self.default_headers}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        h["X-Request-Id"] = f"req_{uuid.uuid4().hex[:12]}"
        if idempotency_key:
            h["Idempotency-Key"] = idempotency_key
        return h

    def _client(self, *, idempotency_key: str | None = None) -> httpx.Client:
        return httpx.Client(timeout=self.timeout_s, headers=self._headers(idempotency_key=idempotency_key))

    # --- Accounts ---

    def register_account(
        self,
        *,
        bot_name: str,
        developer_id: str,
        developer_name: str,
        contact_email: str,
        description: str | None = None,
        skills: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        url = _join(self.base_url, "/v1/accounts/register")
        payload: dict[str, Any] = {
            "bot_name": bot_name,
            "developer_id": developer_id,
            "developer_name": developer_name,
            "contact_email": contact_email,
        }
        if description is not None:
            payload["description"] = description
        if skills is not None:
            payload["skills"] = skills

        with self._client(idempotency_key=idempotency_key) as c:
            r = c.post(url, json=payload)
            r.raise_for_status()
            return r.json()

    def directory(self, *, skill: str | None = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        url = _join(self.base_url, "/v1/accounts/directory")
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if skill:
            params["skill"] = skill
        with self._client() as c:
            r = c.get(url, params=params)
            r.raise_for_status()
            return r.json()

    def get_account(self, *, account_id: str) -> dict[str, Any]:
        url = _join(self.base_url, f"/v1/accounts/{account_id}")
        with self._client() as c:
            r = c.get(url)
            r.raise_for_status()
            return r.json()

    def update_skills(self, *, skills: list[str]) -> dict[str, Any]:
        url = _join(self.base_url, "/v1/accounts/skills")
        with self._client() as c:
            r = c.put(url, json={"skills": skills})
            r.raise_for_status()
            return r.json()

    def rotate_key(self) -> dict[str, Any]:
        """Rotate the API key. Returns the new key and grace period."""
        url = _join(self.base_url, "/v1/accounts/rotate-key")
        with self._client() as c:
            r = c.post(url)
            r.raise_for_status()
            return r.json()

    # --- Webhooks ---

    def set_webhook(self, *, url: str, events: list[str] | None = None) -> dict[str, Any]:
        """Register or update webhook URL."""
        endpoint = _join(self.base_url, "/v1/accounts/webhook")
        payload: dict[str, Any] = {"url": url}
        if events is not None:
            payload["events"] = events
        with self._client() as c:
            r = c.put(endpoint, json=payload)
            r.raise_for_status()
            return r.json()

    def delete_webhook(self) -> dict[str, Any]:
        """Remove webhook configuration."""
        endpoint = _join(self.base_url, "/v1/accounts/webhook")
        with self._client() as c:
            r = c.delete(endpoint)
            r.raise_for_status()
            return r.json()

    # --- Settlement ---

    def create_escrow(
        self,
        *,
        provider_id: str,
        amount: int,
        task_id: str | None = None,
        task_type: str | None = None,
        ttl_minutes: int | None = None,
        group_id: str | None = None,
        depends_on: list[str] | None = None,
        deliverables: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        url = _join(self.base_url, "/v1/exchange/escrow")
        payload: dict[str, Any] = {"provider_id": provider_id, "amount": amount}
        if task_id is not None:
            payload["task_id"] = task_id
        if task_type is not None:
            payload["task_type"] = task_type
        if ttl_minutes is not None:
            payload["ttl_minutes"] = ttl_minutes
        if group_id is not None:
            payload["group_id"] = group_id
        if depends_on is not None:
            payload["depends_on"] = depends_on
        if deliverables is not None:
            payload["deliverables"] = deliverables

        with self._client(idempotency_key=idempotency_key) as c:
            r = c.post(url, json=payload)
            r.raise_for_status()
            return r.json()

    def release_escrow(self, *, escrow_id: str, idempotency_key: str | None = None) -> dict[str, Any]:
        url = _join(self.base_url, "/v1/exchange/release")
        with self._client(idempotency_key=idempotency_key) as c:
            r = c.post(url, json={"escrow_id": escrow_id})
            r.raise_for_status()
            return r.json()

    def refund_escrow(self, *, escrow_id: str, reason: str | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
        url = _join(self.base_url, "/v1/exchange/refund")
        payload: dict[str, Any] = {"escrow_id": escrow_id}
        if reason is not None:
            payload["reason"] = reason
        with self._client(idempotency_key=idempotency_key) as c:
            r = c.post(url, json=payload)
            r.raise_for_status()
            return r.json()

    def dispute_escrow(self, *, escrow_id: str, reason: str) -> dict[str, Any]:
        url = _join(self.base_url, "/v1/exchange/dispute")
        with self._client() as c:
            r = c.post(url, json={"escrow_id": escrow_id, "reason": reason})
            r.raise_for_status()
            return r.json()

    def resolve_escrow(self, *, escrow_id: str, resolution: str, strategy: str | None = None) -> dict[str, Any]:
        url = _join(self.base_url, "/v1/exchange/resolve")
        body: dict[str, Any] = {"escrow_id": escrow_id, "resolution": resolution}
        if strategy is not None:
            body["strategy"] = strategy
        with self._client() as c:
            r = c.post(url, json=body)
            r.raise_for_status()
            return r.json()

    def get_balance(self) -> dict[str, Any]:
        url = _join(self.base_url, "/v1/exchange/balance")
        with self._client() as c:
            r = c.get(url)
            r.raise_for_status()
            return r.json()

    def get_transactions(self, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        url = _join(self.base_url, "/v1/exchange/transactions")
        with self._client() as c:
            r = c.get(url, params={"limit": limit, "offset": offset})
            r.raise_for_status()
            return r.json()

    def get_escrow(self, *, escrow_id: str) -> dict[str, Any]:
        url = _join(self.base_url, f"/v1/exchange/escrows/{escrow_id}")
        with self._client() as c:
            r = c.get(url)
            r.raise_for_status()
            return r.json()

    def list_escrows(
        self,
        *,
        task_id: str | None = None,
        group_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        url = _join(self.base_url, "/v1/exchange/escrows")
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if task_id is not None:
            params["task_id"] = task_id
        if group_id is not None:
            params["group_id"] = group_id
        if status is not None:
            params["status"] = status
        with self._client() as c:
            r = c.get(url, params=params)
            r.raise_for_status()
            return r.json()

    def batch_create_escrow(
        self,
        *,
        escrows: list[dict[str, Any]],
        group_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        url = _join(self.base_url, "/v1/exchange/escrow/batch")
        payload: dict[str, Any] = {"escrows": escrows}
        if group_id is not None:
            payload["group_id"] = group_id
        with self._client(idempotency_key=idempotency_key) as c:
            r = c.post(url, json=payload)
            r.raise_for_status()
            return r.json()
