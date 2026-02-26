"""Tests for the KYA-aware agent registration endpoint."""

from __future__ import annotations

import importlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from exchange.identity.crypto import (
    canonicalize_json,
    generate_keypair,
    sign_ed25519,
)
from exchange.identity.did_resolver import DIDResolver

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ISSUER_DID = "did:web:issuer.example.com"
AGENT_DID = "did:web:agent.example.com"
KEY_ID_ISSUER = f"{ISSUER_DID}#key-1"
KEY_ID_AGENT = f"{AGENT_DID}#key-1"


def _make_did_doc(did: str, key_id: str, pub_multibase: str) -> dict:
    return {
        "id": did,
        "verificationMethod": [
            {
                "id": key_id,
                "type": "Ed25519VerificationKey2020",
                "controller": did,
                "publicKeyMultibase": pub_multibase,
            }
        ],
    }


def _mock_httpx_client(did_docs: dict[str, dict]):
    """Return a mock httpx.Client backed by *did_docs*."""
    def _get(url: str, **kw):
        for did, doc in did_docs.items():
            expected = DIDResolver.did_to_url(did)
            if url == expected:
                return httpx.Response(200, json=doc, request=httpx.Request("GET", url))
        return httpx.Response(404, json={}, request=httpx.Request("GET", url))

    client = MagicMock(spec=httpx.Client)
    client.get.side_effect = _get
    return client


def _build_card_dict(
    agent_priv: bytes,
    agent_pub: str,
    issuer_priv: bytes | None = None,
    kya_level: int = 0,
    name: str = "test-agent",
) -> dict:
    now = datetime.now(timezone.utc)
    attestations = []
    if kya_level >= 2 and issuer_priv is not None:
        cred = {
            "type": "VerifiableCredential",
            "issuer": ISSUER_DID,
            "issuer_name": "Test Issuer",
            "claim": "KYA-Level-2-Verified",
            "credential_subject": AGENT_DID,
            "valid_from": (now - timedelta(days=1)).isoformat(),
            "expires_at": (now + timedelta(days=180)).isoformat(),
            "proof": {
                "type": "Ed25519Signature2020",
                "created": now.isoformat(),
                "verification_method": KEY_ID_ISSUER,
                "proof_value": "",
            },
        }
        payload = {k: v for k, v in cred.items() if k != "proof"}
        cred["proof"]["proof_value"] = sign_ed25519(canonicalize_json(payload), issuer_priv)
        attestations.append(cred)

    card: dict = {
        "protocol_version": "2026.1",
        "name": name,
        "id": AGENT_DID,
        "description": "Test agent",
        "kya_level": kya_level,
        "identity": {"type": "did:web" if kya_level >= 1 else "api_key"},
        "attestations": attestations,
        "settlement": {"supported_methods": ["escrow-v1"], "exchange_url": "https://ex.test"},
        "capabilities": {"skills": ["test"]},
        "policies": {},
        "metadata": {"created": now.isoformat(), "updated": now.isoformat()},
    }

    if kya_level >= 1:
        meta = card["metadata"]
        sig = sign_ed25519(canonicalize_json(card), agent_priv)
        meta["card_signature"] = {
            "type": "Ed25519Signature2020",
            "verification_method": KEY_ID_AGENT,
            "proof_value": sig,
        }

    return card


@pytest.fixture()
def keys():
    issuer_priv, issuer_pub = generate_keypair()
    agent_priv, agent_pub = generate_keypair()
    return {
        "issuer_priv": issuer_priv,
        "issuer_pub": issuer_pub,
        "agent_priv": agent_priv,
        "agent_pub": agent_pub,
    }


@pytest.fixture()
def exchange_app_kya(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, keys):
    monkeypatch.setenv("A2A_EXCHANGE_DATABASE_URL", f"sqlite:///{tmp_path / 'exchange.db'}")
    monkeypatch.setenv("A2A_EXCHANGE_AUTO_CREATE_SCHEMA", "true")
    monkeypatch.setenv("A2A_EXCHANGE_STARTER_TOKENS", "100")
    monkeypatch.setenv("A2A_EXCHANGE_FEE_PERCENT", "0.25")
    monkeypatch.setenv("A2A_EXCHANGE_REGISTER_RATE_LIMIT_HOUR", "0")
    monkeypatch.setenv("A2A_EXCHANGE_REGISTER_RATE_LIMIT_DAY", "0")
    monkeypatch.setenv("A2A_EXCHANGE_INVITE_CODE", "")
    monkeypatch.setenv("A2A_EXCHANGE_KYA_ENABLED", "true")

    import exchange.config as config_mod
    import exchange.ratelimit as ratelimit_mod
    import exchange.observers as observers_mod
    import exchange.spending_guard as spending_guard_mod
    import exchange.tasks as tasks_mod
    import exchange.webhooks as webhooks_mod
    import exchange.identity.issuer_registry as issuer_registry_mod
    import exchange.routes.accounts as accounts_mod
    import exchange.routes.settlement as settlement_mod
    import exchange.routes.webhooks as webhooks_routes_mod
    import exchange.routes.kya_admin as kya_admin_mod
    import exchange.app as app_mod

    importlib.reload(config_mod)
    importlib.reload(ratelimit_mod)
    importlib.reload(observers_mod)
    importlib.reload(spending_guard_mod)
    importlib.reload(tasks_mod)
    importlib.reload(webhooks_mod)
    importlib.reload(issuer_registry_mod)
    importlib.reload(accounts_mod)
    importlib.reload(settlement_mod)
    importlib.reload(webhooks_routes_mod)
    importlib.reload(kya_admin_mod)
    importlib.reload(app_mod)

    did_docs = {
        ISSUER_DID: _make_did_doc(ISSUER_DID, KEY_ID_ISSUER, keys["issuer_pub"]),
        AGENT_DID: _make_did_doc(AGENT_DID, KEY_ID_AGENT, keys["agent_pub"]),
    }
    mock_client = _mock_httpx_client(did_docs)
    resolver = DIDResolver(http_client=mock_client)

    accounts_mod._did_resolver = resolver

    app = app_mod.create_app()

    # Seed trusted issuers after create_app triggers create_all via lifespan.
    # Use TestClient context to invoke lifespan, then seed.
    from fastapi.testclient import TestClient as _TC

    with _TC(app):
        from exchange.identity.issuer_registry import IssuerRegistry
        from exchange.config import get_session

        gen = get_session()
        session = next(gen)
        try:
            with session.begin():
                IssuerRegistry().seed_initial(session)
                IssuerRegistry().add_issuer(
                    session,
                    did=ISSUER_DID,
                    name="Test Issuer",
                    issuer_type="auditor",
                    accepted_claims=["KYA-Level-2-Verified"],
                    added_by="test",
                )
        finally:
            try:
                next(gen)
            except StopIteration:
                pass

    return app


class TestLevel0Registration:
    def test_success(self, exchange_app_kya, keys):
        client = TestClient(exchange_app_kya, raise_server_exceptions=False)
        card = _build_card_dict(keys["agent_priv"], keys["agent_pub"], kya_level=0)
        resp = client.post("/v1/accounts/register-agent", json=card)
        assert resp.status_code == 201
        data = resp.json()
        assert data["kya_level_verified"] == 0
        assert data["api_key"].startswith("ate_")


class TestLevel1Registration:
    def test_valid_signature(self, exchange_app_kya, keys):
        client = TestClient(exchange_app_kya, raise_server_exceptions=False)
        card = _build_card_dict(keys["agent_priv"], keys["agent_pub"], kya_level=1)
        resp = client.post("/v1/accounts/register-agent", json=card)
        assert resp.status_code == 201
        data = resp.json()
        assert data["kya_level_verified"] == 1
        assert data["card_signature_valid"] is True

    def test_bad_signature(self, exchange_app_kya, keys):
        client = TestClient(exchange_app_kya, raise_server_exceptions=False)
        card = _build_card_dict(keys["agent_priv"], keys["agent_pub"], kya_level=1)
        card["metadata"]["card_signature"]["proof_value"] = "badsig"
        resp = client.post("/v1/accounts/register-agent", json=card)
        assert resp.status_code == 401


class TestLevel2Registration:
    def test_valid(self, exchange_app_kya, keys):
        client = TestClient(exchange_app_kya, raise_server_exceptions=False)
        card = _build_card_dict(
            keys["agent_priv"],
            keys["agent_pub"],
            issuer_priv=keys["issuer_priv"],
            kya_level=2,
        )
        resp = client.post("/v1/accounts/register-agent", json=card)
        assert resp.status_code == 201
        data = resp.json()
        assert data["kya_level_verified"] == 2
        assert len(data["credential_results"]) == 1
        assert data["credential_results"][0]["status"] == "valid"

    def test_no_trusted_attestation_downgrades(self, exchange_app_kya, keys):
        client = TestClient(exchange_app_kya, raise_server_exceptions=False)
        wrong_priv, _ = generate_keypair()
        card = _build_card_dict(
            keys["agent_priv"],
            keys["agent_pub"],
            issuer_priv=wrong_priv,
            kya_level=2,
        )
        resp = client.post("/v1/accounts/register-agent", json=card)
        assert resp.status_code == 201
        data = resp.json()
        assert data["kya_level_verified"] == 1


class TestCardEndpoints:
    def test_get_card(self, exchange_app_kya, keys):
        client = TestClient(exchange_app_kya, raise_server_exceptions=False)
        card = _build_card_dict(keys["agent_priv"], keys["agent_pub"], kya_level=0)
        reg = client.post("/v1/accounts/register-agent", json=card)
        assert reg.status_code == 201
        account_id = reg.json()["account"]["id"]

        resp = client.get(f"/v1/accounts/{account_id}/card")
        assert resp.status_code == 200
        assert resp.json()["card"]["name"] == "test-agent"

    def test_get_card_not_found(self, exchange_app_kya):
        client = TestClient(exchange_app_kya, raise_server_exceptions=False)
        resp = client.get("/v1/accounts/nonexistent/card")
        assert resp.status_code == 404

    def test_get_verification_status(self, exchange_app_kya, keys):
        client = TestClient(exchange_app_kya, raise_server_exceptions=False)
        card = _build_card_dict(keys["agent_priv"], keys["agent_pub"], kya_level=0)
        reg = client.post("/v1/accounts/register-agent", json=card)
        account_id = reg.json()["account"]["id"]

        resp = client.get(f"/v1/accounts/{account_id}/verification")
        assert resp.status_code == 200
        assert resp.json()["kya_level_verified"] == 0


class TestDuplicateRegistration:
    def test_duplicate_name(self, exchange_app_kya, keys):
        client = TestClient(exchange_app_kya, raise_server_exceptions=False)
        card = _build_card_dict(keys["agent_priv"], keys["agent_pub"], kya_level=0)
        client.post("/v1/accounts/register-agent", json=card)
        resp = client.post("/v1/accounts/register-agent", json=card)
        assert resp.status_code == 409
