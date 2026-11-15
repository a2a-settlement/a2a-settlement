from __future__ import annotations

from fastapi.testclient import TestClient


def _register_pair(client, auth_header):
    """Register a requester and provider, return (provider_id, requester_key, provider_key)."""
    provider = client.post(
        "/v1/accounts/register",
        json={
            "bot_name": "ProviderBot",
            "developer_id": "dev",
            "developer_name": "Test Dev",
            "contact_email": "provider@test.dev",
            "skills": ["data-retrieval"],
        },
    ).json()
    requester = client.post(
        "/v1/accounts/register",
        json={
            "bot_name": "RequesterBot",
            "developer_id": "dev",
            "owner_api_key": provider["api_key"],
            "developer_name": "Test Dev",
            "contact_email": "requester@test.dev",
            "skills": ["orchestration"],
        },
    ).json()
    return provider["account"]["id"], requester["api_key"], provider["api_key"]


def _create_escrow(client, auth_header, requester_key, provider_id, **kwargs):
    """Create an escrow and return the response JSON."""
    payload = {"provider_id": provider_id, "amount": 50, **kwargs}
    resp = client.post(
        "/v1/exchange/escrow",
        headers=auth_header(requester_key),
        json=payload,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


SAMPLE_PROVENANCE = {
    "source_type": "api",
    "source_refs": [
        {
            "uri": "https://api.github.com/repos/org/repo/commits",
            "method": "GET",
            "timestamp": "2026-03-04T14:32:00Z",
            "content_hash": "sha256:a1b2c3d4e5f6",
        }
    ],
    "attestation_level": "self_declared",
}


def test_deliver_happy_path(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        provider_id, requester_key, provider_key = _register_pair(client, auth_header)
        escrow = _create_escrow(client, auth_header, requester_key, provider_id)
        escrow_id = escrow["escrow_id"]

        resp = client.post(
            f"/v1/exchange/escrow/{escrow_id}/deliver",
            headers=auth_header(provider_key),
            json={"content": "Here are the results.", "provenance": SAMPLE_PROVENANCE},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["escrow_id"] == escrow_id
        assert data["status"] == "held"
        assert "delivered_at" in data

        detail = client.get(
            f"/v1/exchange/escrows/{escrow_id}",
            headers=auth_header(requester_key),
        ).json()
        assert detail["delivered_content"] == "Here are the results."
        assert detail["provenance"]["source_type"] == "api"
        assert detail["provenance"]["attestation_level"] == "self_declared"
        assert detail["delivered_at"] is not None


def test_deliver_without_provenance(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        provider_id, requester_key, provider_key = _register_pair(client, auth_header)
        escrow = _create_escrow(client, auth_header, requester_key, provider_id)
        escrow_id = escrow["escrow_id"]

        resp = client.post(
            f"/v1/exchange/escrow/{escrow_id}/deliver",
            headers=auth_header(provider_key),
            json={"content": "Results without provenance."},
        )
        assert resp.status_code == 200, resp.text

        detail = client.get(
            f"/v1/exchange/escrows/{escrow_id}",
            headers=auth_header(requester_key),
        ).json()
        assert detail["delivered_content"] == "Results without provenance."
        assert detail["provenance"] is None


def test_deliver_wrong_provider_rejected(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        provider_id, requester_key, provider_key = _register_pair(client, auth_header)
        escrow = _create_escrow(client, auth_header, requester_key, provider_id)
        escrow_id = escrow["escrow_id"]

        resp = client.post(
            f"/v1/exchange/escrow/{escrow_id}/deliver",
            headers=auth_header(requester_key),
            json={"content": "I'm not the provider."},
        )
        assert resp.status_code == 403


def test_deliver_nonexistent_escrow(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        _register_pair(client, auth_header)
        resp = client.post(
            "/v1/exchange/escrow/nonexistent-id/deliver",
            headers=auth_header("fake_key"),
            json={"content": "test"},
        )
        assert resp.status_code == 401 or resp.status_code == 404


def test_deliver_after_release_rejected(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        provider_id, requester_key, provider_key = _register_pair(client, auth_header)
        escrow = _create_escrow(client, auth_header, requester_key, provider_id)
        escrow_id = escrow["escrow_id"]

        client.post(
            "/v1/exchange/release",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow_id},
        )

        resp = client.post(
            f"/v1/exchange/escrow/{escrow_id}/deliver",
            headers=auth_header(provider_key),
            json={"content": "Too late."},
        )
        assert resp.status_code == 400


def test_deliver_attestation_level_enforcement(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        provider_id, requester_key, provider_key = _register_pair(client, auth_header)
        escrow = _create_escrow(
            client,
            auth_header,
            requester_key,
            provider_id,
            required_attestation_level="signed",
        )
        escrow_id = escrow["escrow_id"]

        resp = client.post(
            f"/v1/exchange/escrow/{escrow_id}/deliver",
            headers=auth_header(provider_key),
            json={"content": "data", "provenance": SAMPLE_PROVENANCE},
        )
        assert resp.status_code == 400
        assert "attestation level" in resp.json()["detail"].lower()

        signed_provenance = {
            **SAMPLE_PROVENANCE,
            "attestation_level": "signed",
            "signature": "x-req-id-abc123",
        }
        resp2 = client.post(
            f"/v1/exchange/escrow/{escrow_id}/deliver",
            headers=auth_header(provider_key),
            json={"content": "data", "provenance": signed_provenance},
        )
        assert resp2.status_code == 200


def test_escrow_with_required_attestation_level(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        provider_id, requester_key, provider_key = _register_pair(client, auth_header)
        escrow = _create_escrow(
            client,
            auth_header,
            requester_key,
            provider_id,
            required_attestation_level="verifiable",
        )
        escrow_id = escrow["escrow_id"]

        detail = client.get(
            f"/v1/exchange/escrows/{escrow_id}",
            headers=auth_header(requester_key),
        ).json()
        assert detail["required_attestation_level"] == "verifiable"


def test_stats_include_provenance(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        provider_id, requester_key, provider_key = _register_pair(client, auth_header)
        escrow = _create_escrow(client, auth_header, requester_key, provider_id)
        escrow_id = escrow["escrow_id"]

        client.post(
            f"/v1/exchange/escrow/{escrow_id}/deliver",
            headers=auth_header(provider_key),
            json={"content": "data", "provenance": SAMPLE_PROVENANCE},
        )

        stats = client.get("/v1/stats").json()
        assert "provenance" in stats
        assert stats["provenance"]["total_delivered"] >= 1
        assert stats["provenance"]["with_provenance"] >= 1
