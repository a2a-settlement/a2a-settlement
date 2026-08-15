from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


# Match the A2A_EXCHANGE_DISPUTE_STAKE_MIN and A2A_EXCHANGE_EVIDENCE_WINDOW_HOURS
# defaults.
STAKE = 10
EVIDENCE_WINDOW_MINUTES = 72 * 60


def _setup_escrow(client, auth_header):
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

    provider_id = provider["account"]["id"]
    requester_key = requester["api_key"]
    provider_key = provider["api_key"]

    escrow = client.post(
        "/v1/exchange/escrow",
        headers=auth_header(requester_key),
        json={"provider_id": provider_id, "amount": 10, "ttl_minutes": 5},
    ).json()

    return escrow, requester_key, provider_key, provider_id


def test_held_escrow_expires_after_ttl(exchange_app, auth_header):
    """Held escrows past their TTL are expired and refunded by the observer."""
    with TestClient(exchange_app) as client:
        escrow, requester_key, _pk, _pid = _setup_escrow(client, auth_header)

        future = datetime.now(timezone.utc) + timedelta(minutes=10)
        with patch("exchange.observers._now", return_value=future):
            from exchange.tasks import run_expiry_sweep
            counts = run_expiry_sweep()

        assert counts["expired_held"] == 1

        detail = client.get(
            f"/v1/exchange/escrows/{escrow['escrow_id']}",
            headers=auth_header(requester_key),
        ).json()
        assert detail["status"] == "expired"

        bal = client.get("/v1/exchange/balance", headers=auth_header(requester_key)).json()
        assert bal["held_in_escrow"] == 0
        assert bal["available"] == 100


def test_held_escrow_not_expired_before_ttl(exchange_app, auth_header):
    """Held escrows before their TTL should not be expired."""
    with TestClient(exchange_app) as client:
        escrow, requester_key, _pk, _pid = _setup_escrow(client, auth_header)

        future = datetime.now(timezone.utc) + timedelta(minutes=2)
        with patch("exchange.observers._now", return_value=future):
            from exchange.tasks import run_expiry_sweep
            counts = run_expiry_sweep()

        assert counts["expired_held"] == 0

        detail = client.get(
            f"/v1/exchange/escrows/{escrow['escrow_id']}",
            headers=auth_header(requester_key),
        ).json()
        assert detail["status"] == "held"


def _file_dispute(client, auth_header, key, escrow_id):
    return client.post(
        "/v1/exchange/dispute",
        headers=auth_header(key),
        json={"escrow_id": escrow_id, "reason": "bad work", "stake_amount": STAKE},
    )


def _status(client, auth_header, key, escrow_id):
    return client.get(
        f"/v1/exchange/escrows/{escrow_id}", headers=auth_header(key)
    ).json()["status"]


def test_disputed_escrow_expires_after_dispute_ttl(exchange_app, auth_header):
    """A dispute reaches 'disputed' via the evidence window, then expires on TTL."""
    with TestClient(exchange_app) as client:
        escrow, requester_key, provider_key, _pid = _setup_escrow(client, auth_header)
        eid = escrow["escrow_id"]

        _file_dispute(client, auth_header, requester_key, eid)
        assert _status(client, auth_header, requester_key, eid) == "evidence_pending"

        # The respondent must answer, or the closing window applies a default
        # judgment instead of sending the dispute to mediation.
        evidence = client.post(
            f"/v1/exchange/escrow/{eid}/evidence",
            headers=auth_header(provider_key),
            json={"evidence_type": "service", "summary": "work was delivered as agreed"},
        )
        assert evidence.status_code == 200, evidence.text

        # Past the evidence window and the dispute TTL that follows it.
        future = datetime.now(timezone.utc) + timedelta(minutes=EVIDENCE_WINDOW_MINUTES + 90)
        from exchange.tasks import run_expiry_sweep

        with patch("exchange.observers._now", return_value=future):
            # expire_stale_disputes runs before expire_evidence_windows in a single
            # sweep, so promotion and expiry cannot happen in the same pass.
            first = run_expiry_sweep()
            assert first["expired_disputes"] == 0
            assert first["defaulted_evidence"] == 0
            assert _status(client, auth_header, requester_key, eid) == "disputed"

            second = run_expiry_sweep()
            assert second["expired_disputes"] == 1

        assert _status(client, auth_header, requester_key, eid) == "expired"


@pytest.mark.xfail(
    reason="Dispute stake is stranded in held_in_escrow on expiry; see issue #9",
    strict=False,
)
def test_expired_dispute_returns_the_stake(exchange_app, auth_header):
    """Expiry found no fault, so the filer's stake should come back."""
    with TestClient(exchange_app) as client:
        escrow, requester_key, provider_key, _pid = _setup_escrow(client, auth_header)
        eid = escrow["escrow_id"]

        _file_dispute(client, auth_header, requester_key, eid)
        client.post(
            f"/v1/exchange/escrow/{eid}/evidence",
            headers=auth_header(provider_key),
            json={"evidence_type": "service", "summary": "work was delivered as agreed"},
        )

        future = datetime.now(timezone.utc) + timedelta(minutes=EVIDENCE_WINDOW_MINUTES + 90)
        from exchange.tasks import run_expiry_sweep

        with patch("exchange.observers._now", return_value=future):
            run_expiry_sweep()
            run_expiry_sweep()

        bal = client.get("/v1/exchange/balance", headers=auth_header(requester_key)).json()
        assert bal["held_in_escrow"] == 0
        assert bal["available"] == 100


def test_disputed_escrow_not_expired_before_dispute_ttl(exchange_app, auth_header):
    """A dispute inside its evidence window is not swept."""
    with TestClient(exchange_app) as client:
        escrow, requester_key, _pk, _pid = _setup_escrow(client, auth_header)
        eid = escrow["escrow_id"]

        _file_dispute(client, auth_header, requester_key, eid)

        future = datetime.now(timezone.utc) + timedelta(minutes=30)
        with patch("exchange.observers._now", return_value=future):
            from exchange.tasks import run_expiry_sweep
            counts = run_expiry_sweep()

        assert counts["expired_disputes"] == 0
        assert _status(client, auth_header, requester_key, eid) == "evidence_pending"


def test_expiring_soon_warning(exchange_app, auth_header):
    """Escrows approaching their deadline get warned (warning_sent_at stamped)."""
    with TestClient(exchange_app) as client:
        escrow, requester_key, _pk, _pid = _setup_escrow(client, auth_header)

        within_warning = datetime.now(timezone.utc) + timedelta(minutes=3)
        with patch("exchange.observers._now", return_value=within_warning):
            from exchange.tasks import run_expiry_sweep
            counts = run_expiry_sweep()

        assert counts["warned"] == 1
        assert counts["expired_held"] == 0

        detail = client.get(
            f"/v1/exchange/escrows/{escrow['escrow_id']}",
            headers=auth_header(requester_key),
        ).json()
        assert detail["status"] == "held"


def test_warning_not_sent_twice(exchange_app, auth_header):
    """Once a warning is sent, it should not be sent again on the next sweep."""
    with TestClient(exchange_app) as client:
        escrow, requester_key, _pk, _pid = _setup_escrow(client, auth_header)

        within_warning = datetime.now(timezone.utc) + timedelta(minutes=3)
        with patch("exchange.observers._now", return_value=within_warning):
            from exchange.tasks import run_expiry_sweep
            counts1 = run_expiry_sweep()

        assert counts1["warned"] == 1

        slightly_later = within_warning + timedelta(seconds=30)
        with patch("exchange.observers._now", return_value=slightly_later):
            counts2 = run_expiry_sweep()

        assert counts2["warned"] == 0
