"""DID Resolution Service for ``did:web`` identifiers.

Resolves ``did:web`` DIDs to DID documents by fetching the corresponding
HTTPS URL and caches results with a configurable TTL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import unquote

import httpx


class DIDResolutionError(Exception):
    """Raised when DID resolution fails."""


class KeyNotFoundError(Exception):
    """Raised when a verification method key ID is not in the DID document."""


@dataclass
class VerificationMethod:
    """A single verification key from a DID document."""

    id: str
    type: str
    controller: str
    public_key_multibase: str


@dataclass
class DIDDocument:
    """Parsed DID document with extracted verification methods."""

    id: str
    verification_methods: list[VerificationMethod]
    service_endpoints: list[str]
    controller: Optional[str]
    raw: dict
    resolved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class DIDResolver:
    """Resolves ``did:web`` identifiers to DID documents.

    Parameters
    ----------
    cache_ttl_seconds:
        How long resolved documents are cached (default 300s).
    http_timeout:
        Timeout in seconds for the HTTP fetch (default 10).
    http_client:
        Optional pre-configured ``httpx.Client`` (useful for testing).
    """

    def __init__(
        self,
        cache_ttl_seconds: int = 300,
        http_timeout: int = 10,
        http_client: httpx.Client | None = None,
    ):
        self.cache_ttl = timedelta(seconds=cache_ttl_seconds)
        self.http_timeout = http_timeout
        self._cache: dict[str, DIDDocument] = {}
        self._client = http_client

    # ------------------------------------------------------------------
    # URL conversion
    # ------------------------------------------------------------------

    @staticmethod
    def did_to_url(did: str) -> str:
        """Convert a ``did:web`` identifier to its HTTPS document URL.

        Examples::

            did:web:example.com              -> https://example.com/.well-known/did.json
            did:web:example.com:agent:001    -> https://example.com/agent/001/did.json
            did:web:example.com%3A8443       -> https://example.com:8443/.well-known/did.json
        """
        if not did.startswith("did:web:"):
            raise DIDResolutionError(f"Not a did:web identifier: {did}")

        remainder = did[len("did:web:"):]
        parts = remainder.split(":")
        domain = unquote(parts[0])
        path_segments = [unquote(p) for p in parts[1:]]

        if path_segments:
            path = "/".join(path_segments) + "/did.json"
        else:
            path = ".well-known/did.json"

        return f"https://{domain}/{path}"

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve(self, did: str, force_refresh: bool = False) -> DIDDocument:
        """Resolve a ``did:web`` to a :class:`DIDDocument`.

        Returns a cached copy when available and within TTL unless
        *force_refresh* is ``True``.
        """
        if not force_refresh:
            cached = self._cache.get(did)
            if cached is not None:
                age = datetime.now(timezone.utc) - cached.resolved_at
                if age < self.cache_ttl:
                    return cached

        url = self.did_to_url(did)
        try:
            client = self._client or httpx.Client(verify=True, timeout=self.http_timeout)
            try:
                resp = client.get(url)
                resp.raise_for_status()
            finally:
                if self._client is None:
                    client.close()
        except httpx.HTTPStatusError as exc:
            raise DIDResolutionError(
                f"HTTP {exc.response.status_code} fetching DID document from {url}"
            ) from exc
        except httpx.RequestError as exc:
            raise DIDResolutionError(f"Failed to fetch DID document from {url}: {exc}") from exc

        try:
            data = resp.json()
        except ValueError as exc:
            raise DIDResolutionError(f"Invalid JSON in DID document from {url}") from exc

        doc = self._parse_document(did, data)
        self._cache[did] = doc
        return doc

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def extract_verification_method(self, doc: DIDDocument, key_id: str) -> VerificationMethod:
        """Look up a specific verification method by its full key ID."""
        for vm in doc.verification_methods:
            if vm.id == key_id:
                return vm
        raise KeyNotFoundError(f"Key {key_id!r} not found in DID document {doc.id}")

    def clear_cache(self) -> None:
        self._cache.clear()

    def evict_expired(self) -> None:
        """Remove cache entries whose TTL has elapsed."""
        now = datetime.now(timezone.utc)
        expired = [k for k, v in self._cache.items() if (now - v.resolved_at) >= self.cache_ttl]
        for k in expired:
            del self._cache[k]

    # ------------------------------------------------------------------
    # Internal parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_document(did: str, data: dict) -> DIDDocument:
        doc_id = data.get("id", did)
        vms: list[VerificationMethod] = []
        for vm_raw in data.get("verificationMethod", []):
            pkm = vm_raw.get("publicKeyMultibase", "")
            vms.append(
                VerificationMethod(
                    id=vm_raw.get("id", ""),
                    type=vm_raw.get("type", ""),
                    controller=vm_raw.get("controller", ""),
                    public_key_multibase=pkm,
                )
            )
        services: list[str] = []
        for svc in data.get("service", []):
            endpoint = svc.get("serviceEndpoint", "")
            if isinstance(endpoint, str):
                services.append(endpoint)
            elif isinstance(endpoint, list):
                services.extend(endpoint)
        controller = data.get("controller")
        return DIDDocument(
            id=doc_id,
            verification_methods=vms,
            service_endpoints=services,
            controller=controller,
            raw=data,
        )
