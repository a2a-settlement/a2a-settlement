"""AlgoVoi cross-extension v0 artefact loader and JWS verification."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "cross-extension-v0.json"

# AlgoVoi JWKS kid d0481df4cbbda8e8aba86709419884ef (from artefact metadata)
ALGOVOI_JWK_X = "GpEhVWMjUqIKDxcANFjaWqRY_BA0sK6NdwpUiouOkhI"

A2A_SE_SETTLEMENT_VECTORS = (
    "cross-ext-v0-escrow-double-release-001",
    "cross-ext-v0-refund-replay-001",
)


def b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def load_artefact(path: Path | None = None) -> dict[str, Any]:
    p = path or FIXTURE_PATH
    return json.loads(p.read_text(encoding="utf-8"))


def unsigned_vector(vector: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in vector.items() if k != "jws"}


def canonical_vector_bytes(vector: dict[str, Any]) -> bytes:
    """RFC8785-compatible canonical form (JCS subset: sorted keys, minimal separators)."""
    return json.dumps(unsigned_vector(vector), sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def verify_vector_jws(vector: dict[str, Any], *, jwk_x: str = ALGOVOI_JWK_X) -> tuple[bool, str]:
    """Return (ok, detail) per artefact verification_recipe steps 1–6."""
    jws = vector.get("jws")
    if not jws:
        return False, "missing jws"

    parts = jws.split(".")
    if len(parts) != 3:
        return False, "malformed jws"

    header_b64, payload_b64, signature_b64 = parts
    payload_bytes = b64url_decode(payload_b64)
    canonical = canonical_vector_bytes(vector)
    if payload_bytes != canonical:
        return False, "payload bytes != canonical unsigned vector"

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    try:
        verify_key = VerifyKey(b64url_decode(jwk_x))
        verify_key.verify(signing_input, b64url_decode(signature_b64))
    except BadSignatureError:
        return False, "invalid Ed25519 signature"
    except Exception as exc:  # noqa: BLE001 — test helper returns detail string
        return False, str(exc)

    return True, "ok"


def get_vector(artefact: dict[str, Any], vector_id: str) -> dict[str, Any]:
    for vector in artefact.get("vectors", []):
        if vector.get("vector_id") == vector_id:
            return vector
    raise KeyError(vector_id)


def map_verdict_to_http(verdict: str) -> int:
    """AlgoVoi BLOCK/REVIEW → HTTP status we expect from the exchange rail."""
    if verdict == "BLOCK":
        return 400
    if verdict == "REVIEW":
        return 400  # exchange has no REVIEW tier; reject is still a block at HTTP layer
    return 200
