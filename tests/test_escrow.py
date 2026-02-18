from __future__ import annotations

from fastapi.testclient import TestClient


def test_escrow_moves_available_to_held(exchange_app, auth_header):
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
        requester_key = requester["api_key"]

        bal0 = client.get("/v1/exchange/balance", headers=auth_header(requester_key)).json()
        assert bal0["available"] == 100
        assert bal0["held_in_escrow"] == 0

        escrow = client.post(
            "/v1/exchange/escrow",
            headers=auth_header(requester_key),
            json={"provider_id": provider_id, "amount": 50},
        )
        assert escrow.status_code == 201, escrow.text
        esc = escrow.json()
        assert esc["fee_amount"] == 2  # ceil(1.5)
        assert esc["total_held"] == 52

        bal1 = client.get("/v1/exchange/balance", headers=auth_header(requester_key)).json()
        assert bal1["available"] == 48
        assert bal1["held_in_escrow"] == 52

