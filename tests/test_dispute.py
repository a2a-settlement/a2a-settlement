from __future__ import annotations

from fastapi.testclient import TestClient


def _setup_escrow(client, auth_header):
    """Register two agents and create an escrow between them."""
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

    return escrow, requester_key, provider_key, provider_id


def test_dispute_freezes_escrow(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        escrow, requester_key, _provider_key, _provider_id = _setup_escrow(client, auth_header)

        resp = client.post(
            "/v1/exchange/dispute",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow["escrow_id"], "reason": "Incomplete work"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "disputed"
        assert body["reason"] == "Incomplete work"


def test_provider_can_dispute(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        escrow, _requester_key, provider_key, _provider_id = _setup_escrow(client, auth_header)

        resp = client.post(
            "/v1/exchange/dispute",
            headers=auth_header(provider_key),
            json={"escrow_id": escrow["escrow_id"], "reason": "Requester unresponsive"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "disputed"


def test_release_blocked_while_disputed(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        escrow, requester_key, _provider_key, _provider_id = _setup_escrow(client, auth_header)

        client.post(
            "/v1/exchange/dispute",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow["escrow_id"], "reason": "test"},
        )

        resp = client.post(
            "/v1/exchange/release",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow["escrow_id"]},
        )
        assert resp.status_code == 400
        assert "disputed" in resp.json()["detail"].lower()


def test_refund_blocked_while_disputed(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        escrow, requester_key, _provider_key, _provider_id = _setup_escrow(client, auth_header)

        client.post(
            "/v1/exchange/dispute",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow["escrow_id"], "reason": "test"},
        )

        resp = client.post(
            "/v1/exchange/refund",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow["escrow_id"]},
        )
        assert resp.status_code == 400
        assert "disputed" in resp.json()["detail"].lower()


def test_resolve_to_release(exchange_app, auth_header, monkeypatch):
    with TestClient(exchange_app) as client:
        escrow, requester_key, provider_key, provider_id = _setup_escrow(client, auth_header)

        client.post(
            "/v1/exchange/dispute",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow["escrow_id"], "reason": "test"},
        )

        # Promote requester to operator for resolution (in production this
        # would be a separate operator account).
        from exchange.config import get_session
        from exchange.models import Account

        session_gen = get_session()
        session = next(session_gen)
        with session.begin():
            from sqlalchemy import select
            acct = session.execute(select(Account).where(Account.bot_name == "RequesterBot")).scalar_one()
            acct.status = "operator"
            session.add(acct)
        session.close()

        resp = client.post(
            "/v1/exchange/resolve",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow["escrow_id"], "resolution": "release"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["resolution"] == "release"
        assert body["status"] == "released"
        assert body["amount_paid"] == 10

        provider_bal = client.get("/v1/exchange/balance", headers=auth_header(provider_key)).json()
        assert provider_bal["available"] == 110


def test_resolve_to_refund(exchange_app, auth_header, monkeypatch):
    with TestClient(exchange_app) as client:
        escrow, requester_key, _provider_key, _provider_id = _setup_escrow(client, auth_header)

        client.post(
            "/v1/exchange/dispute",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow["escrow_id"], "reason": "test"},
        )

        from exchange.config import get_session
        from exchange.models import Account

        session_gen = get_session()
        session = next(session_gen)
        with session.begin():
            from sqlalchemy import select
            acct = session.execute(select(Account).where(Account.bot_name == "RequesterBot")).scalar_one()
            acct.status = "operator"
            session.add(acct)
        session.close()

        resp = client.post(
            "/v1/exchange/resolve",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow["escrow_id"], "resolution": "refund"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["resolution"] == "refund"
        assert body["status"] == "refunded"
        assert body["amount_returned"] == 11  # 10 + ceil(0.3) fee

        bal = client.get("/v1/exchange/balance", headers=auth_header(requester_key)).json()
        assert bal["held_in_escrow"] == 0


def test_resolve_requires_operator(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        escrow, requester_key, _provider_key, _provider_id = _setup_escrow(client, auth_header)

        client.post(
            "/v1/exchange/dispute",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow["escrow_id"], "reason": "test"},
        )

        resp = client.post(
            "/v1/exchange/resolve",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow["escrow_id"], "resolution": "release"},
        )
        assert resp.status_code == 403
        assert "operator" in resp.json()["detail"].lower()
