from __future__ import annotations

from fastapi.testclient import TestClient


def test_reputation_updates_on_release_and_refund(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        provider = client.post(
            "/v1/accounts/register",
            json={"bot_name": "ProviderBot", "developer_id": "dev", "developer_name": "Test Dev", "contact_email": "test@test.dev", "skills": ["sentiment-analysis"]},
        ).json()
        requester = client.post(
            "/v1/accounts/register",
            json={"bot_name": "RequesterBot", "developer_id": "dev", "developer_name": "Test Dev", "contact_email": "test@test.dev", "skills": ["orchestration"]},
        ).json()

        provider_id = provider["account"]["id"]
        provider_key = provider["api_key"]
        requester_key = requester["api_key"]

        # Reputation starts at 0.5.
        bal0 = client.get("/v1/exchange/balance", headers=auth_header(provider_key)).json()
        assert abs(bal0["reputation"] - 0.5) < 1e-9

        # Release -> rep = 0.9*0.5 + 0.1*1.0 = 0.55
        escrow = client.post(
            "/v1/exchange/escrow",
            headers=auth_header(requester_key),
            json={"provider_id": provider_id, "amount": 10},
        ).json()
        client.post("/v1/exchange/release", headers=auth_header(requester_key), json={"escrow_id": escrow["escrow_id"]})

        bal1 = client.get("/v1/exchange/balance", headers=auth_header(provider_key)).json()
        assert abs(bal1["reputation"] - 0.55) < 1e-9

        # Refund -> rep = 0.9*0.55 + 0.1*0 = 0.495
        escrow2 = client.post(
            "/v1/exchange/escrow",
            headers=auth_header(requester_key),
            json={"provider_id": provider_id, "amount": 10},
        ).json()
        client.post("/v1/exchange/refund", headers=auth_header(requester_key), json={"escrow_id": escrow2["escrow_id"]})

        bal2 = client.get("/v1/exchange/balance", headers=auth_header(provider_key)).json()
        assert abs(bal2["reputation"] - 0.495) < 1e-9

