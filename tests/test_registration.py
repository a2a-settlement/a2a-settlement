from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_REG_PAYLOAD = {
    "bot_name": "TestBot",
    "developer_id": "dev-test",
    "developer_name": "Test Dev",
    "contact_email": "test@test.dev",
}


def _make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **env_overrides):
    monkeypatch.setenv("A2A_EXCHANGE_DATABASE_URL", f"sqlite:///{tmp_path / 'exchange.db'}")
    monkeypatch.setenv("A2A_EXCHANGE_AUTO_CREATE_SCHEMA", "true")
    monkeypatch.setenv("A2A_EXCHANGE_STARTER_TOKENS", "100")
    monkeypatch.setenv("A2A_EXCHANGE_FEE_PERCENT", "3")
    monkeypatch.setenv("A2A_EXCHANGE_REGISTER_RATE_LIMIT_HOUR", "0")
    monkeypatch.setenv("A2A_EXCHANGE_REGISTER_RATE_LIMIT_DAY", "0")
    monkeypatch.setenv("A2A_EXCHANGE_INVITE_CODE", "")
    for k, v in env_overrides.items():
        monkeypatch.setenv(k, v)

    import exchange.config as config_mod
    import exchange.ratelimit as ratelimit_mod
    import exchange.routes.accounts as accounts_mod
    import exchange.routes.settlement as settlement_mod
    import exchange.app as app_mod

    importlib.reload(config_mod)
    importlib.reload(ratelimit_mod)
    importlib.reload(accounts_mod)
    importlib.reload(settlement_mod)
    importlib.reload(app_mod)
    return app_mod.create_app()


# --- Required fields ---


def test_register_requires_developer_name(exchange_app):
    with TestClient(exchange_app) as client:
        resp = client.post("/v1/accounts/register", json={
            "bot_name": "Bot", "developer_id": "dev", "contact_email": "a@b.com",
        })
        assert resp.status_code == 422


def test_register_requires_contact_email(exchange_app):
    with TestClient(exchange_app) as client:
        resp = client.post("/v1/accounts/register", json={
            "bot_name": "Bot", "developer_id": "dev", "developer_name": "Dev",
        })
        assert resp.status_code == 422


def test_register_rejects_invalid_email(exchange_app):
    with TestClient(exchange_app) as client:
        resp = client.post("/v1/accounts/register", json={
            **_REG_PAYLOAD, "contact_email": "not-an-email",
        })
        assert resp.status_code == 422


def test_register_success_with_all_fields(exchange_app):
    with TestClient(exchange_app) as client:
        resp = client.post("/v1/accounts/register", json=_REG_PAYLOAD)
        assert resp.status_code == 201
        body = resp.json()
        assert body["account"]["developer_name"] == "Test Dev"
        assert body["account"]["contact_email"] == "test@test.dev"


# --- Invite code ---


def test_invite_code_required_when_configured(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch, A2A_EXCHANGE_INVITE_CODE="secret-123")
    with TestClient(app) as client:
        resp = client.post("/v1/accounts/register", json=_REG_PAYLOAD)
        assert resp.status_code == 403

        resp = client.post("/v1/accounts/register", json={**_REG_PAYLOAD, "invite_code": "wrong"})
        assert resp.status_code == 403

        resp = client.post("/v1/accounts/register", json={**_REG_PAYLOAD, "invite_code": "secret-123"})
        assert resp.status_code == 201


def test_invite_code_not_required_when_empty(exchange_app):
    with TestClient(exchange_app) as client:
        resp = client.post("/v1/accounts/register", json=_REG_PAYLOAD)
        assert resp.status_code == 201


# --- Rate limiting ---


def test_rate_limit_blocks_after_threshold(tmp_path, monkeypatch):
    app = _make_app(
        tmp_path, monkeypatch,
        A2A_EXCHANGE_REGISTER_RATE_LIMIT_HOUR="2",
        A2A_EXCHANGE_REGISTER_RATE_LIMIT_DAY="10",
    )
    with TestClient(app) as client:
        for i in range(2):
            resp = client.post("/v1/accounts/register", json={
                **_REG_PAYLOAD, "bot_name": f"Bot{i}",
            })
            assert resp.status_code == 201, resp.text

        resp = client.post("/v1/accounts/register", json={
            **_REG_PAYLOAD, "bot_name": "Bot2",
        })
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers


# --- Admin suspend ---


def test_suspend_requires_operator(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        reg = client.post("/v1/accounts/register", json=_REG_PAYLOAD).json()
        api_key = reg["api_key"]
        account_id = reg["account"]["id"]

        resp = client.post(
            "/v1/accounts/admin/suspend",
            headers=auth_header(api_key),
            json={"account_id": account_id},
        )
        assert resp.status_code == 403


def test_suspend_marks_account(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        target = client.post("/v1/accounts/register", json=_REG_PAYLOAD).json()
        operator = client.post("/v1/accounts/register", json={
            **_REG_PAYLOAD, "bot_name": "OperatorBot",
        }).json()

        from exchange.config import get_session
        from exchange.models import Account
        from sqlalchemy import select

        session_gen = get_session()
        session = next(session_gen)
        with session.begin():
            acct = session.execute(
                select(Account).where(Account.bot_name == "OperatorBot")
            ).scalar_one()
            acct.status = "operator"
            session.add(acct)
        session.close()

        resp = client.post(
            "/v1/accounts/admin/suspend",
            headers=auth_header(operator["api_key"]),
            json={"account_id": target["account"]["id"], "reason": "spam"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "suspended"
        assert body["reason"] == "spam"

        bal_resp = client.get(
            "/v1/exchange/balance",
            headers=auth_header(target["api_key"]),
        )
        assert bal_resp.status_code == 401
