from __future__ import annotations

from fastapi.testclient import TestClient


def test_escrow_attestations_endpoint_returns_typed_release(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        provider = client.post(
            "/v1/accounts/register",
            json={
                "bot_name": "AttestationProvider",
                "developer_id": "dev",
                "developer_name": "Test Dev",
                "contact_email": "provider@test.dev",
                "skills": ["settlement"],
            },
        ).json()
        requester = client.post(
            "/v1/accounts/register",
            json={
                "bot_name": "AttestationRequester",
                "developer_id": "dev",
                "developer_name": "Test Dev",
                "contact_email": "requester@test.dev",
                "skills": ["orchestration"],
            },
        ).json()

        provider_id = provider["account"]["id"]
        requester_key = requester["api_key"]

        escrow = client.post(
            "/v1/exchange/escrow",
            headers=auth_header(requester_key),
            json={"provider_id": provider_id, "amount": 25, "task_id": "att-task"},
        ).json()
        escrow_id = escrow["escrow_id"]

        released = client.post(
            "/v1/exchange/release",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow_id},
        )
        assert released.status_code == 200, released.text

        resp = client.get(f"/v1/exchange/escrow/{escrow_id}/attestations")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["enabled"] is True
        assert body["escrow_id"] == escrow_id

        typed = [
            item
            for item in body["attestations"]
            if item["schema_id"] == "urn:a2a-se:escrow-release-attestation:v1"
        ]
        assert typed
        att = typed[-1]
        assert att["data_hash"]
        assert att["merkle_root"]
        assert "proof" in att
        payload = att["payload"]
        assert payload["settlement"]["escrow_id"] == escrow_id
        assert payload["settlement"]["amount"] == 25
        assert payload["settlement"]["task_id"] == "att-task"
        assert payload["amount_paid"] == 25
        assert payload["release_kind"] == "full"

