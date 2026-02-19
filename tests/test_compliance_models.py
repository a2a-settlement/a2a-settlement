from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from compliance.models import (
    AP2MandateBinding,
    AttestationHeader,
    CryptographicProof,
    MediationState,
    PreDisputeAttestationPayload,
)


def _make_payload(**overrides):
    defaults = dict(
        header=AttestationHeader(issuer_id="agent-001"),
        mandate=AP2MandateBinding(
            intent_did="did:example:intent:123",
            cart_did="did:example:cart:456",
            payment_did="did:example:pay:789",
        ),
        mediation=MediationState(escrow_id="esc-001", escrow_status="held"),
    )
    defaults.update(overrides)
    return PreDisputeAttestationPayload(**defaults)


class TestAttestationHeader:
    def test_defaults(self):
        h = AttestationHeader(issuer_id="agent-001")
        assert h.version == "1.0"
        assert h.schema_id == "urn:a2a-se:pre-dispute-attestation:v1"
        assert h.issuer_id == "agent-001"
        assert h.nonce  # non-empty UUID string

    def test_frozen(self):
        h = AttestationHeader(issuer_id="x")
        with pytest.raises(ValidationError):
            h.issuer_id = "y"


class TestAP2MandateBinding:
    def test_construction(self):
        m = AP2MandateBinding(
            intent_did="did:a",
            cart_did="did:b",
            payment_did="did:c",
        )
        assert m.intent_did == "did:a"
        assert m.cart_did == "did:b"
        assert m.payment_did == "did:c"

    def test_frozen(self):
        m = AP2MandateBinding(
            intent_did="did:a",
            cart_did="did:b",
            payment_did="did:c",
        )
        with pytest.raises(ValidationError):
            m.intent_did = "did:x"


class TestMediationState:
    def test_valid_statuses(self):
        for status in ("held", "released", "refunded", "expired", "disputed"):
            ms = MediationState(escrow_id="e1", escrow_status=status)
            assert ms.escrow_status == status

    def test_invalid_status_rejected(self):
        with pytest.raises(ValidationError):
            MediationState(escrow_id="e1", escrow_status="invalid")

    def test_optional_fields_default_none(self):
        ms = MediationState(escrow_id="e1", escrow_status="held")
        assert ms.dispute_reason is None
        assert ms.resolution_strategy is None
        assert ms.mediator_id is None


class TestCryptographicProof:
    def test_construction(self):
        cp = CryptographicProof(
            payload_hash="abc123",
            merkle_root="def456",
            merkle_leaf_index=0,
        )
        assert cp.tsa_timestamp_token is None
        assert cp.tsa_authority_url is None

    def test_frozen(self):
        cp = CryptographicProof(
            payload_hash="abc",
            merkle_root="def",
            merkle_leaf_index=0,
        )
        with pytest.raises(ValidationError):
            cp.payload_hash = "new"


class TestPreDisputeAttestationPayload:
    def test_canonical_bytes_deterministic(self):
        p1 = _make_payload()
        p2 = PreDisputeAttestationPayload(
            header=p1.header,
            mandate=p1.mandate,
            mediation=p1.mediation,
        )
        assert p1.canonical_bytes() == p2.canonical_bytes()

    def test_canonical_bytes_sorted_keys(self):
        p = _make_payload()
        raw = p.canonical_bytes()
        parsed = json.loads(raw)
        assert list(parsed.keys()) == sorted(parsed.keys())

    def test_canonical_bytes_excludes_proof(self):
        p = _make_payload(
            proof=CryptographicProof(
                payload_hash="abc",
                merkle_root="def",
                merkle_leaf_index=0,
            )
        )
        raw = json.loads(p.canonical_bytes())
        assert "proof" not in raw

    def test_canonical_bytes_no_whitespace(self):
        raw = _make_payload().canonical_bytes().decode("utf-8")
        assert " " not in raw
        assert "\n" not in raw

    def test_different_data_produces_different_bytes(self):
        p1 = _make_payload()
        p2 = _make_payload(
            mediation=MediationState(escrow_id="different", escrow_status="disputed"),
        )
        assert p1.canonical_bytes() != p2.canonical_bytes()

    def test_frozen(self):
        p = _make_payload()
        with pytest.raises(ValidationError):
            p.header = AttestationHeader(issuer_id="other")
