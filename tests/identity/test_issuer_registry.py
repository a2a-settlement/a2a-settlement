"""Tests for the Trusted Issuer Registry."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from exchange.models import Base
from exchange.identity.issuer_registry import IssuerRegistry, TrustedIssuer


@pytest.fixture()
def db_session(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'test.db'}"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False, autobegin=False)
    session = factory()
    yield session
    session.close()


@pytest.fixture()
def registry():
    return IssuerRegistry()


class TestIssuerRegistry:
    def test_add_and_get(self, db_session: Session, registry: IssuerRegistry):
        with db_session.begin():
            issuer = registry.add_issuer(
                db_session,
                did="did:web:test.example",
                name="Test",
                issuer_type="auditor",
                accepted_claims=["claim-a"],
                added_by="admin",
            )
        assert issuer.did == "did:web:test.example"
        assert issuer.active is True

        with db_session.begin():
            fetched = registry.get_issuer(db_session, "did:web:test.example")
        assert fetched is not None
        assert fetched.name == "Test"

    def test_get_active_dids(self, db_session: Session, registry: IssuerRegistry):
        with db_session.begin():
            registry.add_issuer(db_session, did="did:web:a", name="A", issuer_type="t", accepted_claims=[], added_by="x")
            registry.add_issuer(db_session, did="did:web:b", name="B", issuer_type="t", accepted_claims=[], added_by="x")

        with db_session.begin():
            dids = registry.get_active_dids(db_session)
        assert dids == {"did:web:a", "did:web:b"}

    def test_deactivate(self, db_session: Session, registry: IssuerRegistry):
        with db_session.begin():
            registry.add_issuer(db_session, did="did:web:a", name="A", issuer_type="t", accepted_claims=[], added_by="x")

        with db_session.begin():
            result = registry.deactivate_issuer(db_session, "did:web:a")
        assert result is True

        with db_session.begin():
            dids = registry.get_active_dids(db_session)
        assert "did:web:a" not in dids

    def test_deactivate_nonexistent(self, db_session: Session, registry: IssuerRegistry):
        with db_session.begin():
            assert registry.deactivate_issuer(db_session, "did:web:none") is False

    def test_reactivate(self, db_session: Session, registry: IssuerRegistry):
        with db_session.begin():
            registry.add_issuer(db_session, did="did:web:a", name="A", issuer_type="t", accepted_claims=[], added_by="x")
        with db_session.begin():
            registry.deactivate_issuer(db_session, "did:web:a")
        with db_session.begin():
            result = registry.reactivate_issuer(db_session, "did:web:a")
        assert result is True
        with db_session.begin():
            assert registry.is_trusted(db_session, "did:web:a")

    def test_is_trusted(self, db_session: Session, registry: IssuerRegistry):
        with db_session.begin():
            assert not registry.is_trusted(db_session, "did:web:x")
            registry.add_issuer(db_session, did="did:web:x", name="X", issuer_type="t", accepted_claims=[], added_by="x")
        with db_session.begin():
            assert registry.is_trusted(db_session, "did:web:x")

    def test_seed_initial(self, db_session: Session, registry: IssuerRegistry):
        with db_session.begin():
            registry.seed_initial(db_session)
        with db_session.begin():
            dids = registry.get_active_dids(db_session)
        assert "did:web:exchange.a2a-settlement.org" in dids

    def test_seed_idempotent(self, db_session: Session, registry: IssuerRegistry):
        with db_session.begin():
            registry.seed_initial(db_session)
        with db_session.begin():
            registry.seed_initial(db_session)
        with db_session.begin():
            all_issuers = registry.get_all_active(db_session)
        assert len(all_issuers) == 1

    def test_get_all_active(self, db_session: Session, registry: IssuerRegistry):
        with db_session.begin():
            registry.add_issuer(db_session, did="did:web:a", name="A", issuer_type="t", accepted_claims=[], added_by="x")
            registry.add_issuer(db_session, did="did:web:b", name="B", issuer_type="t", accepted_claims=[], added_by="x")
        with db_session.begin():
            registry.deactivate_issuer(db_session, "did:web:b")
        with db_session.begin():
            active = registry.get_all_active(db_session)
        assert len(active) == 1
        assert active[0].did == "did:web:a"
