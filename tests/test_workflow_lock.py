"""Escrows marked workflow_locked can be cancelled only with the cancel secret."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text

CANCEL_SECRET = "workflow-cancel-secret"
CANCEL_HASH = hashlib.sha256(CANCEL_SECRET.encode()).hexdigest()
STAKE = 10


def _register_pair(client: TestClient) -> tuple[str, str, str]:
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
    return provider["account"]["id"], requester["api_key"], provider["api_key"]


def _create(client, auth_header, requester_key, provider_id, **extra):
    body = {"provider_id": provider_id, "amount": 10, **extra}
    return client.post(
        "/v1/exchange/escrow",
        headers=auth_header(requester_key),
        json=body,
    )


def _detail(client, auth_header, key, escrow_id) -> dict:
    resp = client.get(
        f"/v1/exchange/escrows/{escrow_id}",
        headers=auth_header(key),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _balance(client, auth_header, key) -> dict:
    return client.get("/v1/exchange/balance", headers=auth_header(key)).json()


@pytest.mark.parametrize(
    "extra",
    [
        {"workflow_locked": True},
        {"workflow_locked": True, "cancel_secret_hash": "abcd"},
        {"workflow_locked": True, "cancel_secret_hash": ""},
        {"workflow_locked": True, "cancel_secret_hash": "A" * 64},
        {"workflow_locked": True, "cancel_secret_hash": "g" * 64},
        {"cancel_secret_hash": CANCEL_HASH},
        {"workflow_locked": False, "cancel_secret_hash": CANCEL_HASH},
    ],
)
def test_locked_create_requires_valid_hash(exchange_app, auth_header, extra):
    with TestClient(exchange_app) as client:
        provider_id, requester_key, _provider_key = _register_pair(client)
        resp = _create(client, auth_header, requester_key, provider_id, **extra)
        assert resp.status_code == 422, resp.text


def test_get_shows_workflow_lock_and_hides_hash(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        provider_id, requester_key, _provider_key = _register_pair(client)
        created = _create(
            client,
            auth_header,
            requester_key,
            provider_id,
            workflow_locked=True,
            cancel_secret_hash=CANCEL_HASH,
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert "cancel_secret_hash" not in body
        assert CANCEL_SECRET not in created.text
        assert CANCEL_HASH not in created.text

        detail = _detail(client, auth_header, requester_key, body["escrow_id"])
        assert detail["workflow_locked"] is True
        assert detail["status"] == "held"
        assert "cancel_secret_hash" not in detail
        assert CANCEL_SECRET not in str(detail)
        assert CANCEL_HASH not in str(detail)


def test_locked_refund_requires_matching_secret(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        provider_id, requester_key, _provider_key = _register_pair(client)
        created = _create(
            client,
            auth_header,
            requester_key,
            provider_id,
            workflow_locked=True,
            cancel_secret_hash=CANCEL_HASH,
        )
        assert created.status_code == 201, created.text
        escrow_id = created.json()["escrow_id"]
        held = _balance(client, auth_header, requester_key)
        assert held["held_in_escrow"] == 11
        assert held["available"] == 89

        missing = client.post(
            "/v1/exchange/refund",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow_id},
        )
        assert missing.status_code == 409, missing.text
        assert missing.json()["detail"] == "escrow is workflow locked"

        wrong = client.post(
            "/v1/exchange/refund",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow_id, "cancel_secret": "not-the-secret"},
        )
        assert wrong.status_code == 409, wrong.text
        assert wrong.json()["detail"] == "escrow is workflow locked"
        assert CANCEL_SECRET not in wrong.text
        still = _balance(client, auth_header, requester_key)
        assert still["held_in_escrow"] == 11
        assert still["available"] == 89
        assert _detail(client, auth_header, requester_key, escrow_id)["status"] == "held"

        headers = auth_header(requester_key)
        headers["Idempotency-Key"] = "locked-refund-1"
        ok = client.post(
            "/v1/exchange/refund",
            headers=headers,
            json={
                "escrow_id": escrow_id,
                "reason": "workflow cancelled",
                "cancel_secret": CANCEL_SECRET,
            },
        )
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["status"] == "refunded"
        assert body["amount_returned"] == 11
        assert CANCEL_SECRET not in ok.text
        assert "cancel_secret" not in body

        ended = _balance(client, auth_header, requester_key)
        assert ended["available"] == 100
        assert ended["held_in_escrow"] == 0

        detail = _detail(client, auth_header, requester_key, escrow_id)
        assert detail["status"] == "refunded"
        assert detail["workflow_locked"] is True
        assert CANCEL_SECRET not in str(detail)
        assert CANCEL_HASH not in str(detail)

        txs = client.get(
            "/v1/exchange/transactions",
            headers=auth_header(requester_key),
        )
        assert txs.status_code == 200, txs.text
        assert CANCEL_SECRET not in txs.text
        assert CANCEL_HASH not in txs.text

        attestations = client.get(
            f"/v1/exchange/escrow/{escrow_id}/attestations",
            headers=auth_header(requester_key),
        )
        assert attestations.status_code == 200, attestations.text
        assert CANCEL_SECRET not in attestations.text
        assert CANCEL_HASH not in attestations.text

        from exchange.config import get_session
        from exchange.models import FinancialOperation

        session = next(get_session())
        try:
            with session.begin():
                rows = session.execute(select(FinancialOperation)).scalars().all()
                assert rows
                for row in rows:
                    assert CANCEL_SECRET not in (row.response_body or "")
                    assert CANCEL_SECRET not in (row.request_hash or "")
                    assert len(row.request_hash) == 64
        finally:
            session.close()


def test_unlocked_refund_ignores_cancel_secret(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        provider_id, requester_key, _provider_key = _register_pair(client)
        created = _create(client, auth_header, requester_key, provider_id)
        assert created.status_code == 201, created.text
        escrow_id = created.json()["escrow_id"]
        detail = _detail(client, auth_header, requester_key, escrow_id)
        assert detail["workflow_locked"] is False

        refund = client.post(
            "/v1/exchange/refund",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow_id, "cancel_secret": "ignored", "reason": "test"},
        )
        assert refund.status_code == 200, refund.text
        assert refund.json()["status"] == "refunded"
        assert refund.json()["amount_returned"] == 11
        ended = _balance(client, auth_header, requester_key)
        assert ended["available"] == 100
        assert ended["held_in_escrow"] == 0


def test_requester_dispute_blocked_provider_dispute_allowed(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        provider_id, requester_key, provider_key = _register_pair(client)
        locked = _create(
            client,
            auth_header,
            requester_key,
            provider_id,
            workflow_locked=True,
            cancel_secret_hash=CANCEL_HASH,
        ).json()
        before = _balance(client, auth_header, requester_key)
        denied = client.post(
            "/v1/exchange/dispute",
            headers=auth_header(requester_key),
            json={
                "escrow_id": locked["escrow_id"],
                "reason": "want the funds back",
                "stake_amount": STAKE,
            },
        )
        assert denied.status_code == 409, denied.text
        assert denied.json()["detail"] == "escrow is workflow locked"
        after = _balance(client, auth_header, requester_key)
        assert after["available"] == before["available"]
        assert after["held_in_escrow"] == before["held_in_escrow"]
        assert _detail(client, auth_header, requester_key, locked["escrow_id"])["status"] == "held"

        allowed = client.post(
            "/v1/exchange/dispute",
            headers=auth_header(provider_key),
            json={
                "escrow_id": locked["escrow_id"],
                "reason": "delivery disputed",
                "stake_amount": STAKE,
            },
        )
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["status"] == "evidence_pending"


def test_expiry_sweep_skips_workflow_locked(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        provider_id, requester_key, _provider_key = _register_pair(client)
        unlocked = _create(
            client,
            auth_header,
            requester_key,
            provider_id,
            ttl_minutes=5,
        ).json()
        locked = _create(
            client,
            auth_header,
            requester_key,
            provider_id,
            ttl_minutes=5,
            workflow_locked=True,
            cancel_secret_hash=CANCEL_HASH,
        ).json()

        future = datetime.now(timezone.utc) + timedelta(minutes=10)
        from exchange.tasks import run_expiry_sweep

        with patch("exchange.observers._now", return_value=future):
            counts = run_expiry_sweep()

        assert counts["expired_held"] == 1
        assert _detail(client, auth_header, requester_key, unlocked["escrow_id"])["status"] == "expired"
        locked_detail = _detail(client, auth_header, requester_key, locked["escrow_id"])
        assert locked_detail["status"] == "held"
        assert locked_detail["workflow_locked"] is True
        bal = _balance(client, auth_header, requester_key)
        assert bal["held_in_escrow"] == 11
        assert bal["available"] == 89


def test_expiry_warning_skips_workflow_locked(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        provider_id, requester_key, _provider_key = _register_pair(client)
        _create(
            client,
            auth_header,
            requester_key,
            provider_id,
            ttl_minutes=3,
        )
        _create(
            client,
            auth_header,
            requester_key,
            provider_id,
            ttl_minutes=3,
            workflow_locked=True,
            cancel_secret_hash=CANCEL_HASH,
        )
        from exchange.tasks import run_expiry_sweep

        counts = run_expiry_sweep()
        assert counts["expired_held"] == 0
        assert counts["warned"] == 1


def test_requester_can_release_locked_escrow(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        provider_id, requester_key, provider_key = _register_pair(client)
        created = _create(
            client,
            auth_header,
            requester_key,
            provider_id,
            workflow_locked=True,
            cancel_secret_hash=CANCEL_HASH,
        )
        assert created.status_code == 201, created.text
        escrow_id = created.json()["escrow_id"]

        released = client.post(
            "/v1/exchange/release",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow_id},
        )
        assert released.status_code == 200, released.text
        body = released.json()
        assert body["status"] == "released"
        assert body["amount_paid"] == 10
        assert body["fee_collected"] == 1
        provider_bal = _balance(client, auth_header, provider_key)
        assert provider_bal["available"] == 110
        requester_bal = _balance(client, auth_header, requester_key)
        assert requester_bal["held_in_escrow"] == 0


def test_partial_release_blocked_on_locked_escrow(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        provider_id, requester_key, _provider_key = _register_pair(client)
        created = _create(
            client,
            auth_header,
            requester_key,
            provider_id,
            workflow_locked=True,
            cancel_secret_hash=CANCEL_HASH,
        )
        assert created.status_code == 201, created.text
        escrow_id = created.json()["escrow_id"]

        resp = client.post(
            f"/v1/exchange/escrow/{escrow_id}/partial-release",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow_id, "release_percent": 10, "score": 10},
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"] == "escrow is workflow locked"
        assert _detail(client, auth_header, requester_key, escrow_id)["status"] == "held"


def test_auto_refund_skips_workflow_locked_dependent(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        provider_id, requester_key, _provider_key = _register_pair(client)
        upstream = _create(client, auth_header, requester_key, provider_id).json()
        locked_dep = _create(
            client,
            auth_header,
            requester_key,
            provider_id,
            depends_on=[upstream["escrow_id"]],
            workflow_locked=True,
            cancel_secret_hash=CANCEL_HASH,
        ).json()
        unlocked_dep = _create(
            client,
            auth_header,
            requester_key,
            provider_id,
            depends_on=[upstream["escrow_id"]],
        ).json()

        refund = client.post(
            "/v1/exchange/refund",
            headers=auth_header(requester_key),
            json={"escrow_id": upstream["escrow_id"]},
        )
        assert refund.status_code == 200, refund.text
        assert _detail(client, auth_header, requester_key, upstream["escrow_id"])["status"] == "refunded"
        assert _detail(client, auth_header, requester_key, unlocked_dep["escrow_id"])["status"] == "refunded"
        locked_detail = _detail(client, auth_header, requester_key, locked_dep["escrow_id"])
        assert locked_detail["status"] == "held"
        assert locked_detail["workflow_locked"] is True
        assert _balance(client, auth_header, requester_key)["held_in_escrow"] == 11


def test_schema_updates_adds_workflow_lock_columns(tmp_path):
    from exchange.schema_updates import ensure_assurance_schema

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE escrows (id VARCHAR(36) PRIMARY KEY)"))
        conn.execute(text("INSERT INTO escrows (id) VALUES ('escrow-1')"))

    ensure_assurance_schema(engine)
    ensure_assurance_schema(engine)

    names = {col["name"] for col in inspect(engine).get_columns("escrows")}
    assert "workflow_locked" in names
    assert "cancel_secret_hash" in names
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT workflow_locked, cancel_secret_hash FROM escrows")
        ).one()
    assert row.workflow_locked in (0, False)
    assert row.cancel_secret_hash is None
