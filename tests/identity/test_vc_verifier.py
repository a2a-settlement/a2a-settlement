"""Tests for the VC Verification Engine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import httpx
import pytest

from exchange.identity.crypto import (
    canonicalize_json,
    generate_keypair,
    sign_ed25519,
)
from exchange.identity.did_resolver import DIDResolver
from exchange.identity.vc_verifier import (
    AgentVerificationResult,
    VerificationStatus,
    VCVerifier,
)

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


def _mock_resolver(did_docs: dict[str, dict]) -> DIDResolver:
    """Return a resolver backed by *did_docs* mapping DID -> raw doc JSON."""
    def _get(url: str) -> httpx.Response:
        for did, doc in did_docs.items():
            expected_url = DIDResolver.did_to_url(did)
            if url == expected_url:
                return httpx.Response(200, json=doc, request=httpx.Request("GET", url))
        return httpx.Response(404, json={}, request=httpx.Request("GET", url))

    client = MagicMock(spec=httpx.Client)
    client.get.side_effect = _get
    return DIDResolver(http_client=client)


def _sign_credential(cred: dict, priv_key: bytes) -> dict:
    """Set the proof.proof_value by signing the credential (minus proof)."""
    payload = {k: v for k, v in cred.items() if k != "proof"}
    sig = sign_ed25519(canonicalize_json(payload), priv_key)
    cred["proof"]["proof_value"] = sig
    return cred


def _valid_credential(issuer_priv: bytes, agent_did: str = AGENT_DID) -> dict:
    now = datetime.now(timezone.utc)
    cred = {
        "type": "VerifiableCredential",
        "issuer": ISSUER_DID,
        "issuer_name": "Test Issuer",
        "claim": "KYA-Level-2-Verified",
        "credential_subject": agent_did,
        "valid_from": (now - timedelta(days=1)).isoformat(),
        "expires_at": (now + timedelta(days=180)).isoformat(),
        "proof": {
            "type": "Ed25519Signature2020",
            "verification_method": KEY_ID_ISSUER,
            "proof_value": "",
        },
    }
    return _sign_credential(cred, issuer_priv)


class TestVerifyCredential:
    def _setup(self):
        issuer_priv, issuer_pub = generate_keypair()
        did_docs = {ISSUER_DID: _make_did_doc(ISSUER_DID, KEY_ID_ISSUER, issuer_pub)}
        resolver = _mock_resolver(did_docs)
        verifier = VCVerifier(resolver, trusted_issuers={ISSUER_DID})
        return issuer_priv, verifier

    def test_valid(self):
        priv, verifier = self._setup()
        cred = _valid_credential(priv)
        r = verifier.verify_credential(cred)
        assert r.status == VerificationStatus.VALID
        assert r.credential_claim == "KYA-Level-2-Verified"

    def test_expired(self):
        priv, verifier = self._setup()
        now = datetime.now(timezone.utc)
        cred = _valid_credential(priv)
        cred["expires_at"] = (now - timedelta(hours=1)).isoformat()
        # Re-sign because payload changed
        cred = _sign_credential(cred, priv)
        r = verifier.verify_credential(cred)
        assert r.status == VerificationStatus.EXPIRED

    def test_not_yet_valid(self):
        priv, verifier = self._setup()
        future = datetime.now(timezone.utc) + timedelta(days=30)
        cred = _valid_credential(priv)
        cred["valid_from"] = future.isoformat()
        cred = _sign_credential(cred, priv)
        r = verifier.verify_credential(cred)
        assert r.status == VerificationStatus.NOT_YET_VALID

    def test_tampered_claim(self):
        priv, verifier = self._setup()
        cred = _valid_credential(priv)
        cred["claim"] = "TAMPERED"
        r = verifier.verify_credential(cred)
        assert r.status == VerificationStatus.INVALID_SIGNATURE

    def test_untrusted_issuer(self):
        priv, verifier = self._setup()
        verifier.trusted_issuers.clear()
        cred = _valid_credential(priv)
        r = verifier.verify_credential(cred)
        assert r.status == VerificationStatus.UNTRUSTED_ISSUER

    def test_unresolvable_issuer(self):
        _priv, _pub = generate_keypair()
        resolver = _mock_resolver({})  # no docs at all
        verifier = VCVerifier(resolver, trusted_issuers={ISSUER_DID})
        now = datetime.now(timezone.utc)
        cred = {
            "type": "VerifiableCredential",
            "issuer": "did:web:unreachable.example",
            "claim": "test",
            "credential_subject": AGENT_DID,
            "valid_from": (now - timedelta(days=1)).isoformat(),
            "expires_at": (now + timedelta(days=1)).isoformat(),
            "proof": {"type": "Ed25519Signature2020", "verification_method": "x#k", "proof_value": "x"},
        }
        r = verifier.verify_credential(cred)
        assert r.status == VerificationStatus.ISSUER_UNRESOLVABLE

    def test_malformed_missing_fields(self):
        _priv, verifier = self._setup()
        r = verifier.verify_credential({"type": "VerifiableCredential"})
        assert r.status == VerificationStatus.MALFORMED

    def test_malformed_missing_proof_fields(self):
        _priv, verifier = self._setup()
        now = datetime.now(timezone.utc)
        cred = {
            "type": "VerifiableCredential",
            "issuer": ISSUER_DID,
            "claim": "c",
            "credential_subject": "s",
            "valid_from": now.isoformat(),
            "expires_at": (now + timedelta(days=1)).isoformat(),
            "proof": {"type": "Ed25519Signature2020"},
        }
        r = verifier.verify_credential(cred)
        assert r.status == VerificationStatus.MALFORMED


class TestVerifyAgentCard:
    def _setup(self):
        issuer_priv, issuer_pub = generate_keypair()
        agent_priv, agent_pub = generate_keypair()
        did_docs = {
            ISSUER_DID: _make_did_doc(ISSUER_DID, KEY_ID_ISSUER, issuer_pub),
            AGENT_DID: _make_did_doc(AGENT_DID, KEY_ID_AGENT, agent_pub),
        }
        resolver = _mock_resolver(did_docs)
        verifier = VCVerifier(resolver, trusted_issuers={ISSUER_DID})
        return issuer_priv, agent_priv, verifier

    def _build_card(self, agent_priv, issuer_priv, kya_level=2):
        card = {
            "protocol_version": "2026.1",
            "name": "test-agent",
            "id": AGENT_DID,
            "description": "Test agent",
            "kya_level": kya_level,
            "identity": {"type": "did:web"},
            "attestations": [_valid_credential(issuer_priv, AGENT_DID)] if kya_level >= 2 else [],
            "settlement": {"supported_methods": ["escrow-v1"], "exchange_url": "https://ex.test"},
            "capabilities": {},
            "policies": {},
            "metadata": {"created": datetime.now(timezone.utc).isoformat(), "updated": datetime.now(timezone.utc).isoformat()},
        }
        # Sign the card (without card_signature present)
        msg = canonicalize_json(card)
        sig = sign_ed25519(msg, agent_priv)
        card["metadata"]["card_signature"] = {
            "type": "Ed25519Signature2020",
            "verification_method": KEY_ID_AGENT,
            "proof_value": sig,
        }
        return card

    def test_level0_no_checks(self):
        _ip, _ap, verifier = self._setup()
        card = {"kya_level": 0, "id": AGENT_DID}
        r = verifier.verify_agent_card(card)
        assert r.verified
        assert r.kya_level_verified == 0

    def test_level1_valid(self):
        _ip, agent_priv, verifier = self._setup()
        card = self._build_card(agent_priv, None, kya_level=1)
        r = verifier.verify_agent_card(card)
        assert r.verified
        assert r.kya_level_verified == 1
        assert r.card_signature_valid

    def test_level2_valid(self):
        issuer_priv, agent_priv, verifier = self._setup()
        card = self._build_card(agent_priv, issuer_priv, kya_level=2)
        r = verifier.verify_agent_card(card)
        assert r.verified
        assert r.kya_level_verified == 2

    def test_level2_no_attestations(self):
        _ip, agent_priv, verifier = self._setup()
        card = self._build_card(agent_priv, None, kya_level=1)
        card["kya_level"] = 2
        card["attestations"] = []
        # Re-sign card
        meta = card["metadata"]
        meta.pop("card_signature", None)
        msg = canonicalize_json(card)
        sig = sign_ed25519(msg, agent_priv)
        meta["card_signature"] = {
            "type": "Ed25519Signature2020",
            "verification_method": KEY_ID_AGENT,
            "proof_value": sig,
        }
        r = verifier.verify_agent_card(card)
        assert not r.verified
        assert r.kya_level_verified == 1

    def test_level1_bad_signature(self):
        _ip, agent_priv, verifier = self._setup()
        card = self._build_card(agent_priv, None, kya_level=1)
        card["metadata"]["card_signature"]["proof_value"] = "badsig"
        r = verifier.verify_agent_card(card)
        assert not r.verified

    def test_trust_management(self):
        _ip, _ap, verifier = self._setup()
        new_did = "did:web:new-issuer.example"
        verifier.add_trusted_issuer(new_did)
        assert new_did in verifier.trusted_issuers
        verifier.remove_trusted_issuer(new_did)
        assert new_did not in verifier.trusted_issuers
