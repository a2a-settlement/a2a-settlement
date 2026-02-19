"""Validate that the FastAPI-generated OpenAPI schema aligns with the handwritten openapi.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
OPENAPI_YAML = REPO_ROOT / "openapi.yaml"


def _load_handwritten_spec() -> dict:
    return yaml.safe_load(OPENAPI_YAML.read_text())


def _get_fastapi_spec(exchange_app) -> dict:
    return exchange_app.openapi()


def test_handwritten_spec_exists():
    assert OPENAPI_YAML.exists(), "openapi.yaml not found at repo root"


def test_handwritten_spec_parses():
    spec = _load_handwritten_spec()
    assert spec["openapi"].startswith("3.1")
    assert spec["info"]["version"] == "0.8.1"


def test_all_handwritten_paths_exist_in_fastapi(exchange_app):
    hand = _load_handwritten_spec()
    fastapi = _get_fastapi_spec(exchange_app)

    hand_paths = set(hand.get("paths", {}).keys())
    # FastAPI prefixes with /v1 (or /api/v1); the handwritten spec uses unprefixed paths.
    fastapi_paths = set()
    for p in fastapi.get("paths", {}).keys():
        stripped = p.removeprefix("/v1").removeprefix("/api/v1")
        if stripped == "":
            stripped = "/"
        fastapi_paths.add(stripped)

    missing = hand_paths - fastapi_paths
    assert not missing, f"Paths in openapi.yaml but not in FastAPI: {missing}"


def test_version_matches(exchange_app):
    hand = _load_handwritten_spec()
    fastapi = _get_fastapi_spec(exchange_app)
    assert hand["info"]["version"] == fastapi["info"]["version"], (
        f"Version mismatch: openapi.yaml={hand['info']['version']}, "
        f"FastAPI={fastapi['info']['version']}"
    )


def test_all_schema_names_present(exchange_app):
    """Check that key schema names from the handwritten spec have corresponding models in FastAPI."""
    hand = _load_handwritten_spec()
    fastapi = _get_fastapi_spec(exchange_app)

    hand_schemas = set(hand.get("components", {}).get("schemas", {}).keys())
    fastapi_schemas = set(fastapi.get("components", {}).get("schemas", {}).keys())

    key_schemas = {
        "EscrowRequest",
        "EscrowResponse",
        "ReleaseResponse",
        "RefundResponse",
        "DisputeResponse",
        "BalanceResponse",
        "HealthResponse",
    }
    for name in key_schemas:
        assert name in hand_schemas, f"{name} missing from openapi.yaml"
        assert name in fastapi_schemas, f"{name} missing from FastAPI schemas"
