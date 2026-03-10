"""Tests for deliver endpoint with grounding metadata — verifies backward
compatibility and new grounding verification checks."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _register_pair(client, auth_header):
    provider = client.post(
        "/v1/accounts/register",
        json={
            "bot_name": "GroundingProvider",
            "developer_id": "dev",
            "developer_name": "Test",
            "contact_email": "gp@test.dev",
            "skills": ["research"],
        },
    ).json()
    requester = client.post(
        "/v1/accounts/register",
        json={
            "bot_name": "GroundingRequester",
            "developer_id": "dev",
            "developer_name": "Test",
            "contact_email": "gr@test.dev",
            "skills": ["orchestration"],
        },
    ).json()
    return provider["account"]["id"], requester["api_key"], provider["api_key"]


def _create_escrow(client, auth_header, requester_key, provider_id, **kwargs):
    resp = client.post(
        "/v1/exchange/escrow",
        headers=auth_header(requester_key),
        json={"provider_id": provider_id, "amount": 50, **kwargs},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


GROUNDING_PROVENANCE = {
    "source_type": "web",
    "source_refs": [
        {
            "uri": "https://worldbank.org/gdp",
            "method": "google_search_grounding",
            "timestamp": "2026-03-10T10:00:00Z",
        },
        {
            "uri": "https://imf.org/data/france",
            "method": "google_search_grounding",
            "timestamp": "2026-03-10T10:00:00Z",
        },
    ],
    "attestation_level": "verifiable",
    "grounding_metadata": {
        "chunks": [
            {"uri": "https://worldbank.org/gdp", "title": "World Bank GDP"},
            {"uri": "https://imf.org/data/france", "title": "IMF France"},
        ],
        "supports": [
            {
                "segment": {
                    "text": "France GDP was $3.05T in 2025.",
                    "start_index": 0,
                    "end_index": 30,
                },
                "chunk_indices": [0, 1],
            }
        ],
        "search_queries": ["France GDP 2025"],
        "coverage": 0.75,
    },
}


def test_deliver_with_grounding_metadata(exchange_app, auth_header):
    """Deliver with grounding metadata should succeed and store it."""
    with TestClient(exchange_app) as client:
        provider_id, requester_key, provider_key = _register_pair(client, auth_header)
        escrow = _create_escrow(client, auth_header, requester_key, provider_id)
        escrow_id = escrow["escrow_id"]

        content = "France GDP was $3.05T in 2025. It grew 1.2% year-over-year."
        resp = client.post(
            f"/v1/exchange/escrow/{escrow_id}/deliver",
            headers=auth_header(provider_key),
            json={"content": content, "provenance": GROUNDING_PROVENANCE},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["escrow_id"] == escrow_id

        detail = client.get(
            f"/v1/exchange/escrows/{escrow_id}",
            headers=auth_header(requester_key),
        ).json()
        assert detail["provenance"]["source_type"] == "web"
        assert detail["provenance"]["grounding_metadata"] is not None
        gm = detail["provenance"]["grounding_metadata"]
        assert len(gm["chunks"]) == 2
        assert gm["coverage"] == 0.75

        prov_result = detail["provenance_result"]
        assert prov_result is not None
        checks = prov_result["checks"]
        assert any("grounding_chunks_present" in c for c in checks)
        assert any("grounding_coverage" in c for c in checks)
        assert any("grounding_source_diversity" in c for c in checks)


def test_deliver_without_grounding_backward_compatible(exchange_app, auth_header):
    """Deliver without grounding should work exactly as before."""
    with TestClient(exchange_app) as client:
        provider_id, requester_key, provider_key = _register_pair(client, auth_header)
        escrow = _create_escrow(client, auth_header, requester_key, provider_id)
        escrow_id = escrow["escrow_id"]

        plain_provenance = {
            "source_type": "api",
            "source_refs": [
                {
                    "uri": "https://api.github.com/repos/org/repo",
                    "method": "GET",
                    "timestamp": "2026-03-10T10:00:00Z",
                }
            ],
            "attestation_level": "self_declared",
        }

        resp = client.post(
            f"/v1/exchange/escrow/{escrow_id}/deliver",
            headers=auth_header(provider_key),
            json={"content": "Some result.", "provenance": plain_provenance},
        )
        assert resp.status_code == 200, resp.text

        detail = client.get(
            f"/v1/exchange/escrows/{escrow_id}",
            headers=auth_header(requester_key),
        ).json()
        assert detail["provenance"]["grounding_metadata"] is None
        checks = detail["provenance_result"]["checks"]
        assert not any("grounding" in c for c in checks)


def test_deliver_no_provenance_still_works(exchange_app, auth_header):
    """Deliver without any provenance — regression test."""
    with TestClient(exchange_app) as client:
        provider_id, requester_key, provider_key = _register_pair(client, auth_header)
        escrow = _create_escrow(client, auth_header, requester_key, provider_id)
        escrow_id = escrow["escrow_id"]

        resp = client.post(
            f"/v1/exchange/escrow/{escrow_id}/deliver",
            headers=auth_header(provider_key),
            json={"content": "Just content, no provenance."},
        )
        assert resp.status_code == 200
        detail = client.get(
            f"/v1/exchange/escrows/{escrow_id}",
            headers=auth_header(requester_key),
        ).json()
        assert detail["provenance"] is None
        assert detail["provenance_result"] is None


def test_grounding_low_coverage_flagged(exchange_app, auth_header):
    """Grounding with low coverage should be flagged as insufficient."""
    with TestClient(exchange_app) as client:
        provider_id, requester_key, provider_key = _register_pair(client, auth_header)
        escrow = _create_escrow(client, auth_header, requester_key, provider_id)
        escrow_id = escrow["escrow_id"]

        low_coverage_prov = {
            **GROUNDING_PROVENANCE,
            "grounding_metadata": {
                **GROUNDING_PROVENANCE["grounding_metadata"],
                "coverage": 0.2,
            },
        }

        resp = client.post(
            f"/v1/exchange/escrow/{escrow_id}/deliver",
            headers=auth_header(provider_key),
            json={"content": "short text", "provenance": low_coverage_prov},
        )
        assert resp.status_code == 200
        detail = client.get(
            f"/v1/exchange/escrows/{escrow_id}",
            headers=auth_header(requester_key),
        ).json()
        checks = detail["provenance_result"]["checks"]
        assert any("grounding_insufficient" in c for c in checks)


def test_grounding_single_source_flagged(exchange_app, auth_header):
    """Grounding from a single domain should be noted."""
    with TestClient(exchange_app) as client:
        provider_id, requester_key, provider_key = _register_pair(client, auth_header)
        escrow = _create_escrow(client, auth_header, requester_key, provider_id)
        escrow_id = escrow["escrow_id"]

        single_source_prov = {
            "source_type": "web",
            "source_refs": [
                {
                    "uri": "https://example.com/a",
                    "method": "google_search_grounding",
                    "timestamp": "2026-03-10T10:00:00Z",
                },
            ],
            "attestation_level": "verifiable",
            "grounding_metadata": {
                "chunks": [
                    {"uri": "https://example.com/a", "title": "A"},
                    {"uri": "https://example.com/b", "title": "B"},
                ],
                "supports": [
                    {
                        "segment": {
                            "text": "short text",
                            "start_index": 0,
                            "end_index": 10,
                        },
                        "chunk_indices": [0],
                    }
                ],
                "search_queries": ["test"],
                "coverage": 0.8,
            },
        }

        resp = client.post(
            f"/v1/exchange/escrow/{escrow_id}/deliver",
            headers=auth_header(provider_key),
            json={"content": "short text", "provenance": single_source_prov},
        )
        assert resp.status_code == 200
        detail = client.get(
            f"/v1/exchange/escrows/{escrow_id}",
            headers=auth_header(requester_key),
        ).json()
        checks = detail["provenance_result"]["checks"]
        assert any("grounding_single_source" in c for c in checks)
