from __future__ import annotations

from fastapi.testclient import TestClient


# Matches the A2A_EXCHANGE_DISPUTE_STAKE_MIN default. Filing a dispute costs the
# filer this much, held until an operator rules on it.
STAKE = 10


def _setup_escrow(client, auth_header):
    """Register two agents and create an escrow between them."""
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

    escrow = client.post(
        "/v1/exchange/escrow",
        headers=auth_header(requester_key),
        json={"provider_id": provider_id, "amount": 10},
    ).json()

    return escrow, requester_key, provider_key, provider_id


def _dispute(client, auth_header, key, escrow_id, reason="test", stake=STAKE):
    return client.post(
        "/v1/exchange/dispute",
        headers=auth_header(key),
        json={"escrow_id": escrow_id, "reason": reason, "stake_amount": stake},
    )


def _promote_to_operator(bot_name="RequesterBot"):
    """Grant operator status directly; in production this is a separate account."""
    from sqlalchemy import select

    from exchange.config import get_session
    from exchange.models import Account

    session_gen = get_session()
    session = next(session_gen)
    with session.begin():
        acct = session.execute(select(Account).where(Account.bot_name == bot_name)).scalar_one()
        acct.status = "operator"
        session.add(acct)
    session.close()


def _balance(client, auth_header, key):
    return client.get("/v1/exchange/balance", headers=auth_header(key)).json()


def _detail(client, auth_header, key, escrow_id):
    return client.get(
        f"/v1/exchange/escrows/{escrow_id}", headers=auth_header(key)
    ).json()


def test_dispute_opens_evidence_window(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        escrow, requester_key, _provider_key, _provider_id = _setup_escrow(client, auth_header)

        resp = _dispute(client, auth_header, requester_key, escrow["escrow_id"], "Incomplete work")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "evidence_pending"
        assert body["reason"] == "Incomplete work"
        assert body["stake_amount"] == STAKE
        assert body["evidence_window_closes_at"] is not None


def test_dispute_holds_the_stake(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        escrow, requester_key, _provider_key, _provider_id = _setup_escrow(client, auth_header)

        before = _balance(client, auth_header, requester_key)
        _dispute(client, auth_header, requester_key, escrow["escrow_id"])
        after = _balance(client, auth_header, requester_key)

        assert after["available"] == before["available"] - STAKE
        assert after["held_in_escrow"] == before["held_in_escrow"] + STAKE

        detail = _detail(client, auth_header, requester_key, escrow["escrow_id"])
        assert detail["dispute_stake_amount"] == STAKE
        assert detail["dispute_stake_status"] == "held"


def test_provider_can_dispute(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        escrow, _requester_key, provider_key, _provider_id = _setup_escrow(client, auth_header)

        resp = _dispute(
            client, auth_header, provider_key, escrow["escrow_id"], "Requester unresponsive"
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "evidence_pending"


def test_dispute_below_minimum_stake_rejected(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        escrow, requester_key, _provider_key, _provider_id = _setup_escrow(client, auth_header)

        resp = _dispute(client, auth_header, requester_key, escrow["escrow_id"], stake=STAKE - 1)
        assert resp.status_code == 400
        assert "at least" in resp.json()["detail"].lower()


def test_dispute_beyond_available_balance_rejected(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        escrow, requester_key, _provider_key, _provider_id = _setup_escrow(client, auth_header)

        resp = _dispute(client, auth_header, requester_key, escrow["escrow_id"], stake=10_000)
        assert resp.status_code == 400
        assert "insufficient balance" in resp.json()["detail"].lower()


def test_release_blocked_while_disputed(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        escrow, requester_key, _provider_key, _provider_id = _setup_escrow(client, auth_header)

        _dispute(client, auth_header, requester_key, escrow["escrow_id"])

        resp = client.post(
            "/v1/exchange/release",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow["escrow_id"]},
        )
        assert resp.status_code == 400
        assert "evidence_pending" in resp.json()["detail"].lower()


def test_refund_blocked_while_disputed(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        escrow, requester_key, _provider_key, _provider_id = _setup_escrow(client, auth_header)

        _dispute(client, auth_header, requester_key, escrow["escrow_id"])

        resp = client.post(
            "/v1/exchange/refund",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow["escrow_id"]},
        )
        assert resp.status_code == 400
        assert "evidence_pending" in resp.json()["detail"].lower()


def test_resolve_to_release(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        escrow, requester_key, provider_key, _provider_id = _setup_escrow(client, auth_header)

        _dispute(client, auth_header, requester_key, escrow["escrow_id"])
        _promote_to_operator()

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

        provider_bal = _balance(client, auth_header, provider_key)
        assert provider_bal["available"] == 110


def test_resolve_to_refund(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        escrow, requester_key, _provider_key, _provider_id = _setup_escrow(client, auth_header)

        _dispute(client, auth_header, requester_key, escrow["escrow_id"])
        _promote_to_operator()

        resp = client.post(
            "/v1/exchange/resolve",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow["escrow_id"], "resolution": "refund"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["resolution"] == "refund"
        assert body["status"] == "refunded"
        assert body["amount_returned"] == 11  # 10 + ceil(10 * 0.0025) fee

        bal = _balance(client, auth_header, requester_key)
        assert bal["held_in_escrow"] == 0


def test_resolve_returns_the_stake_by_default(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        escrow, requester_key, _provider_key, _provider_id = _setup_escrow(client, auth_header)

        before = _balance(client, auth_header, requester_key)
        _dispute(client, auth_header, requester_key, escrow["escrow_id"])
        _promote_to_operator()

        client.post(
            "/v1/exchange/resolve",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow["escrow_id"], "resolution": "release"},
        )

        # No stake_ruling means no finding against the filer, so the stake comes back.
        after = _balance(client, auth_header, requester_key)
        assert after["available"] == before["available"]
        assert _detail(client, auth_header, requester_key, escrow["escrow_id"])[
            "dispute_stake_status"
        ] == "returned"


def test_resolve_can_forfeit_the_stake(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        escrow, requester_key, provider_key, _provider_id = _setup_escrow(client, auth_header)

        _dispute(client, auth_header, requester_key, escrow["escrow_id"])
        _promote_to_operator()

        resp = client.post(
            "/v1/exchange/resolve",
            headers=auth_header(requester_key),
            json={
                "escrow_id": escrow["escrow_id"],
                "resolution": "release",
                "stake_ruling": "forfeit",
            },
        )
        assert resp.status_code == 200, resp.text

        # The counterparty receives the forfeited stake on top of the released amount.
        provider_bal = _balance(client, auth_header, provider_key)
        assert provider_bal["available"] == 110 + STAKE
        assert _detail(client, auth_header, requester_key, escrow["escrow_id"])[
            "dispute_stake_status"
        ] == "forfeited"


def test_resolve_with_strategy(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        escrow, requester_key, _provider_key, _provider_id = _setup_escrow(client, auth_header)

        _dispute(client, auth_header, requester_key, escrow["escrow_id"])
        _promote_to_operator()

        resp = client.post(
            "/v1/exchange/resolve",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow["escrow_id"], "resolution": "release", "strategy": "ai-mediator"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["resolution"] == "release"
        assert body["status"] == "released"

        detail = _detail(client, auth_header, requester_key, escrow["escrow_id"])
        assert detail["resolution_strategy"] == "ai-mediator"


def test_resolve_without_strategy_is_null(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        escrow, requester_key, _provider_key, _provider_id = _setup_escrow(client, auth_header)

        _dispute(client, auth_header, requester_key, escrow["escrow_id"])
        _promote_to_operator()

        resp = client.post(
            "/v1/exchange/resolve",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow["escrow_id"], "resolution": "refund"},
        )
        assert resp.status_code == 200, resp.text

        detail = _detail(client, auth_header, requester_key, escrow["escrow_id"])
        assert detail["resolution_strategy"] is None


def test_resolve_requires_operator(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        escrow, requester_key, _provider_key, _provider_id = _setup_escrow(client, auth_header)

        _dispute(client, auth_header, requester_key, escrow["escrow_id"])

        resp = client.post(
            "/v1/exchange/resolve",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow["escrow_id"], "resolution": "release"},
        )
        assert resp.status_code == 403
        assert "operator" in resp.json()["detail"].lower()
