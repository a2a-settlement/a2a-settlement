"""Trusted Issuer Registry backed by SQLAlchemy.

Stores the set of DIDs that the exchange accepts as credential issuers for
KYA Level 2 verification.  Provides CRUD operations and an in-memory set
export for :class:`VCVerifier`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, String, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from exchange.models import Base


class TrustedIssuer(Base):
    __tablename__ = "trusted_issuers"
    __table_args__ = {"extend_existing": True}

    did: Mapped[str] = mapped_column(String(500), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    issuer_type: Mapped[str] = mapped_column(String(50), nullable=False)
    accepted_claims: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    added_by: Mapped[str] = mapped_column(String(255), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)


INITIAL_TRUSTED_ISSUERS = [
    {
        "did": "did:web:exchange.a2a-settlement.org",
        "name": "A2A-SE Exchange (Self)",
        "issuer_type": "exchange",
        "accepted_claims": ["KYA-Level-2-Verified"],
        "added_by": "system",
    },
]


class IssuerRegistry:
    """CRUD interface over the ``trusted_issuers`` table."""

    def get_all_active(self, session: Session) -> list[TrustedIssuer]:
        return list(
            session.execute(
                select(TrustedIssuer).where(TrustedIssuer.active.is_(True))
            ).scalars().all()
        )

    def get_active_dids(self, session: Session) -> set[str]:
        rows = session.execute(
            select(TrustedIssuer.did).where(TrustedIssuer.active.is_(True))
        ).scalars().all()
        return set(rows)

    def add_issuer(
        self,
        session: Session,
        *,
        did: str,
        name: str,
        issuer_type: str,
        accepted_claims: list[str],
        added_by: str,
        notes: str | None = None,
    ) -> TrustedIssuer:
        issuer = TrustedIssuer(
            did=did,
            name=name,
            issuer_type=issuer_type,
            accepted_claims=accepted_claims,
            added_by=added_by,
            notes=notes,
        )
        session.add(issuer)
        session.flush()
        return issuer

    def deactivate_issuer(self, session: Session, did: str) -> bool:
        issuer = session.execute(
            select(TrustedIssuer).where(TrustedIssuer.did == did)
        ).scalar_one_or_none()
        if issuer is None:
            return False
        issuer.active = False
        session.add(issuer)
        session.flush()
        return True

    def reactivate_issuer(self, session: Session, did: str) -> bool:
        issuer = session.execute(
            select(TrustedIssuer).where(TrustedIssuer.did == did)
        ).scalar_one_or_none()
        if issuer is None:
            return False
        issuer.active = True
        session.add(issuer)
        session.flush()
        return True

    def is_trusted(self, session: Session, did: str) -> bool:
        row = session.execute(
            select(TrustedIssuer).where(
                TrustedIssuer.did == did, TrustedIssuer.active.is_(True)
            )
        ).scalar_one_or_none()
        return row is not None

    def get_issuer(self, session: Session, did: str) -> TrustedIssuer | None:
        return session.execute(
            select(TrustedIssuer).where(TrustedIssuer.did == did)
        ).scalar_one_or_none()

    def seed_initial(self, session: Session) -> None:
        """Insert seed issuers if the table is empty."""
        count = session.execute(select(func.count()).select_from(TrustedIssuer)).scalar_one()
        if count > 0:
            return
        for data in INITIAL_TRUSTED_ISSUERS:
            session.add(TrustedIssuer(**data))
        session.flush()
