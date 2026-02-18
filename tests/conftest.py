from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture()
def exchange_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Isolated DB per test.
    monkeypatch.setenv("A2A_EXCHANGE_DATABASE_URL", f"sqlite:///{tmp_path / 'exchange.db'}")
    monkeypatch.setenv("A2A_EXCHANGE_AUTO_CREATE_SCHEMA", "true")
    monkeypatch.setenv("A2A_EXCHANGE_STARTER_TOKENS", "100")
    monkeypatch.setenv("A2A_EXCHANGE_FEE_PERCENT", "0.25")
    monkeypatch.setenv("A2A_EXCHANGE_REGISTER_RATE_LIMIT_HOUR", "0")
    monkeypatch.setenv("A2A_EXCHANGE_REGISTER_RATE_LIMIT_DAY", "0")
    monkeypatch.setenv("A2A_EXCHANGE_INVITE_CODE", "")

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


@pytest.fixture()
def auth_header():
    def _auth(api_key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {api_key}"}

    return _auth

