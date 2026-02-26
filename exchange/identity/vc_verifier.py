"""Verifiable Credential Verification Engine.

Verifies VCs according to the subset of the W3C VC Data Model v2.0 used by
A2A-SE KYA Agent Cards.  No JSON-LD processing; only Ed25519 proof
verification against resolved issuer DIDs and a trusted-issuer allow-list.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from exchange.identity.crypto import canonicalize_json, verify_ed25519_signature
from exchange.identity.did_resolver import DIDResolutionError, DIDResolver, KeyNotFoundError


class VerificationStatus(Enum):
    VALID = "valid"
    EXPIRED = "expired"
    NOT_YET_VALID = "not_yet_valid"
    INVALID_SIGNATURE = "invalid_signature"
    UNTRUSTED_ISSUER = "untrusted_issuer"
    ISSUER_UNRESOLVABLE = "issuer_unresolvable"
    MALFORMED = "malformed"


@dataclass
class CredentialVerificationResult:
    status: VerificationStatus
    credential_claim: Optional[str] = None
    issuer_did: Optional[str] = None
    issuer_name: Optional[str] = None
    valid_from: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    error_detail: Optional[str] = None


@dataclass
class AgentVerificationResult:
    verified: bool
    kya_level_claimed: int
    kya_level_verified: int
    credential_results: list[CredentialVerificationResult] = field(default_factory=list)
    card_signature_valid: bool = False
    did_resolved: bool = False
    error_summary: Optional[str] = None


_REQUIRED_VC_FIELDS = {"type", "issuer", "claim", "credential_subject", "valid_from", "expires_at", "proof"}
_REQUIRED_PROOF_FIELDS = {"type", "verification_method", "proof_value"}


class VCVerifier:
    """Verifies Verifiable Credentials and Agent Card identity claims.

    Parameters
    ----------
    did_resolver:
        A :class:`DIDResolver` used for resolving issuer DIDs.
    trusted_issuers:
        Set of issuer DIDs accepted for KYA Level 2 verification.
    """

    def __init__(self, did_resolver: DIDResolver, trusted_issuers: set[str]):
        self.did_resolver = did_resolver
        self.trusted_issuers = set(trusted_issuers)

    # ------------------------------------------------------------------
    # Single credential
    # ------------------------------------------------------------------

    def verify_credential(self, credential: dict) -> CredentialVerificationResult:
        """Verify a single Verifiable Credential."""
        missing = _REQUIRED_VC_FIELDS - set(credential.keys())
        if missing:
            return CredentialVerificationResult(
                status=VerificationStatus.MALFORMED,
                error_detail=f"Missing fields: {sorted(missing)}",
            )

        proof = credential.get("proof", {})
        missing_proof = _REQUIRED_PROOF_FIELDS - set(proof.keys())
        if missing_proof:
            return CredentialVerificationResult(
                status=VerificationStatus.MALFORMED,
                error_detail=f"Missing proof fields: {sorted(missing_proof)}",
            )

        try:
            valid_from = _parse_dt(credential["valid_from"])
            expires_at = _parse_dt(credential["expires_at"])
        except (ValueError, TypeError) as exc:
            return CredentialVerificationResult(
                status=VerificationStatus.MALFORMED,
                error_detail=f"Invalid datetime: {exc}",
            )

        now = datetime.now(timezone.utc)
        if now < valid_from:
            return CredentialVerificationResult(
                status=VerificationStatus.NOT_YET_VALID,
                credential_claim=credential.get("claim"),
                issuer_did=credential.get("issuer"),
                valid_from=valid_from,
                expires_at=expires_at,
            )
        if now >= expires_at:
            return CredentialVerificationResult(
                status=VerificationStatus.EXPIRED,
                credential_claim=credential.get("claim"),
                issuer_did=credential.get("issuer"),
                valid_from=valid_from,
                expires_at=expires_at,
            )

        issuer_did = credential["issuer"]
        try:
            doc = self.did_resolver.resolve(issuer_did)
        except DIDResolutionError as exc:
            return CredentialVerificationResult(
                status=VerificationStatus.ISSUER_UNRESOLVABLE,
                credential_claim=credential.get("claim"),
                issuer_did=issuer_did,
                valid_from=valid_from,
                expires_at=expires_at,
                error_detail=str(exc),
            )

        vm_id = proof["verification_method"]
        try:
            vm = self.did_resolver.extract_verification_method(doc, vm_id)
        except KeyNotFoundError as exc:
            return CredentialVerificationResult(
                status=VerificationStatus.INVALID_SIGNATURE,
                credential_claim=credential.get("claim"),
                issuer_did=issuer_did,
                valid_from=valid_from,
                expires_at=expires_at,
                error_detail=str(exc),
            )

        payload = {k: v for k, v in credential.items() if k != "proof"}
        message = canonicalize_json(payload)
        if not verify_ed25519_signature(message, proof["proof_value"], vm.public_key_multibase):
            return CredentialVerificationResult(
                status=VerificationStatus.INVALID_SIGNATURE,
                credential_claim=credential.get("claim"),
                issuer_did=issuer_did,
                valid_from=valid_from,
                expires_at=expires_at,
            )

        if issuer_did not in self.trusted_issuers:
            return CredentialVerificationResult(
                status=VerificationStatus.UNTRUSTED_ISSUER,
                credential_claim=credential.get("claim"),
                issuer_did=issuer_did,
                issuer_name=credential.get("issuer_name"),
                valid_from=valid_from,
                expires_at=expires_at,
            )

        return CredentialVerificationResult(
            status=VerificationStatus.VALID,
            credential_claim=credential.get("claim"),
            issuer_did=issuer_did,
            issuer_name=credential.get("issuer_name"),
            valid_from=valid_from,
            expires_at=expires_at,
        )

    # ------------------------------------------------------------------
    # Full Agent Card
    # ------------------------------------------------------------------

    def verify_agent_card(self, card: dict) -> AgentVerificationResult:
        """Verify all KYA claims on an Agent Card."""
        kya_level = card.get("kya_level", 0)
        result = AgentVerificationResult(
            verified=True,
            kya_level_claimed=kya_level,
            kya_level_verified=0,
        )

        if kya_level == 0:
            result.kya_level_verified = 0
            return result

        # Level 1+: resolve agent DID and verify card signature
        identity = card.get("identity", {})
        agent_did = card.get("id", "")
        try:
            doc = self.did_resolver.resolve(agent_did)
            result.did_resolved = True
        except DIDResolutionError as exc:
            result.verified = False
            result.error_summary = f"DID resolution failed: {exc}"
            return result

        metadata = card.get("metadata", {})
        card_sig = metadata.get("card_signature", {})
        if not card_sig:
            result.verified = False
            result.error_summary = "Missing card_signature in metadata"
            return result

        sig_vm_id = card_sig.get("verification_method", "")
        try:
            vm = self.did_resolver.extract_verification_method(doc, sig_vm_id)
        except KeyNotFoundError as exc:
            result.verified = False
            result.error_summary = str(exc)
            return result

        card_for_signing = _card_without_signature(card)
        message = canonicalize_json(card_for_signing)
        if verify_ed25519_signature(message, card_sig.get("proof_value", ""), vm.public_key_multibase):
            result.card_signature_valid = True
            result.kya_level_verified = 1
        else:
            result.verified = False
            result.error_summary = "Card signature verification failed"
            return result

        if kya_level < 2:
            return result

        # Level 2: verify attestations
        attestations = card.get("attestations", [])
        if not attestations:
            result.verified = False
            result.kya_level_verified = 1
            result.error_summary = "No attestations provided for Level 2"
            return result

        has_trusted_valid = False
        for att in attestations:
            cr = self.verify_credential(att)
            result.credential_results.append(cr)
            if cr.status == VerificationStatus.VALID:
                has_trusted_valid = True

        if has_trusted_valid:
            result.kya_level_verified = 2
        else:
            result.verified = False
            result.kya_level_verified = 1
            result.error_summary = "No valid trusted-issuer attestation"

        return result

    # ------------------------------------------------------------------
    # Trust management
    # ------------------------------------------------------------------

    def add_trusted_issuer(self, issuer_did: str) -> None:
        self.trusted_issuers.add(issuer_did)

    def remove_trusted_issuer(self, issuer_did: str) -> None:
        self.trusted_issuers.discard(issuer_did)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _parse_dt(value) -> datetime:
    """Parse an ISO-8601 datetime string or pass through a datetime object."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    return datetime.fromisoformat(str(value)).replace(tzinfo=timezone.utc)


def _card_without_signature(card: dict) -> dict:
    """Return a copy of the card with ``metadata.card_signature`` removed."""
    out = copy.deepcopy(card)
    meta = out.get("metadata", {})
    meta.pop("card_signature", None)
    out["metadata"] = meta
    return out
