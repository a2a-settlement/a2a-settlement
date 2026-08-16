"""Shadow-mode trust tier labels on escrow create."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select


def _reg(client, name: str) -> dict:
    return client.post(
        "/v1/accounts/register",
        json={
            "bot_name": name,
            "developer_id": "dev",
            "developer_name": "Dev",
            "contact_email": "a@b.com",
        },
    ).json()


def test_classify_trust_state_thresholds(exchange_app):
    # exchange_app fixture reloads config against sqlite before exchange imports work.
    from exchange.reputation_metrics import ReputationMetrics
    from exchange.trust_tiers import STATE_PROVEN, STATE_REGISTERED, classify_trust_state

    now = datetime.now(timezone.utc)
    registered = ReputationMetrics(
        score=0.5,
        task_count=3,
        dispute_rate=0.0,
        settlement_volume=0,
        window_days=90,
        window_start=now,
        issued_at=now,
    )
    assert classify_trust_state(registered) == STATE_REGISTERED

    proven = ReputationMetrics(
        score=0.7,
        task_count=10,
        dispute_rate=0.1,
        settlement_volume=100,
        window_days=90,
        window_start=now,
        issued_at=now,
    )
    assert classify_trust_state(proven) == STATE_PROVEN

    high_dispute = ReputationMetrics(
        score=0.9,
        task_count=20,
        dispute_rate=0.25,
        settlement_volume=100,
        window_days=90,
        window_start=now,
        issued_at=now,
    )
    assert classify_trust_state(high_dispute) == STATE_REGISTERED


def test_escrow_records_registered_shadow_labels(exchange_app, auth_header):
    from exchange.config import get_session
    from exchange.models import Escrow
    from exchange.trust_tiers import STATE_REGISTERED

    with TestClient(exchange_app) as client:
        provider = _reg(client, "ProvShadow")
        requester = _reg(client, "ReqShadow")
        resp = client.post(
            "/v1/exchange/escrow",
            headers=auth_header(requester["api_key"]),
            json={"provider_id": provider["account"]["id"], "amount": 5},
        )
        assert resp.status_code == 201, resp.text
        escrow_id = resp.json()["escrow_id"]

        session = next(get_session())
        try:
            with session.begin():
                esc = session.execute(
                    select(Escrow).where(Escrow.id == escrow_id)
                ).scalar_one()
                assert esc.trust_tier_shadow is True
                assert esc.requester_trust_state == STATE_REGISTERED
                assert esc.provider_trust_state == STATE_REGISTERED
                assert esc.requester_task_count == 0
                assert esc.requester_dispute_rate == 0.0
        finally:
            session.close()


def test_escrow_shadow_never_blocks_large_amount(exchange_app, auth_header):
    # Shadow mode must never 403 based on trust state (only amount/KYA gates apply).
    with TestClient(exchange_app) as client:
        provider = _reg(client, "ProvBig")
        requester = _reg(client, "ReqBig")
        resp = client.post(
            "/v1/exchange/escrow",
            headers=auth_header(requester["api_key"]),
            json={"provider_id": provider["account"]["id"], "amount": 50},
        )
        assert resp.status_code == 201, resp.text


def test_proven_label_after_settled_history(exchange_app, auth_header):
    from exchange.config import get_session
    from exchange.models import Account, Escrow
    from exchange.trust_tiers import STATE_PROVEN, STATE_REGISTERED

    with TestClient(exchange_app) as client:
        provider = _reg(client, "ProvProven")
        requester = _reg(client, "ReqProven")
        provider_id = provider["account"]["id"]
        requester_id = requester["account"]["id"]

        session = next(get_session())
        try:
            with session.begin():
                acct = session.execute(
                    select(Account).where(Account.id == provider_id)
                ).scalar_one()
                acct.reputation = 0.75
                session.add(acct)
                now = datetime.now(timezone.utc)
                for _ in range(10):
                    session.add(
                        Escrow(
                            requester_id=requester_id,
                            provider_id=provider_id,
                            amount=5,
                            fee_amount=0,
                            status="released",
                            expires_at=now + timedelta(days=1),
                            created_at=now - timedelta(days=1),
                            resolved_at=now,
                        )
                    )
        finally:
            session.close()

        other = _reg(client, "ReqOther")
        resp = client.post(
            "/v1/exchange/escrow",
            headers=auth_header(other["api_key"]),
            json={"provider_id": provider_id, "amount": 5},
        )
        assert resp.status_code == 201, resp.text
        escrow_id = resp.json()["escrow_id"]

        session = next(get_session())
        try:
            with session.begin():
                esc = session.execute(
                    select(Escrow).where(Escrow.id == escrow_id)
                ).scalar_one()
                assert esc.provider_trust_state == STATE_PROVEN
                assert esc.requester_trust_state == STATE_REGISTERED
        finally:
            session.close()


def test_batch_escrow_sets_trust_columns(exchange_app, auth_header):
    from exchange.config import get_session
    from exchange.models import Escrow
    from exchange.trust_tiers import STATE_REGISTERED

    with TestClient(exchange_app) as client:
        provider = _reg(client, "ProvBatch")
        requester = _reg(client, "ReqBatch")
        resp = client.post(
            "/v1/exchange/escrow/batch",
            headers=auth_header(requester["api_key"]),
            json={
                "escrows": [
                    {"provider_id": provider["account"]["id"], "amount": 5},
                    {"provider_id": provider["account"]["id"], "amount": 5},
                ]
            },
        )
        assert resp.status_code == 201, resp.text
        ids = [e["escrow_id"] for e in resp.json()["escrows"]]

        session = next(get_session())
        try:
            with session.begin():
                for eid in ids:
                    esc = session.execute(
                        select(Escrow).where(Escrow.id == eid)
                    ).scalar_one()
                    assert esc.requester_trust_state == STATE_REGISTERED
                    assert esc.trust_tier_shadow is True
        finally:
            session.close()
