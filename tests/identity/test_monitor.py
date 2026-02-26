"""Tests for the KYA background monitor."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from exchange.identity.did_resolver import DIDResolver
from exchange.identity.monitor import KYAMonitor
from exchange.models import Account, Balance, Base


@pytest.fixture()
def db_session(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'mon.db'}"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False, autobegin=False)
    session = factory()
    yield session
    session.close()


def _make_agent(session: Session, *, agent_id: str, kya_level: int, did: str | None = None,
                attestation_expires_at: datetime | None = None,
                card_verified_at: datetime | None = None) -> Account:
    acct = Account(
        id=agent_id,
        bot_name=f"bot-{agent_id}",
        developer_id="dev",
        developer_name="Dev",
        contact_email="x@x.com",
        api_key_hash="hash",
        kya_level_verified=kya_level,
        did=did,
        attestation_expires_at=attestation_expires_at,
        card_verified_at=card_verified_at,
    )
    session.add(acct)
    session.add(Balance(account_id=agent_id))
    session.flush()
    return acct


class TestCheckExpiringAttestations:
    def test_warns_within_window(self, db_session):
        now = datetime.now(timezone.utc)
        with db_session.begin():
            _make_agent(
                db_session, agent_id="a1", kya_level=2, did="did:web:a",
                attestation_expires_at=now + timedelta(days=3),
            )
        monitor = KYAMonitor(expiry_warning_days=7)
        with db_session.begin():
            warned = monitor.check_expiring_attestations(db_session)
        assert "a1" in warned

    def test_no_warn_outside_window(self, db_session):
        now = datetime.now(timezone.utc)
        with db_session.begin():
            _make_agent(
                db_session, agent_id="a2", kya_level=2, did="did:web:b",
                attestation_expires_at=now + timedelta(days=30),
            )
        monitor = KYAMonitor(expiry_warning_days=7)
        with db_session.begin():
            warned = monitor.check_expiring_attestations(db_session)
        assert warned == []


class TestCheckExpiredAttestations:
    def test_downgrades(self, db_session):
        now = datetime.now(timezone.utc)
        with db_session.begin():
            _make_agent(
                db_session, agent_id="a3", kya_level=2, did="did:web:c",
                attestation_expires_at=now - timedelta(hours=1),
            )
        monitor = KYAMonitor()
        with db_session.begin():
            downgraded = monitor.check_expired_attestations(db_session)
        assert "a3" in downgraded
        with db_session.begin():
            acct = db_session.execute(select(Account).where(Account.id == "a3")).scalar_one()
            assert acct.kya_level_verified == 1
            assert acct.attestation_expires_at is None

    def test_no_downgrade_if_not_expired(self, db_session):
        now = datetime.now(timezone.utc)
        with db_session.begin():
            _make_agent(
                db_session, agent_id="a4", kya_level=2, did="did:web:d",
                attestation_expires_at=now + timedelta(days=30),
            )
        monitor = KYAMonitor()
        with db_session.begin():
            downgraded = monitor.check_expired_attestations(db_session)
        assert downgraded == []


class TestRecheckIdentities:
    def _mock_resolver(self, fail_dids: set[str] | None = None):
        fail_dids = fail_dids or set()

        def _get(url, **kw):
            for did in fail_dids:
                expected = DIDResolver.did_to_url(did)
                if url == expected:
                    return httpx.Response(404, json={}, request=httpx.Request("GET", url))
            return httpx.Response(
                200,
                json={"id": "did:web:test", "verificationMethod": []},
                request=httpx.Request("GET", url),
            )

        client = MagicMock(spec=httpx.Client)
        client.get.side_effect = _get
        return DIDResolver(http_client=client)

    def test_recheck_resolves(self, db_session):
        now = datetime.now(timezone.utc)
        with db_session.begin():
            _make_agent(
                db_session, agent_id="a5", kya_level=1, did="did:web:ok.example",
                card_verified_at=now - timedelta(hours=48),
            )
        resolver = self._mock_resolver()
        monitor = KYAMonitor(did_resolver=resolver, did_recheck_interval_hours=24)
        with db_session.begin():
            unresolvable = monitor.recheck_agent_identities(db_session)
        assert unresolvable == []

    def test_recheck_fails(self, db_session):
        now = datetime.now(timezone.utc)
        with db_session.begin():
            _make_agent(
                db_session, agent_id="a6", kya_level=1, did="did:web:fail.example",
                card_verified_at=now - timedelta(hours=48),
            )
        resolver = self._mock_resolver(fail_dids={"did:web:fail.example"})
        monitor = KYAMonitor(did_resolver=resolver, did_recheck_interval_hours=24)
        with db_session.begin():
            unresolvable = monitor.recheck_agent_identities(db_session)
        assert "a6" in unresolvable
