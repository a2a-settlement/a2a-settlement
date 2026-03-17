"""SQLAlchemy models for federation state."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    Boolean,
    JSON,
)
from sqlalchemy.orm import DeclarativeBase

from exchange.models import Base


class FederationPeer(Base):
    """A federated exchange that has completed the peering handshake."""

    __tablename__ = "federation_peers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    peer_did = Column(String(512), unique=True, nullable=False, index=True)
    name = Column(String(256), nullable=False)
    operator = Column(String(256), nullable=True)

    # Peering metadata
    peering_id = Column(String(256), unique=True, nullable=True)
    peered_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    status = Column(
        String(32), default="active", nullable=False
    )  # active, suspended, terminated

    # Capability manifest (stored as JSON)
    capability_manifest = Column(JSON, nullable=True)

    # Trust Discount
    trust_discount_policy = Column(JSON, nullable=True)
    current_rho = Column(Float, default=0.15, nullable=False)
    rho_updated_at = Column(DateTime(timezone=True), nullable=True)

    # Health monitoring
    health_status = Column(String(32), default="unknown", nullable=True)
    last_health_check = Column(DateTime(timezone=True), nullable=True)
    consecutive_health_failures = Column(Integer, default=0, nullable=False)
    uptime_90d = Column(Float, nullable=True)
    avg_attestation_latency_ms = Column(Integer, nullable=True)

    # Telemetry (for Trust Discount calculation)
    federation_age_days = Column(Integer, default=0, nullable=False)
    cross_exchange_volume_ate = Column(Float, default=0.0, nullable=False)
    cross_exchange_tx_count = Column(Integer, default=0, nullable=False)
    attestation_success_rate = Column(Float, default=1.0, nullable=False)


class FederatedAttestation(Base):
    """An imported Verifiable Credential from a federation peer."""

    __tablename__ = "federated_attestations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vc_id = Column(String(512), unique=True, nullable=False, index=True)
    agent_did = Column(String(512), nullable=False, index=True)
    source_exchange_did = Column(String(512), nullable=False, index=True)

    # VC type and content
    attestation_type = Column(String(64), nullable=False)
    credential_data = Column(JSON, nullable=False)

    # Reputation scoring
    native_reputation = Column(Float, nullable=True)
    trust_discount_rho = Column(Float, nullable=True)
    effective_reputation = Column(Float, nullable=True)

    # Metadata
    imported_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_until = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
