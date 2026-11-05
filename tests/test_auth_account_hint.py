from __future__ import annotations

from fastapi.testclient import TestClient

import exchange.auth as auth_mod


def _register(client, name: str) -> tuple[str, str]:
    body = client.post(
        "/v1/accounts/register",
        json={
            "bot_name": name,
            "developer_id": "dev",
            "developer_name": "Test Dev",
            "contact_email": f"{name.lower()}@test.dev",
            "skills": ["testing"],
        },
    ).json()
    return body["account"]["id"], body["api_key"]


def _balance(client, api_key: str, account_id: str | None = None):
    headers = {"Authorization": f"Bearer {api_key}"}
    if account_id is not None:
        headers[auth_mod.ACCOUNT_ID_HEADER] = account_id
    return client.get("/v1/exchange/balance", headers=headers)


def test_without_hint_any_account_key_still_works(exchange_app):
    with TestClient(exchange_app) as client:
        a_id, a_key = _register(client, "AlphaBot")
        b_id, b_key = _register(client, "BetaBot")
        assert _balance(client, a_key).json()["account_id"] == a_id
        assert _balance(client, b_key).json()["account_id"] == b_id


def test_hint_matching_account_succeeds(exchange_app):
    with TestClient(exchange_app) as client:
        a_id, a_key = _register(client, "AlphaBot")
        _register(client, "BetaBot")
        resp = _balance(client, a_key, a_id)
        assert resp.status_code == 200
        assert resp.json()["account_id"] == a_id


def test_hint_for_another_account_is_401(exchange_app):
    with TestClient(exchange_app) as client:
        _, a_key = _register(client, "AlphaBot")
        b_id, _ = _register(client, "BetaBot")
        resp = _balance(client, a_key, b_id)
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid API key"


def test_hint_for_unknown_account_is_401(exchange_app):
    with TestClient(exchange_app) as client:
        _, a_key = _register(client, "AlphaBot")
        assert _balance(client, a_key, "no-such-account").status_code == 401


def test_blank_hint_is_401(exchange_app):
    with TestClient(exchange_app) as client:
        _, a_key = _register(client, "AlphaBot")
        assert _balance(client, a_key, "   ").status_code == 401


def test_hint_checks_one_hash(exchange_app, monkeypatch):
    with TestClient(exchange_app) as client:
        a_id, a_key = _register(client, "AlphaBot")
        for name in ("BetaBot", "GammaBot", "DeltaBot"):
            _register(client, name)
        calls = []
        real = auth_mod._check_api_key

        def counting(api_key, api_key_hash):
            calls.append(1)
            return real(api_key, api_key_hash)

        monkeypatch.setattr(auth_mod, "_check_api_key", counting)
        assert _balance(client, "ate_wrong_key_for_alpha", a_id).status_code == 401
        assert len(calls) == 1


def test_lookup_runs_off_the_event_loop(exchange_app, monkeypatch):
    with TestClient(exchange_app) as client:
        a_id, a_key = _register(client, "AlphaBot")
        seen = []
        real = auth_mod.run_in_threadpool

        async def recording(func, *args, **kwargs):
            seen.append(func)
            return await real(func, *args, **kwargs)

        monkeypatch.setattr(auth_mod, "run_in_threadpool", recording)
        assert _balance(client, a_key, a_id).status_code == 200
        assert auth_mod._find_account in seen
