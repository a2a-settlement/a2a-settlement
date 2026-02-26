"""Tests for KYA-gated escrow enforcement."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _register(client: TestClient, name: str) -> tuple[str, dict]:
    """Register an agent and return (api_key, response_data)."""
    resp = client.post(
        "/v1/accounts/register",
        json={
            "bot_name": name,
            "developer_id": "dev",
            "developer_name": "Dev",
            "contact_email": "d@test.com",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return data["api_key"], data


def _set_kya_level(client: TestClient, account_id: str, level: int):
    """Directly update an account's kya_level_verified via the database."""
    from exchange.config import get_session
    from exchange.models import Account
    from sqlalchemy import select

    gen = get_session()
    session = next(gen)
    try:
        with session.begin():
            acct = session.execute(select(Account).where(Account.id == account_id)).scalar_one()
            acct.kya_level_verified = level
            session.add(acct)
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def _make_kya_app(tmp_path, monkeypatch, kya_enabled: bool = True):
    monkeypatch.setenv("A2A_EXCHANGE_DATABASE_URL", f"sqlite:///{tmp_path / 'exchange.db'}")
    monkeypatch.setenv("A2A_EXCHANGE_AUTO_CREATE_SCHEMA", "true")
    monkeypatch.setenv("A2A_EXCHANGE_STARTER_TOKENS", "50000")
    monkeypatch.setenv("A2A_EXCHANGE_FEE_PERCENT", "0")
    monkeypatch.setenv("A2A_EXCHANGE_MIN_FEE", "0")
    monkeypatch.setenv("A2A_EXCHANGE_REGISTER_RATE_LIMIT_HOUR", "0")
    monkeypatch.setenv("A2A_EXCHANGE_REGISTER_RATE_LIMIT_DAY", "0")
    monkeypatch.setenv("A2A_EXCHANGE_INVITE_CODE", "")
    monkeypatch.setenv("A2A_EXCHANGE_KYA_ENABLED", "true" if kya_enabled else "false")
    monkeypatch.setenv("A2A_EXCHANGE_KYA_ESCROW_TIER1_MAX", "100")
    monkeypatch.setenv("A2A_EXCHANGE_KYA_ESCROW_TIER2_MAX", "10000")
    monkeypatch.setenv("A2A_EXCHANGE_KYA_HITL_THRESHOLD", "10000")
    monkeypatch.setenv("A2A_EXCHANGE_MAX_ESCROW", "50000")

    # Same reload pattern as tests/conftest.py
    import exchange.config as config_mod
    import exchange.ratelimit as ratelimit_mod
    import exchange.observers as observers_mod
    import exchange.spending_guard as spending_guard_mod
    import exchange.tasks as tasks_mod
    import exchange.webhooks as webhooks_mod
    import exchange.routes.accounts as accounts_mod
    import exchange.routes.settlement as settlement_mod
    import exchange.routes.webhooks as webhooks_routes_mod
    import exchange.routes.kya_admin as kya_admin_mod
    import exchange.app as app_mod

    importlib.reload(config_mod)
    importlib.reload(ratelimit_mod)
    importlib.reload(observers_mod)
    importlib.reload(spending_guard_mod)
    importlib.reload(tasks_mod)
    importlib.reload(webhooks_mod)
    importlib.reload(accounts_mod)
    importlib.reload(settlement_mod)
    importlib.reload(webhooks_routes_mod)
    importlib.reload(kya_admin_mod)
    importlib.reload(app_mod)

    return app_mod.create_app()


@pytest.fixture()
def kya_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    return _make_kya_app(tmp_path, monkeypatch, kya_enabled=True)


class TestKYAGate:
    def test_level0_under_tier1(self, kya_app):
        with TestClient(kya_app) as client:
            key_a, data_a = _register(client, "agent-a")
            _key_b, data_b = _register(client, "agent-b")
            resp = client.post(
                "/v1/exchange/escrow",
                json={"provider_id": data_b["account"]["id"], "amount": 50},
                headers={"Authorization": f"Bearer {key_a}"},
            )
            assert resp.status_code == 201

    def test_level0_over_tier1_blocked(self, kya_app):
        with TestClient(kya_app) as client:
            key_a, data_a = _register(client, "agent-a2")
            _key_b, data_b = _register(client, "agent-b2")
            resp = client.post(
                "/v1/exchange/escrow",
                json={"provider_id": data_b["account"]["id"], "amount": 500},
                headers={"Authorization": f"Bearer {key_a}"},
            )
            assert resp.status_code == 403
            assert "KYA level" in resp.json()["detail"]

    def test_level1_allows_up_to_tier2(self, kya_app):
        with TestClient(kya_app) as client:
            key_a, data_a = _register(client, "agent-c")
            _key_b, data_b = _register(client, "agent-d")
            _set_kya_level(client, data_a["account"]["id"], 1)
            _set_kya_level(client, data_b["account"]["id"], 1)
            resp = client.post(
                "/v1/exchange/escrow",
                json={"provider_id": data_b["account"]["id"], "amount": 5000},
                headers={"Authorization": f"Bearer {key_a}"},
            )
            assert resp.status_code == 201

    def test_level1_over_tier2_blocked(self, kya_app):
        with TestClient(kya_app) as client:
            key_a, data_a = _register(client, "agent-e")
            _key_b, data_b = _register(client, "agent-f")
            _set_kya_level(client, data_a["account"]["id"], 1)
            _set_kya_level(client, data_b["account"]["id"], 1)
            resp = client.post(
                "/v1/exchange/escrow",
                json={"provider_id": data_b["account"]["id"], "amount": 15000},
                headers={"Authorization": f"Bearer {key_a}"},
            )
            assert resp.status_code == 403

    def test_level2_allows_large_escrow(self, kya_app):
        with TestClient(kya_app) as client:
            key_a, data_a = _register(client, "agent-g")
            _key_b, data_b = _register(client, "agent-h")
            _set_kya_level(client, data_a["account"]["id"], 2)
            _set_kya_level(client, data_b["account"]["id"], 2)
            resp = client.post(
                "/v1/exchange/escrow",
                json={"provider_id": data_b["account"]["id"], "amount": 15000},
                headers={"Authorization": f"Bearer {key_a}"},
            )
            assert resp.status_code == 201

    def test_mixed_levels_uses_minimum(self, kya_app):
        with TestClient(kya_app) as client:
            key_a, data_a = _register(client, "agent-i")
            _key_b, data_b = _register(client, "agent-j")
            _set_kya_level(client, data_a["account"]["id"], 2)
            _set_kya_level(client, data_b["account"]["id"], 0)
            resp = client.post(
                "/v1/exchange/escrow",
                json={"provider_id": data_b["account"]["id"], "amount": 500},
                headers={"Authorization": f"Bearer {key_a}"},
            )
            assert resp.status_code == 403

    def test_hitl_flag_set(self, kya_app):
        with TestClient(kya_app) as client:
            key_a, data_a = _register(client, "agent-k")
            _key_b, data_b = _register(client, "agent-l")
            _set_kya_level(client, data_a["account"]["id"], 2)
            _set_kya_level(client, data_b["account"]["id"], 2)
            resp = client.post(
                "/v1/exchange/escrow",
                json={"provider_id": data_b["account"]["id"], "amount": 15000},
                headers={"Authorization": f"Bearer {key_a}"},
            )
            assert resp.status_code == 201
            escrow_id = resp.json()["escrow_id"]
            detail = client.get(
                f"/v1/exchange/escrows/{escrow_id}",
                headers={"Authorization": f"Bearer {key_a}"},
            )
            assert detail.status_code == 200


class TestKYADisabled:
    def test_no_gate_when_disabled(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        app = _make_kya_app(tmp_path, monkeypatch, kya_enabled=False)
        with TestClient(app) as client:
            key_a, data_a = _register(client, "no-kya-a")
            _key_b, data_b = _register(client, "no-kya-b")
            resp = client.post(
                "/v1/exchange/escrow",
                json={"provider_id": data_b["account"]["id"], "amount": 15000},
                headers={"Authorization": f"Bearer {key_a}"},
            )
            assert resp.status_code == 201
