from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Float,
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Principal(Base):
    """Represents a real-world controlling entity (human or org) behind one or more agents.

    Multiple agents can share a principal; the principal resolver collapses those
    identities for anti-self-dealing checks without exposing the mapping publicly.
    """

    __tablename__ = "principals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    principal_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unknown"
    )
    kya_level: Mapped[str] = mapped_column(
        String(20), nullable=False, default="none"
    )
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "principal_type IN ('human', 'org', 'unknown')",
            name="ck_principal_type",
        ),
        CheckConstraint(
            "kya_level IN ('none', 'basic', 'attested', 'verified')",
            name="ck_principal_kya_level",
        ),
    )


class AgentPrincipalLink(Base):
    """Associates an agent account with a principal identity.

    An agent may link to multiple principals with varying confidence scores.
    The highest-confidence link drives enforcement; lower-confidence links
    feed analytics and the nightly payment-graph batch.
    """

    __tablename__ = "agent_principal_links"

    agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounts.id"), primary_key=True, nullable=False
    )
    principal_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("principals.id"), primary_key=True, nullable=False
    )
    link_source: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    established_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "link_source IN ('registration', 'attestation', 'payment_graph', 'behavioral_cluster', 'manual')",
            name="ck_link_source",
        ),
        CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="ck_link_confidence"),
        Index("idx_apl_principal", "principal_id"),
    )


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    bot_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    developer_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    developer_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    contact_email: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    api_key_hash: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    previous_api_key_hash: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    key_rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    skills: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", index=True
    )
    reputation: Mapped[float] = mapped_column(nullable=False, default=0.5)
    daily_spend_limit: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, default=None
    )
    frozen_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Counterparty diversity metrics — updated nightly by background_diversity_loop()
    unique_counterparties_90d: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    counterparty_hhi: Mapped[float | None] = mapped_column(Float, nullable=True)
    diversity_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Oracle role — set by operator via /accounts/admin/register-oracle
    is_oracle: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # KYA identity fields
    kya_level_verified: Mapped[int] = mapped_column(nullable=False, default=0)
    did: Mapped[str | None] = mapped_column(
        String(500), nullable=True, unique=True, index=True
    )
    did_key: Mapped[str | None] = mapped_column(
        String(500), nullable=True, unique=True, index=True,
        comment="Agent's self-sovereign did:key identity (Ed25519)"
    )
    agent_card_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    card_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attestation_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    balance: Mapped["Balance"] = relationship(
        back_populates="account", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'suspended', 'operator')", name="ck_account_status"
        ),
    )


class Balance(Base):
    __tablename__ = "balances"

    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounts.id"), primary_key=True
    )
    available: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    held_in_escrow: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_earned: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_spent: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    account: Mapped[Account] = relationship(back_populates="balance")


class Escrow(Base):
    __tablename__ = "escrows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    requester_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounts.id"), nullable=False, index=True
    )
    provider_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounts.id"), nullable=False, index=True
    )
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fee_amount: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    task_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    task_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    group_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    depends_on: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    deliverables: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="held", index=True
    )
    dispute_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_strategy: Mapped[str | None] = mapped_column(String(100), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    dispute_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    warning_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Anti-self-dealing classification — set at creation, never updated
    # 'arms_length' | 'suspected_self_dealing' | 'self_dealing'
    self_dealing_class: Mapped[str | None] = mapped_column(
        String(30), nullable=True, index=True
    )

    # Provenance attestation fields
    required_attestation_level: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )
    delivered_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    provenance: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    provenance_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Partial release / holdback fields
    released_amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    released_fee: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    holdback_amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    holdback_fee: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    score: Mapped[int | None] = mapped_column(nullable=True)
    efficacy_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    efficacy_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Verifiable Intent (VI) credential chain
    vi_credential_chain: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Dispute evidence fields
    dispute_filed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    dispute_stake_amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    dispute_stake_status: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )
    evidence_window_closes_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    mediator_context: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # KYA escrow fields
    requester_did: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider_did: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Designated Escrow (cross-exchange federation)
    is_federated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    designated_exchange_did: Mapped[str | None] = mapped_column(
        String(512), nullable=True
    )
    remote_peer_did: Mapped[str | None] = mapped_column(String(512), nullable=True)
    remote_agent_did: Mapped[str | None] = mapped_column(String(512), nullable=True)
    kya_level_at_creation: Mapped[int | None] = mapped_column(nullable=True)
    hitl_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    hitl_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    hitl_approval_vc: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        Index(
            "uq_active_task_escrow",
            "requester_id",
            "provider_id",
            "task_id",
            unique=True,
            postgresql_where=text("task_id IS NOT NULL AND status = 'held'"),
            sqlite_where=text("task_id IS NOT NULL AND status = 'held'"),
        ),
    )


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    escrow_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("escrows.id"), nullable=True, index=True
    )
    from_account: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("accounts.id"), nullable=True, index=True
    )
    to_account: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("accounts.id"), nullable=True, index=True
    )
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tx_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    self_dealing_class: Mapped[str | None] = mapped_column(
        String(30), nullable=True, index=True
    )
    fee_class: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WebhookConfig(Base):
    __tablename__ = "webhook_configs"

    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounts.id"), primary_key=True
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    secret: Mapped[str] = mapped_column(String(255), nullable=False)
    events: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_body: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class Attestation(Base):
    __tablename__ = "attestations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounts.id"), nullable=False, index=True
    )
    attestation_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", index=True
    )

    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revocation_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)

    parent_attestation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("attestations.id"), nullable=True
    )
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)

    warning_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "attestation_type IN ('identity', 'reputation', 'transaction', 'capability')",
            name="ck_attestation_type",
        ),
        CheckConstraint(
            "status IN ('active', 'expired', 'revoked', 'renewed')",
            name="ck_attestation_status",
        ),
    )


class EvidenceSubmission(Base):
    __tablename__ = "evidence_submissions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    escrow_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("escrows.id"), nullable=False, index=True
    )
    submitter_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounts.id"), nullable=False, index=True
    )
    evidence_type: Mapped[str] = mapped_column(String(50), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    artifacts: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    encrypted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    encryption_key_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    attestor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    attestor_signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "party" = requester/provider self-reported; "oracle" = registered oracle
    source_type: Mapped[str] = mapped_column(String(20), nullable=False, default="party")
    oracle_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
