"""Financial idempotency commits with the ledger effect and is scoped."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_acceptance_binding import TASK, _bootstrap


def test_duplicate_release_pays_once(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        ctx = _bootstrap(client, auth_header, acceptance_required=False)
        created = client.post(
            "/v1/exchange/escrow",
            headers={**auth_header(ctx["requester"]["api_key"]), "Idempotency-Key": "idem-create"},
            json={"provider_id": ctx["provider"]["account"]["id"], "amount": 10, "task_id": TASK},
        )
        assert created.status_code == 201, created.text
        headers = {**auth_header(ctx["requester"]["api_key"]), "Idempotency-Key": "idem-rel"}
        body = {"escrow_id": created.json()["escrow_id"]}
        first = client.post("/v1/exchange/release", headers=headers, json=body)
        second = client.post("/v1/exchange/release", headers=headers, json=body)
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert second.json()["escrow_id"] == first.json()["escrow_id"]
        provider_bal = client.get(
            "/v1/exchange/balance", headers=auth_header(ctx["provider"]["api_key"])
        ).json()
        assert provider_bal["available"] == 110


def test_same_key_different_body_conflicts(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        ctx = _bootstrap(client, auth_header, acceptance_required=False, limit_units=40)
        headers = {**auth_header(ctx["requester"]["api_key"]), "Idempotency-Key": "same-key"}
        first = client.post(
            "/v1/exchange/escrow",
            headers=headers,
            json={"provider_id": ctx["provider"]["account"]["id"], "amount": 10, "task_id": TASK},
        )
        assert first.status_code == 201, first.text
        other = client.post(
            "/v1/accounts/register",
            json={
                "bot_name": "OtherProvider",
                "developer_id": "dev",
                "developer_name": "Test Dev",
                "contact_email": "test@test.dev",
                "skills": ["analysis"],
            },
        ).json()
        second = client.post(
            "/v1/exchange/escrow",
            headers=headers,
            json={"provider_id": other["account"]["id"], "amount": 10, "task_id": TASK},
        )
        assert second.status_code == 409, second.text


def test_same_key_other_principal_does_not_collide(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        ctx = _bootstrap(client, auth_header, acceptance_required=False, limit_units=40)
        sibling_reg = client.post(
            "/v1/accounts/register",
            json={
                "bot_name": "SiblingPayer",
                "developer_id": "dev",
                "developer_name": "Test Dev",
                "contact_email": "test@test.dev",
                "skills": ["orchestration"],
            },
        ).json()
        client.post(
            "/v1/exchange/assurance/bind-account",
            headers=auth_header(ctx["operator_key"]),
            json={"account_id": sibling_reg["account"]["id"], "policy_id": ctx["policy_id"]},
        )
        provider2 = client.post(
            "/v1/accounts/register",
            json={
                "bot_name": "ProviderB",
                "developer_id": "dev",
                "developer_name": "Test Dev",
                "contact_email": "test@test.dev",
                "skills": ["analysis"],
            },
        ).json()
        key = "shared-key"
        first = client.post(
            "/v1/exchange/escrow",
            headers={**auth_header(ctx["requester"]["api_key"]), "Idempotency-Key": key},
            json={"provider_id": ctx["provider"]["account"]["id"], "amount": 10, "task_id": TASK},
        )
        second = client.post(
            "/v1/exchange/escrow",
            headers={**auth_header(sibling_reg["api_key"]), "Idempotency-Key": key},
            json={"provider_id": provider2["account"]["id"], "amount": 10, "task_id": TASK},
        )
        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        assert first.json()["escrow_id"] != second.json()["escrow_id"]


def test_partial_release_replay_pays_once(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        ctx = _bootstrap(client, auth_header, acceptance_required=False, limit_units=80)
        created = client.post(
            "/v1/exchange/escrow",
            headers={**auth_header(ctx["requester"]["api_key"]), "Idempotency-Key": "idem-create-partial"},
            json={"provider_id": ctx["provider"]["account"]["id"], "amount": 40, "task_id": TASK},
        )
        assert created.status_code == 201, created.text
        escrow_id = created.json()["escrow_id"]
        headers = {**auth_header(ctx["requester"]["api_key"]), "Idempotency-Key": "idem-partial"}
        body = {"escrow_id": escrow_id, "release_percent": 50}
        first = client.post(f"/v1/exchange/escrow/{escrow_id}/partial-release", headers=headers, json=body)
        second = client.post(f"/v1/exchange/escrow/{escrow_id}/partial-release", headers=headers, json=body)
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert second.json()["released_amount"] == first.json()["released_amount"]
        provider_bal = client.get(
            "/v1/exchange/balance", headers=auth_header(ctx["provider"]["api_key"])
        ).json()
        assert provider_bal["available"] == 100 + first.json()["released_amount"]


def test_refund_replay_returns_funds_once(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        ctx = _bootstrap(client, auth_header, acceptance_required=False)
        created = client.post(
            "/v1/exchange/escrow",
            headers={**auth_header(ctx["requester"]["api_key"]), "Idempotency-Key": "idem-create-refund"},
            json={"provider_id": ctx["provider"]["account"]["id"], "amount": 10, "task_id": TASK},
        )
        assert created.status_code == 201, created.text
        headers = {**auth_header(ctx["requester"]["api_key"]), "Idempotency-Key": "idem-refund"}
        body = {"escrow_id": created.json()["escrow_id"]}
        before = client.get("/v1/exchange/balance", headers=auth_header(ctx["requester"]["api_key"])).json()
        first = client.post("/v1/exchange/refund", headers=headers, json=body)
        second = client.post("/v1/exchange/refund", headers=headers, json=body)
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        after = client.get("/v1/exchange/balance", headers=auth_header(ctx["requester"]["api_key"])).json()
        assert after["available"] == before["available"] + first.json()["amount_returned"]


def test_unbound_instant_settle_replay_pays_once(exchange_app, auth_header):
    from exchange.config import get_session
    from exchange.models import Account
    from sqlalchemy import select

    with TestClient(exchange_app) as client:
        requester = client.post(
            "/v1/accounts/register",
            json={
                "bot_name": "InstantRequester",
                "developer_id": "dev",
                "developer_name": "Test Dev",
                "contact_email": "test@test.dev",
                "skills": ["orchestration"],
            },
        ).json()
        provider = client.post(
            "/v1/accounts/register",
            json={
                "bot_name": "InstantProvider",
                "developer_id": "dev",
                "developer_name": "Test Dev",
                "contact_email": "test@test.dev",
                "skills": ["analysis"],
            },
        ).json()
        session = next(get_session())
        with session.begin():
            acct = session.execute(select(Account).where(Account.id == requester["account"]["id"])).scalar_one()
            acct.reputation = 0.9
        headers = {**auth_header(requester["api_key"]), "Idempotency-Key": "idem-instant"}
        body = {"provider_id": provider["account"]["id"], "amount": 10}
        first = client.post("/v1/exchange/instant-settle", headers=headers, json=body)
        second = client.post("/v1/exchange/instant-settle", headers=headers, json=body)
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert second.json()["transaction_id"] == first.json()["transaction_id"]
        provider_bal = client.get("/v1/exchange/balance", headers=auth_header(provider["api_key"])).json()
        assert provider_bal["available"] == 110


def test_bound_account_requires_key(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        ctx = _bootstrap(client, auth_header, acceptance_required=False)
        resp = client.post(
            "/v1/exchange/escrow",
            headers=auth_header(ctx["requester"]["api_key"]),
            json={"provider_id": ctx["provider"]["account"]["id"], "amount": 10, "task_id": TASK},
        )
        assert resp.status_code == 400, resp.text
