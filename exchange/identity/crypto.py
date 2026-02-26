"""Cryptographic utilities for KYA identity verification.

Uses Ed25519 for all signing/verification operations.
Key encoding follows the Multibase specification (base58btc with 'z' prefix).
"""

from __future__ import annotations

import base64
import json
from typing import Union

import base58
from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

# Ed25519 multicodec prefix: 0xed 0x01
_ED25519_MULTICODEC_PREFIX = b"\xed\x01"


def decode_multibase_key(multibase_key: str) -> bytes:
    """Decode a multibase-encoded public key (base58btc, 'z' prefix).

    Strips the two-byte Ed25519 multicodec prefix (0xed01) after base58
    decoding to return the raw 32-byte key.
    """
    if not multibase_key.startswith("z"):
        raise ValueError(f"Unsupported multibase prefix: {multibase_key[0]!r} (expected 'z')")
    decoded = base58.b58decode(multibase_key[1:])
    if decoded[:2] != _ED25519_MULTICODEC_PREFIX:
        raise ValueError("Missing Ed25519 multicodec prefix (0xed01)")
    raw_key = decoded[2:]
    if len(raw_key) != 32:
        raise ValueError(f"Expected 32-byte Ed25519 key, got {len(raw_key)} bytes")
    return raw_key


def verify_ed25519_signature(
    message: Union[str, bytes],
    signature_b64: str,
    public_key_multibase: str,
) -> bool:
    """Verify an Ed25519 signature. Returns False (never raises) on failure."""
    try:
        raw_key = decode_multibase_key(public_key_multibase)
        verify_key = VerifyKey(raw_key)
        sig_bytes = base64.b64decode(signature_b64)
        if isinstance(message, str):
            message = message.encode("utf-8")
        verify_key.verify(message, sig_bytes)
        return True
    except (BadSignatureError, ValueError, Exception):
        return False


def sign_ed25519(message: Union[str, bytes], private_key_bytes: bytes) -> str:
    """Sign a message with an Ed25519 private key. Returns base64 signature."""
    signing_key = SigningKey(private_key_bytes)
    if isinstance(message, str):
        message = message.encode("utf-8")
    signed = signing_key.sign(message)
    return base64.b64encode(signed.signature).decode("ascii")


def generate_keypair() -> tuple[bytes, str]:
    """Generate a new Ed25519 keypair.

    Returns ``(private_key_bytes, public_key_multibase)``.
    """
    signing_key = SigningKey.generate()
    pub_raw = bytes(signing_key.verify_key)
    multibase = "z" + base58.b58encode(_ED25519_MULTICODEC_PREFIX + pub_raw).decode("ascii")
    return bytes(signing_key), multibase


def canonicalize_json(data: dict) -> bytes:
    """Canonical JSON: sorted keys, no whitespace, UTF-8 encoded."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
