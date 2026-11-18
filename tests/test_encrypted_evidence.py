"""Encrypted evidence is refused at submission time.

Nothing in the ecosystem can decrypt evidence yet, so a submission flagged
``encrypted`` would be withheld by the mediator and count for nothing. The
exchange rejects it up front. The ``encrypted`` column and response field
remain, so rows stored before the check still read back.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


STAKE = 10


def _register(client, name, owner_key=None):
    body = {
        "bot_name": name,
        "developer_id": "dev",
        "developer_name": "Test Dev",
        "contact_email": "test@test.dev",
        "skills": ["sentiment-analysis"],
    }
    if owner_key:
        body["owner_api_key"] = owner_key
    return client.post("/v1/accounts/register", json=body).json()


def _disputed_escrow(client, auth_header):
    """Return (escrow_id, requester_key, provider) with the evidence window open."""
    provider = _register(client, "ProviderBot")
    requester = _register(client, "RequesterBot", owner_key=provider["api_key"])
    escrow = client.post(
        "/v1/exchange/escrow",
        headers=auth_header(requester["api_key"]),
        json={"provider_id": provider["account"]["id"], "amount": 10, "ttl_minutes": 5},
    ).json()
    eid = escrow["escrow_id"]
    resp = client.post(
        "/v1/exchange/dispute",
        headers=auth_header(requester["api_key"]),
        json={"escrow_id": eid, "reason": "bad work", "stake_amount": STAKE},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "evidence_pending"
    return eid, requester["api_key"], provider


def _make_oracle(client, provider_key):
    from sqlalchemy import update

    from exchange.config import SessionLocal
    from exchange.models import Account

    oracle = _register(client, "OracleBot", owner_key=provider_key)
    with SessionLocal() as session, session.begin():
        session.execute(
            update(Account)
            .where(Account.id == oracle["account"]["id"])
            .values(is_oracle=True, reputation=0.9)
        )
    return oracle["api_key"]


def _evidence_rows(escrow_id):
    from sqlalchemy import select

    from exchange.config import SessionLocal
    from exchange.models import EvidenceSubmission

    with SessionLocal() as session, session.begin():
        return (
            session.execute(
                select(EvidenceSubmission).where(
                    EvidenceSubmission.escrow_id == escrow_id
                )
            )
            .scalars()
            .all()
        )


def _assert_rejected(resp):
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"].lower()
    assert "encrypted evidence is not supported" in detail
    assert "in the clear" in detail


@pytest.mark.parametrize(
    "extra",
    [{"encrypted": True}, {"encrypted": True, "encryption_key_id": "key-1"}],
)
def test_party_encrypted_evidence_rejected(exchange_app, auth_header, extra):
    with TestClient(exchange_app) as client:
        eid, requester_key, provider = _disputed_escrow(client, auth_header)
        for key in (requester_key, provider["api_key"]):
            resp = client.post(
                f"/v1/exchange/escrow/{eid}/evidence",
                headers=auth_header(key),
                json={"evidence_type": "service", "summary": "ciphertext", **extra},
            )
            _assert_rejected(resp)
        assert _evidence_rows(eid) == []


def test_oracle_encrypted_evidence_rejected(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        eid, _rk, provider = _disputed_escrow(client, auth_header)
        oracle_key = _make_oracle(client, provider["api_key"])
        resp = client.post(
            f"/v1/exchange/escrow/{eid}/oracle-evidence",
            headers=auth_header(oracle_key),
            json={"evidence_type": "service", "summary": "ciphertext", "encrypted": True},
        )
        _assert_rejected(resp)
        assert _evidence_rows(eid) == []


@pytest.mark.parametrize("extra", [{}, {"encrypted": False}])
def test_unencrypted_evidence_still_accepted(exchange_app, auth_header, extra):
    with TestClient(exchange_app) as client:
        eid, _rk, provider = _disputed_escrow(client, auth_header)
        resp = client.post(
            f"/v1/exchange/escrow/{eid}/evidence",
            headers=auth_header(provider["api_key"]),
            json={"evidence_type": "service", "summary": "delivered as agreed", **extra},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["encrypted"] is False

        oracle_key = _make_oracle(client, provider["api_key"])
        resp = client.post(
            f"/v1/exchange/escrow/{eid}/oracle-evidence",
            headers=auth_header(oracle_key),
            json={"evidence_type": "service", "summary": "observed delivery", **extra},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["encrypted"] is False
        assert resp.json()["source_type"] == "oracle"

        rows = _evidence_rows(eid)
        assert len(rows) == 2
        assert all(r.encrypted is False for r in rows)


def test_existing_encrypted_row_still_reads_back(exchange_app, auth_header):
    """A row stored before the check lists and appears in the compliance bundle."""
    from exchange.config import SessionLocal
    from exchange.models import EvidenceSubmission

    with TestClient(exchange_app) as client:
        eid, requester_key, provider = _disputed_escrow(client, auth_header)
        with SessionLocal() as session, session.begin():
            session.add(
                EvidenceSubmission(
                    escrow_id=eid,
                    submitter_id=provider["account"]["id"],
                    evidence_type="service",
                    summary="legacy ciphertext",
                    artifacts=[],
                    encrypted=True,
                    encryption_key_id="key-1",
                    content_hash="0" * 64,
                )
            )

        listed = client.get(
            f"/v1/exchange/escrow/{eid}/evidence", headers=auth_header(requester_key)
        )
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert body["total"] == 1
        assert body["evidence"][0]["encrypted"] is True
        assert body["evidence"][0]["summary"] == "legacy ciphertext"

        bundle = client.get(
            f"/v1/exchange/escrow/{eid}/compliance-bundle",
            headers=auth_header(requester_key),
        )
        assert bundle.status_code == 200, bundle.text
        evidence = bundle.json()["evidence_submissions"]
        assert len(evidence) == 1
        assert evidence[0]["encrypted"] is True
        assert evidence[0]["content_hash"] == "0" * 64
