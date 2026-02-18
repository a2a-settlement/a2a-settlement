from __future__ import annotations

from fastapi.testclient import TestClient


def test_refund_returns_amount_plus_fee(exchange_app, auth_header):
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

        escrow = client.post(
            "/v1/exchange/escrow",
            headers=auth_header(requester_key),
            json={"provider_id": provider_id, "amount": 10},
        ).json()

        bal_after_escrow = client.get("/v1/exchange/balance", headers=auth_header(requester_key)).json()
        assert bal_after_escrow["held_in_escrow"] == 11

        refund = client.post(
            "/v1/exchange/refund",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow["escrow_id"], "reason": "test"},
        )
        assert refund.status_code == 200, refund.text
        body = refund.json()
        assert body["status"] == "refunded"
        assert body["amount_returned"] == 11

        bal_end = client.get("/v1/exchange/balance", headers=auth_header(requester_key)).json()
        assert bal_end["held_in_escrow"] == 0

