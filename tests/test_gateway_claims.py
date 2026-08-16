"""Gateway registration gating and claim visibility."""

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

_GATEWAY_PAYLOAD = {
    **_REG_PAYLOAD,
    "bot_name": "TestGateway",
    "account_type": "gateway",
}


def _make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **env_overrides):
    monkeypatch.setenv("A2A_EXCHANGE_DATABASE_URL", f"sqlite:///{tmp_path / 'exchange.db'}")
    monkeypatch.setenv("A2A_EXCHANGE_AUTO_CREATE_SCHEMA", "true")
    monkeypatch.setenv("A2A_EXCHANGE_STARTER_TOKENS", "100")
    monkeypatch.setenv("A2A_EXCHANGE_FEE_PERCENT", "0.25")
    monkeypatch.setenv("A2A_EXCHANGE_REGISTER_RATE_LIMIT_HOUR", "0")
    monkeypatch.setenv("A2A_EXCHANGE_REGISTER_RATE_LIMIT_DAY", "0")
    monkeypatch.setenv("A2A_EXCHANGE_INVITE_CODE", "")
    monkeypatch.setenv("A2A_EXCHANGE_GATEWAY_INVITE_CODE", "")
    for k, v in env_overrides.items():
        monkeypatch.setenv(k, v)

    import exchange.config as config_mod
    import exchange.ratelimit as ratelimit_mod
    import exchange.auth as auth_mod
    import exchange.routes.accounts as accounts_mod
    import exchange.routes.settlement as settlement_mod
    import exchange.app as app_mod

    importlib.reload(config_mod)
    importlib.reload(ratelimit_mod)
    importlib.reload(auth_mod)
    importlib.reload(accounts_mod)
    importlib.reload(settlement_mod)
    importlib.reload(app_mod)
    return app_mod.create_app()


def _register_agent(client: TestClient, bot_name: str = "AgentBot") -> dict:
    resp = client.post(
        "/v1/accounts/register",
        json={**_REG_PAYLOAD, "bot_name": bot_name},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _register_gateway(
    client: TestClient, invite_code: str, bot_name: str = "GwBot"
) -> dict:
    resp = client.post(
        "/v1/accounts/register",
        json={
            **_GATEWAY_PAYLOAD,
            "bot_name": bot_name,
            "invite_code": invite_code,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- Gateway registration gating ---


def test_gateway_registration_rejected_when_invite_unset(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)  # gateway_invite_code empty
    with TestClient(app) as client:
        resp = client.post("/v1/accounts/register", json=_GATEWAY_PAYLOAD)
        assert resp.status_code == 403
        assert "not enabled" in resp.json()["detail"].lower()


def test_gateway_registration_rejected_with_wrong_code(tmp_path, monkeypatch):
    app = _make_app(
        tmp_path, monkeypatch, A2A_EXCHANGE_GATEWAY_INVITE_CODE="gw-secret"
    )
    with TestClient(app) as client:
        resp = client.post(
            "/v1/accounts/register",
            json={**_GATEWAY_PAYLOAD, "invite_code": "wrong"},
        )
        assert resp.status_code == 403
        assert "gateway invite" in resp.json()["detail"].lower()


def test_gateway_registration_accepted_with_correct_code(tmp_path, monkeypatch):
    app = _make_app(
        tmp_path, monkeypatch, A2A_EXCHANGE_GATEWAY_INVITE_CODE="gw-secret"
    )
    with TestClient(app) as client:
        resp = client.post(
            "/v1/accounts/register",
            json={**_GATEWAY_PAYLOAD, "invite_code": "gw-secret"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["account"]["bot_name"] == "TestGateway"
        # account_type is not on RegisterAccountInfo; verify via GET
        aid = body["account"]["id"]
        acct = client.get(f"/v1/accounts/{aid}").json()
        assert acct["account_type"] == "gateway"


def test_agent_registration_unaffected_by_gateway_invite(tmp_path, monkeypatch):
    app = _make_app(
        tmp_path, monkeypatch, A2A_EXCHANGE_GATEWAY_INVITE_CODE="gw-secret"
    )
    with TestClient(app) as client:
        resp = client.post("/v1/accounts/register", json=_REG_PAYLOAD)
        assert resp.status_code == 201


def test_agent_invite_code_independent_of_gateway_invite(tmp_path, monkeypatch):
    app = _make_app(
        tmp_path,
        monkeypatch,
        A2A_EXCHANGE_INVITE_CODE="agent-secret",
        A2A_EXCHANGE_GATEWAY_INVITE_CODE="gw-secret",
    )
    with TestClient(app) as client:
        # Agent needs agent invite, not gateway invite
        resp = client.post(
            "/v1/accounts/register",
            json={**_REG_PAYLOAD, "invite_code": "gw-secret"},
        )
        assert resp.status_code == 403

        resp = client.post(
            "/v1/accounts/register",
            json={**_REG_PAYLOAD, "invite_code": "agent-secret"},
        )
        assert resp.status_code == 201

        # Gateway needs gateway invite, not agent invite
        resp = client.post(
            "/v1/accounts/register",
            json={
                **_GATEWAY_PAYLOAD,
                "bot_name": "Gw1",
                "invite_code": "agent-secret",
            },
        )
        assert resp.status_code == 403

        resp = client.post(
            "/v1/accounts/register",
            json={
                **_GATEWAY_PAYLOAD,
                "bot_name": "Gw2",
                "invite_code": "gw-secret",
            },
        )
        assert resp.status_code == 201


# --- Claim auth + visibility ---


def test_claim_403_for_agent_type_caller(tmp_path, monkeypatch, auth_header):
    app = _make_app(
        tmp_path, monkeypatch, A2A_EXCHANGE_GATEWAY_INVITE_CODE="gw-secret"
    )
    with TestClient(app) as client:
        agent_a = _register_agent(client, "AgentA")
        agent_b = _register_agent(client, "AgentB")

        resp = client.post(
            f"/v1/accounts/{agent_b['account']['id']}/claim",
            headers=auth_header(agent_a["api_key"]),
            json={},
        )
        assert resp.status_code == 403
        assert "gateway" in resp.json()["detail"].lower()


def test_unverified_claims_hidden_from_public_endpoints(
    tmp_path, monkeypatch, auth_header
):
    app = _make_app(
        tmp_path, monkeypatch, A2A_EXCHANGE_GATEWAY_INVITE_CODE="gw-secret"
    )
    with TestClient(app) as client:
        agent = _register_agent(client, "ClaimedAgent")
        gateway = _register_gateway(client, "gw-secret", "SoftGateway")
        agent_id = agent["account"]["id"]

        # Soft claim (no agent_api_key) -> verified=False
        claim_resp = client.post(
            f"/v1/accounts/{agent_id}/claim",
            headers=auth_header(gateway["api_key"]),
            json={},
        )
        assert claim_resp.status_code == 201, claim_resp.text
        assert claim_resp.json()["verified"] is False

        # Public GET /accounts/{id} — no unverified claims
        acct = client.get(f"/v1/accounts/{agent_id}").json()
        assert acct.get("gateway_claims") in (None, [])

        # Public GET /accounts/{id}/claims — empty
        claims = client.get(f"/v1/accounts/{agent_id}/claims").json()
        assert claims["count"] == 0
        assert claims["claims"] == []


def test_verified_claims_visible_publicly(tmp_path, monkeypatch, auth_header):
    app = _make_app(
        tmp_path, monkeypatch, A2A_EXCHANGE_GATEWAY_INVITE_CODE="gw-secret"
    )
    with TestClient(app) as client:
        agent = _register_agent(client, "VerifiedAgent")
        gateway = _register_gateway(client, "gw-secret", "VerifiedGateway")
        agent_id = agent["account"]["id"]

        claim_resp = client.post(
            f"/v1/accounts/{agent_id}/claim",
            headers=auth_header(gateway["api_key"]),
            json={"agent_api_key": agent["api_key"]},
        )
        assert claim_resp.status_code == 201, claim_resp.text
        assert claim_resp.json()["verified"] is True

        acct = client.get(f"/v1/accounts/{agent_id}").json()
        assert acct.get("gateway_claims")
        assert len(acct["gateway_claims"]) == 1
        assert acct["gateway_claims"][0]["verified"] is True
        assert acct["gateway_claims"][0]["gateway_name"] == "VerifiedGateway"

        claims = client.get(f"/v1/accounts/{agent_id}/claims").json()
        assert claims["count"] == 1
        assert claims["claims"][0]["verified"] is True


def test_unverified_claims_visible_to_authenticated_agent(
    tmp_path, monkeypatch, auth_header
):
    app = _make_app(
        tmp_path, monkeypatch, A2A_EXCHANGE_GATEWAY_INVITE_CODE="gw-secret"
    )
    with TestClient(app) as client:
        agent = _register_agent(client, "OwnerAgent")
        gateway = _register_gateway(client, "gw-secret", "SoftGw")
        agent_id = agent["account"]["id"]

        claim_resp = client.post(
            f"/v1/accounts/{agent_id}/claim",
            headers=auth_header(gateway["api_key"]),
            json={},
        )
        assert claim_resp.status_code == 201
        assert claim_resp.json()["verified"] is False

        # Agent sees its soft claim
        claims = client.get(
            f"/v1/accounts/{agent_id}/claims",
            headers=auth_header(agent["api_key"]),
        ).json()
        assert claims["count"] == 1
        assert claims["claims"][0]["verified"] is False
        assert claims["claims"][0]["gateway_id"] == gateway["account"]["id"]

        # Claiming gateway also sees its own soft claim
        claims_gw = client.get(
            f"/v1/accounts/{agent_id}/claims",
            headers=auth_header(gateway["api_key"]),
        ).json()
        assert claims_gw["count"] == 1

        # Unrelated agent does not
        other = _register_agent(client, "OtherAgent")
        claims_other = client.get(
            f"/v1/accounts/{agent_id}/claims",
            headers=auth_header(other["api_key"]),
        ).json()
        assert claims_other["count"] == 0
