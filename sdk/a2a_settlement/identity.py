"""SDK utilities for building and signing KYA-enhanced Agent Cards.

Usage::

    from a2a_settlement.identity import AgentCardBuilder

    card = (
        AgentCardBuilder("my-agent", "did:web:mycompany.com:agent:001")
        .set_kya_level(2)
        .set_description("Financial reconciliation agent")
        .set_exchange_url("https://exchange.a2a-settlement.org/api/v1")
        .add_skill("reconciliation")
        .add_attestation(
            issuer="did:web:exchange.a2a-settlement.org",
            issuer_name="A2A-SE Exchange",
            claim="KYA-Level-2-Verified",
            valid_from=datetime(2026, 2, 1, tzinfo=timezone.utc),
            expires_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            proof_value="eyJhbG...",
        )
        .set_hitl_threshold(10000)
        .sign(private_key_bytes)
        .build()
    )
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any


class AgentCardBuilder:
    """Fluent builder for KYA-Enhanced Agent Cards."""

    def __init__(self, name: str, did: str) -> None:
        self._name = name
        self._did = did
        self._description = ""
        self._kya_level = 0
        self._exchange_url = ""
        self._supported_methods: list[str] = ["escrow-v1"]
        self._skills: list[str] = []
        self._attestations: list[dict] = []
        self._hitl: dict | None = None
        self._merkle_proof: dict | None = None
        self._policies: dict[str, Any] = {}
        self._card_signature: dict | None = None
        self._frozen_card: dict | None = None

    # ------------------------------------------------------------------
    # Setters (fluent)
    # ------------------------------------------------------------------

    def set_kya_level(self, level: int) -> AgentCardBuilder:
        self._kya_level = level
        return self

    def set_description(self, desc: str) -> AgentCardBuilder:
        self._description = desc
        return self

    def set_exchange_url(self, url: str) -> AgentCardBuilder:
        self._exchange_url = url
        return self

    def add_skill(self, skill: str) -> AgentCardBuilder:
        self._skills.append(skill)
        return self

    def add_attestation(
        self,
        *,
        issuer: str,
        issuer_name: str,
        claim: str,
        valid_from: datetime,
        expires_at: datetime,
        proof_value: str,
        proof_verification_method: str | None = None,
    ) -> AgentCardBuilder:
        now = datetime.now(timezone.utc)
        self._attestations.append({
            "type": "VerifiableCredential",
            "issuer": issuer,
            "issuer_name": issuer_name,
            "claim": claim,
            "credential_subject": self._did,
            "valid_from": valid_from.isoformat(),
            "expires_at": expires_at.isoformat(),
            "proof": {
                "type": "Ed25519Signature2020",
                "created": now.isoformat(),
                "verification_method": proof_verification_method or f"{issuer}#key-1",
                "proof_value": proof_value,
            },
        })
        return self

    def set_hitl_threshold(self, amount: float, currency: str = "USD") -> AgentCardBuilder:
        self._hitl = {
            "enabled": True,
            "amount": amount,
            "currency": currency,
            "approval_credential_type": "HumanApprovalVC",
        }
        return self

    def set_merkle_proof(self, enabled: bool = True, frequency_seconds: int = 60) -> AgentCardBuilder:
        self._merkle_proof = {
            "enabled": enabled,
            "frequency_seconds": frequency_seconds,
        }
        return self

    def set_policies(self, **kwargs: Any) -> AgentCardBuilder:
        self._policies.update(kwargs)
        return self

    def sign(self, private_key_bytes: bytes) -> AgentCardBuilder:
        """Sign the card using Ed25519.

        Requires ``PyNaCl`` (install with ``pip install a2a-settlement[identity]``).
        """
        card = self._build_unsigned()
        self._frozen_card = card
        message = _canonicalize_json(card)
        sig = _sign_ed25519(message, private_key_bytes)
        self._card_signature = {
            "type": "Ed25519Signature2020",
            "verification_method": f"{self._did}#key-1",
            "proof_value": sig,
        }
        return self

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(self) -> dict:
        """Return the complete Agent Card as a dict ready for JSON serialization.

        Raises :class:`ValueError` if required fields are missing for the
        claimed KYA level.
        """
        if self._kya_level >= 1 and self._card_signature is None:
            raise ValueError("Level 1+ cards must be signed — call .sign(private_key_bytes) first")
        if self._kya_level >= 2 and not self._attestations:
            raise ValueError("Level 2 cards must have at least one attestation")

        card = self._frozen_card if self._frozen_card is not None else self._build_unsigned()
        if self._card_signature:
            card["metadata"]["card_signature"] = self._card_signature
        return card

    def _build_unsigned(self) -> dict:
        now = datetime.now(timezone.utc)
        settlement: dict[str, Any] = {
            "supported_methods": self._supported_methods,
            "exchange_url": self._exchange_url,
            "token_types": ["SETTLE"],
            "merkle_proof_enabled": False,
            "partial_completion": False,
        }
        if self._hitl:
            settlement["hitl_threshold"] = self._hitl
        if self._merkle_proof:
            settlement["merkle_proof_enabled"] = self._merkle_proof["enabled"]
            settlement["audit_root_frequency_seconds"] = self._merkle_proof["frequency_seconds"]

        identity_type = "did:web" if self._kya_level >= 1 else "api_key"

        return {
            "protocol_version": "2026.1",
            "name": self._name,
            "id": self._did,
            "description": self._description,
            "kya_level": self._kya_level,
            "identity": {"type": identity_type},
            "attestations": self._attestations,
            "settlement": settlement,
            "capabilities": {
                "skills": self._skills,
                "input_formats": ["application/json"],
                "output_formats": ["application/json"],
            },
            "policies": self._policies,
            "metadata": {
                "created": now.isoformat(),
                "updated": now.isoformat(),
            },
        }


# ---------------------------------------------------------------------------
# Internal helpers — avoid hard dependency on PyNaCl at import time
# ---------------------------------------------------------------------------


def _canonicalize_json(data: dict) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sign_ed25519(message: bytes, private_key_bytes: bytes) -> str:
    try:
        from nacl.signing import SigningKey
    except ImportError as exc:
        raise ImportError(
            "PyNaCl is required for signing. Install with: pip install a2a-settlement[identity]"
        ) from exc
    signing_key = SigningKey(private_key_bytes)
    signed = signing_key.sign(message)
    return base64.b64encode(signed.signature).decode("ascii")
