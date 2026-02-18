from __future__ import annotations

from dataclasses import dataclass
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

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self.timeout_s, headers=self._headers())

    def register_account(
        self,
        *,
        bot_name: str,
        developer_id: str,
        description: str | None = None,
        skills: list[str] | None = None,
    ) -> dict[str, Any]:
        url = _join(self.base_url, "/v1/accounts/register")
        payload: dict[str, Any] = {"bot_name": bot_name, "developer_id": developer_id}
        if description is not None:
            payload["description"] = description
        if skills is not None:
            payload["skills"] = skills

        with self._client() as c:
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

    def create_escrow(
        self,
        *,
        provider_id: str,
        amount: int,
        task_id: str | None = None,
        task_type: str | None = None,
        ttl_minutes: int | None = None,
    ) -> dict[str, Any]:
        url = _join(self.base_url, "/v1/exchange/escrow")
        payload: dict[str, Any] = {"provider_id": provider_id, "amount": amount}
        if task_id is not None:
            payload["task_id"] = task_id
        if task_type is not None:
            payload["task_type"] = task_type
        if ttl_minutes is not None:
            payload["ttl_minutes"] = ttl_minutes

        with self._client() as c:
            r = c.post(url, json=payload)
            r.raise_for_status()
            return r.json()

    def release_escrow(self, *, escrow_id: str) -> dict[str, Any]:
        url = _join(self.base_url, "/v1/exchange/release")
        with self._client() as c:
            r = c.post(url, json={"escrow_id": escrow_id})
            r.raise_for_status()
            return r.json()

    def refund_escrow(self, *, escrow_id: str, reason: str | None = None) -> dict[str, Any]:
        url = _join(self.base_url, "/v1/exchange/refund")
        payload: dict[str, Any] = {"escrow_id": escrow_id}
        if reason is not None:
            payload["reason"] = reason
        with self._client() as c:
            r = c.post(url, json=payload)
            r.raise_for_status()
            return r.json()

    def dispute_escrow(self, *, escrow_id: str, reason: str) -> dict[str, Any]:
        url = _join(self.base_url, "/v1/exchange/dispute")
        with self._client() as c:
            r = c.post(url, json={"escrow_id": escrow_id, "reason": reason})
            r.raise_for_status()
            return r.json()

    def resolve_escrow(self, *, escrow_id: str, resolution: str) -> dict[str, Any]:
        url = _join(self.base_url, "/v1/exchange/resolve")
        with self._client() as c:
            r = c.post(url, json={"escrow_id": escrow_id, "resolution": resolution})
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

