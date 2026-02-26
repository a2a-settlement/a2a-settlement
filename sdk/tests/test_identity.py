"""Tests for the SDK AgentCardBuilder."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from a2a_settlement.identity import AgentCardBuilder


def _keypair():
    from nacl.signing import SigningKey

    sk = SigningKey.generate()
    return bytes(sk), bytes(sk.verify_key)


class TestAgentCardBuilder:
    def test_level0_minimal(self):
        card = (
            AgentCardBuilder("test-bot", "did:web:test.example")
            .set_kya_level(0)
            .set_description("A test bot")
            .set_exchange_url("https://ex.test/api/v1")
            .build()
        )
        assert card["name"] == "test-bot"
        assert card["kya_level"] == 0
        assert card["identity"]["type"] == "api_key"
        assert "card_signature" not in card["metadata"]

    def test_level1_signed(self):
        priv, _pub = _keypair()
        card = (
            AgentCardBuilder("l1-bot", "did:web:test.example")
            .set_kya_level(1)
            .set_description("Level 1 bot")
            .set_exchange_url("https://ex.test/api/v1")
            .add_skill("analysis")
            .sign(priv)
            .build()
        )
        assert card["kya_level"] == 1
        assert card["identity"]["type"] == "did:web"
        assert card["metadata"]["card_signature"]["proof_value"]
        assert "analysis" in card["capabilities"]["skills"]

    def test_level2_with_attestation(self):
        priv, _pub = _keypair()
        now = datetime.now(timezone.utc)
        card = (
            AgentCardBuilder("l2-bot", "did:web:test.example")
            .set_kya_level(2)
            .set_description("Level 2 bot")
            .set_exchange_url("https://ex.test/api/v1")
            .add_attestation(
                issuer="did:web:issuer.example",
                issuer_name="Test Issuer",
                claim="KYA-Level-2-Verified",
                valid_from=now - timedelta(days=1),
                expires_at=now + timedelta(days=180),
                proof_value="fakesig",
            )
            .sign(priv)
            .build()
        )
        assert card["kya_level"] == 2
        assert len(card["attestations"]) == 1
        assert card["attestations"][0]["claim"] == "KYA-Level-2-Verified"

    def test_level1_unsigned_raises(self):
        with pytest.raises(ValueError, match="must be signed"):
            (
                AgentCardBuilder("bot", "did:web:test.example")
                .set_kya_level(1)
                .set_exchange_url("https://ex.test")
                .build()
            )

    def test_level2_no_attestation_raises(self):
        priv, _ = _keypair()
        with pytest.raises(ValueError, match="attestation"):
            (
                AgentCardBuilder("bot", "did:web:test.example")
                .set_kya_level(2)
                .set_exchange_url("https://ex.test")
                .sign(priv)
                .build()
            )

    def test_hitl_threshold(self):
        card = (
            AgentCardBuilder("bot", "did:web:test.example")
            .set_kya_level(0)
            .set_exchange_url("https://ex.test")
            .set_hitl_threshold(5000, "EUR")
            .build()
        )
        hitl = card["settlement"]["hitl_threshold"]
        assert hitl["enabled"] is True
        assert hitl["amount"] == 5000
        assert hitl["currency"] == "EUR"

    def test_merkle_proof(self):
        card = (
            AgentCardBuilder("bot", "did:web:test.example")
            .set_kya_level(0)
            .set_exchange_url("https://ex.test")
            .set_merkle_proof(True, 120)
            .build()
        )
        assert card["settlement"]["merkle_proof_enabled"] is True
        assert card["settlement"]["audit_root_frequency_seconds"] == 120

    def test_policies(self):
        card = (
            AgentCardBuilder("bot", "did:web:test.example")
            .set_kya_level(0)
            .set_exchange_url("https://ex.test")
            .set_policies(jurisdiction="US", data_retention="90d")
            .build()
        )
        assert card["policies"]["jurisdiction"] == "US"

    def test_sign_verify_roundtrip(self):
        """The card signature can be verified with the exchange crypto module."""
        from exchange.identity.crypto import canonicalize_json, generate_keypair, verify_ed25519_signature

        priv, pub = generate_keypair()
        card = (
            AgentCardBuilder("roundtrip", "did:web:test.example")
            .set_kya_level(1)
            .set_exchange_url("https://ex.test")
            .sign(priv)
            .build()
        )
        sig = card["metadata"]["card_signature"]["proof_value"]
        card_no_sig = dict(card)
        card_no_sig["metadata"] = {k: v for k, v in card["metadata"].items() if k != "card_signature"}
        msg = canonicalize_json(card_no_sig)
        assert verify_ed25519_signature(msg, sig, pub)
