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
            "developer_name": "Test Dev",
            "contact_email": "requester@test.dev",
            "skills": ["orchestration"],
        },
    ).json()
    return provider["account"]["id"], requester["api_key"], provider["api_key"]


def _create_escrow(client, auth_header, requester_key, provider_id, amount=50):
    resp = client.post(
        "/v1/exchange/escrow",
        headers=auth_header(requester_key),
        json={"provider_id": provider_id, "amount": amount},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- Tests ---


def test_partial_release_splits_amounts_correctly(exchange_app, auth_header):
    """50% release on a 50-token escrow: 25 released, 25 holdback."""
    with TestClient(exchange_app) as client:
        provider_id, requester_key, provider_key = _register_pair(client, auth_header)
        escrow = _create_escrow(client, auth_header, requester_key, provider_id, amount=50)
        escrow_id = escrow["escrow_id"]

        resp = client.post(
            f"/v1/exchange/escrow/{escrow_id}/partial-release",
            headers=auth_header(requester_key),
            json={
                "escrow_id": escrow_id,
                "release_percent": 50,
                "score": 75,
                "efficacy_check_at": "2099-12-31T23:59:59Z",
                "efficacy_criteria": "Prediction accuracy >= 75%",
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "partially_released"
        assert data["released_amount"] == 25
        assert data["holdback_amount"] == 25
        assert data["provider_id"] == provider_id

        provider_bal = client.get(
            "/v1/exchange/balance", headers=auth_header(provider_key)
        ).json()
        assert provider_bal["available"] == 100 + 25

        detail = client.get(
            f"/v1/exchange/escrows/{escrow_id}",
            headers=auth_header(requester_key),
        ).json()
        assert detail["score"] == 75
        assert detail["released_amount"] == 25
        assert detail["holdback_amount"] == 25
        assert detail["efficacy_criteria"] == "Prediction accuracy >= 75%"


def test_partial_release_with_efficacy_check(exchange_app, auth_header):
    """When efficacy_check_at is provided, status becomes partially_released."""
    with TestClient(exchange_app) as client:
        provider_id, requester_key, provider_key = _register_pair(client, auth_header)
        escrow = _create_escrow(client, auth_header, requester_key, provider_id)
        escrow_id = escrow["escrow_id"]

        resp = client.post(
            f"/v1/exchange/escrow/{escrow_id}/partial-release",
            headers=auth_header(requester_key),
            json={
                "escrow_id": escrow_id,
                "release_percent": 60,
                "efficacy_check_at": "2099-06-15T00:00:00Z",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "partially_released"
        assert data["holdback_amount"] > 0
        assert data["efficacy_check_at"] is not None


def test_partial_release_without_efficacy_refunds_holdback(exchange_app, auth_header):
    """Without efficacy_check_at, holdback is immediately refunded and status is released."""
    with TestClient(exchange_app) as client:
        provider_id, requester_key, provider_key = _register_pair(client, auth_header)
        escrow = _create_escrow(client, auth_header, requester_key, provider_id, amount=50)
        escrow_id = escrow["escrow_id"]

        resp = client.post(
            f"/v1/exchange/escrow/{escrow_id}/partial-release",
            headers=auth_header(requester_key),
            json={
                "escrow_id": escrow_id,
                "release_percent": 80,
                "score": 80,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "released"
        assert data["holdback_amount"] == 0
        assert data["holdback_fee"] == 0

        requester_bal = client.get(
            "/v1/exchange/balance", headers=auth_header(requester_key)
        ).json()
        holdback_refund = 50 - (50 * 80 // 100)
        assert requester_bal["available"] >= holdback_refund


def test_holdback_release_via_release_endpoint(exchange_app, auth_header):
    """After partial release, calling release on partially_released escrow releases holdback."""
    with TestClient(exchange_app) as client:
        provider_id, requester_key, provider_key = _register_pair(client, auth_header)
        escrow = _create_escrow(client, auth_header, requester_key, provider_id, amount=50)
        escrow_id = escrow["escrow_id"]

        client.post(
            f"/v1/exchange/escrow/{escrow_id}/partial-release",
            headers=auth_header(requester_key),
            json={
                "escrow_id": escrow_id,
                "release_percent": 50,
                "efficacy_check_at": "2099-12-31T23:59:59Z",
            },
        )

        rel = client.post(
            "/v1/exchange/release",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow_id},
        )
        assert rel.status_code == 200, rel.text
        body = rel.json()
        assert body["status"] == "released"
        assert body["amount_paid"] == 25

        provider_bal = client.get(
            "/v1/exchange/balance", headers=auth_header(provider_key)
        ).json()
        assert provider_bal["available"] == 100 + 50


def test_holdback_refund_via_refund_endpoint(exchange_app, auth_header):
    """After partial release, refunding a partially_released escrow refunds only the holdback."""
    with TestClient(exchange_app) as client:
        provider_id, requester_key, provider_key = _register_pair(client, auth_header)
        escrow = _create_escrow(client, auth_header, requester_key, provider_id, amount=50)
        escrow_id = escrow["escrow_id"]
        fee = escrow["fee_amount"]

        client.post(
            f"/v1/exchange/escrow/{escrow_id}/partial-release",
            headers=auth_header(requester_key),
            json={
                "escrow_id": escrow_id,
                "release_percent": 50,
                "efficacy_check_at": "2099-12-31T23:59:59Z",
            },
        )

        ref = client.post(
            "/v1/exchange/refund",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow_id},
        )
        assert ref.status_code == 200, ref.text
        body = ref.json()
        assert body["status"] == "released"

        holdback_amount = 50 - (50 * 50 // 100)
        holdback_fee = fee - (fee * 50 // 100)
        assert body["amount_returned"] == holdback_amount + holdback_fee

        provider_bal = client.get(
            "/v1/exchange/balance", headers=auth_header(provider_key)
        ).json()
        assert provider_bal["available"] == 100 + 25


def test_partial_release_only_by_requester(exchange_app, auth_header):
    """Provider cannot call partial-release."""
    with TestClient(exchange_app) as client:
        provider_id, requester_key, provider_key = _register_pair(client, auth_header)
        escrow = _create_escrow(client, auth_header, requester_key, provider_id)
        escrow_id = escrow["escrow_id"]

        resp = client.post(
            f"/v1/exchange/escrow/{escrow_id}/partial-release",
            headers=auth_header(provider_key),
            json={
                "escrow_id": escrow_id,
                "release_percent": 50,
            },
        )
        assert resp.status_code == 403


def test_partial_release_requires_held_status(exchange_app, auth_header):
    """Cannot partial-release an already released escrow."""
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
            f"/v1/exchange/escrow/{escrow_id}/partial-release",
            headers=auth_header(requester_key),
            json={
                "escrow_id": escrow_id,
                "release_percent": 50,
            },
        )
        assert resp.status_code == 400


def test_partial_release_fee_proportionality(exchange_app, auth_header):
    """Fee split should be proportional and not lose tokens."""
    with TestClient(exchange_app) as client:
        provider_id, requester_key, provider_key = _register_pair(client, auth_header)
        escrow = _create_escrow(client, auth_header, requester_key, provider_id, amount=33)
        escrow_id = escrow["escrow_id"]
        fee = escrow["fee_amount"]

        resp = client.post(
            f"/v1/exchange/escrow/{escrow_id}/partial-release",
            headers=auth_header(requester_key),
            json={
                "escrow_id": escrow_id,
                "release_percent": 70,
                "efficacy_check_at": "2099-12-31T23:59:59Z",
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()

        assert data["released_amount"] + data["holdback_amount"] == 33
        assert data["fee_collected"] + data["holdback_fee"] == fee
