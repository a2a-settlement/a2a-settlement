from __future__ import annotations

from fastapi.testclient import TestClient


def test_release_pays_provider_and_records_fee(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        provider = client.post(
            "/v1/accounts/register",
            json={"bot_name": "ProviderBot", "developer_id": "dev", "skills": ["sentiment-analysis"]},
        ).json()
        requester = client.post(
            "/v1/accounts/register",
            json={"bot_name": "RequesterBot", "developer_id": "dev", "skills": ["orchestration"]},
        ).json()

        provider_id = provider["account"]["id"]
        provider_key = provider["api_key"]
        requester_key = requester["api_key"]

        escrow = client.post(
            "/v1/exchange/escrow",
            headers=auth_header(requester_key),
            json={"provider_id": provider_id, "amount": 10},
        ).json()

        rel = client.post(
            "/v1/exchange/release",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow["escrow_id"]},
        )
        assert rel.status_code == 200, rel.text
        body = rel.json()
        assert body["status"] == "released"
        assert body["amount_paid"] == 10
        assert body["fee_collected"] == 1  # ceil(0.3)

        provider_bal = client.get("/v1/exchange/balance", headers=auth_header(provider_key)).json()
        assert provider_bal["available"] == 110

