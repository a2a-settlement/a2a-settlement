from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient


def _register_pair(client):
    provider = client.post(
        "/v1/accounts/register",
        json={
            "bot_name": "ProviderBot",
            "developer_id": "dev",
            "developer_name": "Test Dev",
            "contact_email": "test@test.dev",
            "skills": ["sentiment-analysis"],
        },
    ).json()
    requester = client.post(
        "/v1/accounts/register",
        json={
            "bot_name": "RequesterBot",
            "developer_id": "dev",
            "developer_name": "Test Dev",
            "contact_email": "test@test.dev",
            "skills": ["orchestration"],
        },
    ).json()
    return provider, requester


def _set_daily_limit(account_id: str, limit: int):
    from exchange.config import get_session
    from exchange.models import Account
    from sqlalchemy import select

    session_gen = get_session()
    session = next(session_gen)
    with session.begin():
        acct = session.execute(select(Account).where(Account.id == account_id)).scalar_one()
        acct.daily_spend_limit = limit
        session.add(acct)
    session.close()


def _get_frozen_until(account_id: str):
    from exchange.config import get_session
    from exchange.models import Account
    from sqlalchemy import select

    session_gen = get_session()
    session = next(session_gen)
    with session.begin():
        acct = session.execute(select(Account).where(Account.id == account_id)).scalar_one()
        result = acct.frozen_until
    session.close()
    return result


def test_rolling_window_limit_blocks_escrow(exchange_app, auth_header):
    """Exceeding the rolling-window daily limit blocks new escrows and freezes the account."""
    with TestClient(exchange_app) as client:
        provider, requester = _register_pair(client)
        provider_id = provider["account"]["id"]
        requester_id = requester["account"]["id"]
        requester_key = requester["api_key"]

        _set_daily_limit(requester_id, 30)

        resp1 = client.post(
            "/v1/exchange/escrow",
            headers=auth_header(requester_key),
            json={"provider_id": provider_id, "amount": 20},
        )
        assert resp1.status_code == 201

        resp2 = client.post(
            "/v1/exchange/escrow",
            headers=auth_header(requester_key),
            json={"provider_id": provider_id, "amount": 20, "task_id": "second"},
        )
        assert resp2.status_code == 400
        assert "spend limit" in resp2.json()["detail"].lower()

        frozen = _get_frozen_until(requester_id)
        assert frozen is not None


def test_frozen_account_returns_423(exchange_app, auth_header):
    """A frozen account receives 423 Locked on escrow creation attempts."""
    with TestClient(exchange_app) as client:
        provider, requester = _register_pair(client)
        provider_id = provider["account"]["id"]
        requester_id = requester["account"]["id"]
        requester_key = requester["api_key"]

        _set_daily_limit(requester_id, 30)

        client.post(
            "/v1/exchange/escrow",
            headers=auth_header(requester_key),
            json={"provider_id": provider_id, "amount": 20},
        )
        client.post(
            "/v1/exchange/escrow",
            headers=auth_header(requester_key),
            json={"provider_id": provider_id, "amount": 20, "task_id": "trigger"},
        )

        resp = client.post(
            "/v1/exchange/escrow",
            headers=auth_header(requester_key),
            json={"provider_id": provider_id, "amount": 1, "task_id": "blocked"},
        )
        assert resp.status_code == 423
        assert "frozen" in resp.json()["detail"].lower()


def test_freeze_expires_and_allows_escrow(exchange_app, auth_header):
    """After the freeze period expires, escrow creation is allowed again."""
    with TestClient(exchange_app) as client:
        provider, requester = _register_pair(client)
        provider_id = provider["account"]["id"]
        requester_id = requester["account"]["id"]
        requester_key = requester["api_key"]

        _set_daily_limit(requester_id, 30)

        client.post(
            "/v1/exchange/escrow",
            headers=auth_header(requester_key),
            json={"provider_id": provider_id, "amount": 20},
        )
        client.post(
            "/v1/exchange/escrow",
            headers=auth_header(requester_key),
            json={"provider_id": provider_id, "amount": 20, "task_id": "trigger"},
        )

        future = datetime.now(timezone.utc) + timedelta(minutes=60)
        with patch("exchange.spending_guard._now", return_value=future):
            resp = client.post(
                "/v1/exchange/escrow",
                headers=auth_header(requester_key),
                json={"provider_id": provider_id, "amount": 1, "task_id": "after-freeze"},
            )
        assert resp.status_code == 201


def test_no_limit_allows_spending(exchange_app, auth_header):
    """With no daily_spend_limit set, spending proceeds without restriction."""
    with TestClient(exchange_app) as client:
        provider, requester = _register_pair(client)
        provider_id = provider["account"]["id"]
        requester_key = requester["api_key"]

        resp = client.post(
            "/v1/exchange/escrow",
            headers=auth_header(requester_key),
            json={"provider_id": provider_id, "amount": 50},
        )
        assert resp.status_code == 201


def test_hourly_velocity_limit(exchange_app, auth_header, monkeypatch):
    """Exceeding the hourly velocity limit triggers a freeze."""
    monkeypatch.setenv("A2A_EXCHANGE_HOURLY_VELOCITY_LIMIT", "25")

    import importlib
    import exchange.config as config_mod
    import exchange.spending_guard as sg_mod
    import exchange.routes.settlement as settlement_mod

    importlib.reload(config_mod)
    importlib.reload(sg_mod)
    importlib.reload(settlement_mod)

    import exchange.app as app_mod
    importlib.reload(app_mod)
    app = app_mod.create_app()

    with TestClient(app) as client:
        provider, requester = _register_pair(client)
        provider_id = provider["account"]["id"]
        requester_id = requester["account"]["id"]
        requester_key = requester["api_key"]

        resp1 = client.post(
            "/v1/exchange/escrow",
            headers=auth_header(requester_key),
            json={"provider_id": provider_id, "amount": 20},
        )
        assert resp1.status_code == 201

        resp2 = client.post(
            "/v1/exchange/escrow",
            headers=auth_header(requester_key),
            json={"provider_id": provider_id, "amount": 10, "task_id": "second"},
        )
        assert resp2.status_code == 400
        assert "velocity" in resp2.json()["detail"].lower()

        frozen = _get_frozen_until(requester_id)
        assert frozen is not None


def test_batch_escrow_respects_spending_limit(exchange_app, auth_header):
    """Batch escrow creation also enforces the spending limit."""
    with TestClient(exchange_app) as client:
        provider, requester = _register_pair(client)
        provider_id = provider["account"]["id"]
        requester_id = requester["account"]["id"]
        requester_key = requester["api_key"]

        _set_daily_limit(requester_id, 15)

        resp = client.post(
            "/v1/exchange/escrow/batch",
            headers=auth_header(requester_key),
            json={
                "escrows": [
                    {"provider_id": provider_id, "amount": 10, "task_id": "a"},
                    {"provider_id": provider_id, "amount": 10, "task_id": "b"},
                ],
            },
        )
        assert resp.status_code == 400
        assert "spend limit" in resp.json()["detail"].lower()
