from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import httpx
import pytest

from exchange.identity.did_resolver import (
    DIDDocument,
    DIDResolutionError,
    DIDResolver,
    KeyNotFoundError,
    VerificationMethod,
)

SAMPLE_DID_DOC = {
    "id": "did:web:example.com:agent:001",
    "verificationMethod": [
        {
            "id": "did:web:example.com:agent:001#key-1",
            "type": "Ed25519VerificationKey2020",
            "controller": "did:web:example.com",
            "publicKeyMultibase": "z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK",
        }
    ],
    "service": [
        {"id": "#agent", "type": "AgentService", "serviceEndpoint": "https://example.com/agent"},
    ],
}


def _mock_client(json_data: dict, status_code: int = 200) -> httpx.Client:
    """Return an httpx.Client whose GET always returns *json_data*."""
    mock = MagicMock(spec=httpx.Client)
    response = httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request("GET", "https://example.com"),
    )
    mock.get.return_value = response
    return mock


class TestDidToUrl:
    def test_domain_only(self):
        assert DIDResolver.did_to_url("did:web:example.com") == "https://example.com/.well-known/did.json"

    def test_domain_with_path(self):
        url = DIDResolver.did_to_url("did:web:example.com:agent:001")
        assert url == "https://example.com/agent/001/did.json"

    def test_percent_encoded_port(self):
        url = DIDResolver.did_to_url("did:web:example.com%3A8443")
        assert url == "https://example.com:8443/.well-known/did.json"

    def test_percent_encoded_port_with_path(self):
        url = DIDResolver.did_to_url("did:web:example.com%3A8443:path:to")
        assert url == "https://example.com:8443/path/to/did.json"

    def test_not_did_web(self):
        with pytest.raises(DIDResolutionError, match="Not a did:web"):
            DIDResolver.did_to_url("did:key:z6Mkf5rG...")


class TestResolve:
    def test_successful_resolve(self):
        client = _mock_client(SAMPLE_DID_DOC)
        resolver = DIDResolver(http_client=client)
        doc = resolver.resolve("did:web:example.com:agent:001")

        assert doc.id == "did:web:example.com:agent:001"
        assert len(doc.verification_methods) == 1
        assert doc.verification_methods[0].type == "Ed25519VerificationKey2020"
        assert len(doc.service_endpoints) == 1

    def test_cache_hit(self):
        client = _mock_client(SAMPLE_DID_DOC)
        resolver = DIDResolver(cache_ttl_seconds=60, http_client=client)
        resolver.resolve("did:web:example.com:agent:001")
        resolver.resolve("did:web:example.com:agent:001")

        assert client.get.call_count == 1

    def test_cache_miss_after_ttl(self):
        client = _mock_client(SAMPLE_DID_DOC)
        resolver = DIDResolver(cache_ttl_seconds=60, http_client=client)
        doc = resolver.resolve("did:web:example.com:agent:001")

        doc.resolved_at = datetime.now(timezone.utc) - timedelta(seconds=120)

        resolver.resolve("did:web:example.com:agent:001")
        assert client.get.call_count == 2

    def test_force_refresh(self):
        client = _mock_client(SAMPLE_DID_DOC)
        resolver = DIDResolver(http_client=client)
        resolver.resolve("did:web:example.com:agent:001")
        resolver.resolve("did:web:example.com:agent:001", force_refresh=True)
        assert client.get.call_count == 2

    def test_http_error(self):
        client = _mock_client({}, status_code=404)
        resolver = DIDResolver(http_client=client)
        with pytest.raises(DIDResolutionError, match="HTTP 404"):
            resolver.resolve("did:web:example.com")

    def test_connection_error(self):
        client = MagicMock(spec=httpx.Client)
        client.get.side_effect = httpx.ConnectError("refused")
        resolver = DIDResolver(http_client=client)
        with pytest.raises(DIDResolutionError, match="Failed to fetch"):
            resolver.resolve("did:web:unreachable.example")


class TestExtractVerificationMethod:
    def test_found(self):
        client = _mock_client(SAMPLE_DID_DOC)
        resolver = DIDResolver(http_client=client)
        doc = resolver.resolve("did:web:example.com:agent:001")
        vm = resolver.extract_verification_method(doc, "did:web:example.com:agent:001#key-1")
        assert vm.public_key_multibase == "z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK"

    def test_not_found(self):
        client = _mock_client(SAMPLE_DID_DOC)
        resolver = DIDResolver(http_client=client)
        doc = resolver.resolve("did:web:example.com:agent:001")
        with pytest.raises(KeyNotFoundError):
            resolver.extract_verification_method(doc, "did:web:example.com#nonexistent")


class TestCacheManagement:
    def test_clear_cache(self):
        client = _mock_client(SAMPLE_DID_DOC)
        resolver = DIDResolver(http_client=client)
        resolver.resolve("did:web:example.com:agent:001")
        resolver.clear_cache()
        resolver.resolve("did:web:example.com:agent:001")
        assert client.get.call_count == 2

    def test_evict_expired(self):
        client = _mock_client(SAMPLE_DID_DOC)
        resolver = DIDResolver(cache_ttl_seconds=60, http_client=client)
        doc = resolver.resolve("did:web:example.com:agent:001")
        doc.resolved_at = datetime.now(timezone.utc) - timedelta(seconds=120)
        resolver.evict_expired()
        assert "did:web:example.com:agent:001" not in resolver._cache
