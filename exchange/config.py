from __future__ import annotations

import ipaddress
import os
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def _get_int(name: str, default: int) -> int:
    val = os.getenv(name)
    if val is None or val == "":
        return default
    return int(val)


def _get_float(name: str, default: float) -> float:
    val = os.getenv(name)
    if val is None or val == "":
        return default
    return float(val)


def _get_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None or val == "":
        return default
    return val.strip().lower() in {"1", "true", "yes", "y", "on"}


RegisterTrustedRule = (
    ipaddress.IPv4Address
    | ipaddress.IPv6Address
    | ipaddress.IPv4Network
    | ipaddress.IPv6Network
    | str
)


def parse_register_trusted_ip_rules(raw: str) -> list[RegisterTrustedRule]:
    """Parse comma-separated IPs, CIDRs, or exact hostnames for registration rate-limit bypass.

    Hostnames match ``request.client.host`` as-is (useful behind fixed proxies or in tests;
    Starlette's TestClient uses the hostname ``testclient``).
    """
    rules: list[RegisterTrustedRule] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "/" in part:
            rules.append(ipaddress.ip_network(part, strict=False))
            continue
        try:
            rules.append(ipaddress.ip_address(part))
        except ValueError:
            rules.append(part)
    return rules


def client_ip_matches_register_trusted_rules(client_host: str, rules: list[RegisterTrustedRule]) -> bool:
    if not rules:
        return False
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address | None
    try:
        addr = ipaddress.ip_address(client_host)
    except ValueError:
        addr = None
    for rule in rules:
        if isinstance(rule, str):
            if client_host == rule:
                return True
            continue
        if addr is None:
            continue
        if isinstance(rule, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
            if addr in rule:
                return True
        elif addr == rule:
            return True
    return False


class Settings:
    database_url: str = os.getenv("DATABASE_URL") or os.getenv("A2A_EXCHANGE_DATABASE_URL", "sqlite:///./a2a_exchange.db")

    fee_percent: float = _get_float("A2A_EXCHANGE_FEE_PERCENT", 0.25)
    starter_tokens: int = _get_int("A2A_EXCHANGE_STARTER_TOKENS", 100)
    min_escrow: int = _get_int("A2A_EXCHANGE_MIN_ESCROW", 1)
    max_escrow: int = _get_int("A2A_EXCHANGE_MAX_ESCROW", 10_000)
    min_fee: int = _get_int("A2A_EXCHANGE_MIN_FEE", 1)
    default_ttl_minutes: int = _get_int("A2A_EXCHANGE_DEFAULT_TTL_MINUTES", 30)
    default_daily_spend_limit: int = _get_int("A2A_EXCHANGE_DEFAULT_DAILY_SPEND_LIMIT", 0)
    api_key_salt_rounds: int = _get_int("A2A_EXCHANGE_API_KEY_SALT_ROUNDS", 10)

    auto_create_schema: bool = _get_bool("A2A_EXCHANGE_AUTO_CREATE_SCHEMA", True)

    host: str = os.getenv("A2A_EXCHANGE_HOST", "127.0.0.1")
    port: int = _get_int("A2A_EXCHANGE_PORT", 3000)
    workers: int = _get_int("A2A_EXCHANGE_WORKERS", 4)
    worker_timeout: int = _get_int("A2A_EXCHANGE_WORKER_TIMEOUT", 120)

    # Rate limiting
    redis_url: str = os.getenv("A2A_EXCHANGE_REDIS_URL", "")
    rate_limit_authenticated: str = os.getenv("A2A_EXCHANGE_RATE_LIMIT", "60/minute")
    rate_limit_public: str = os.getenv("A2A_EXCHANGE_RATE_LIMIT_PUBLIC", "120/minute")
    # Registration: separate per-IP limits (see docs/self-hosting.md). Defaults favor cold-start / lab NAT;
    # tighten in untrusted public deployments via env.
    register_rate_limit_per_hour: int = _get_int("A2A_EXCHANGE_REGISTER_RATE_LIMIT_HOUR", 30)
    register_rate_limit_per_day: int = _get_int("A2A_EXCHANGE_REGISTER_RATE_LIMIT_DAY", 200)
    register_trusted_ip_rules: list[RegisterTrustedRule] = parse_register_trusted_ip_rules(
        os.getenv("A2A_EXCHANGE_REGISTER_TRUSTED_IPS", "")
    )

    # Invite code (empty = open registration)
    invite_code: str = os.getenv("A2A_EXCHANGE_INVITE_CODE", "")

    # Request signatures
    require_signatures: bool = _get_bool("A2A_EXCHANGE_REQUIRE_SIGNATURES", False)
    signature_max_age_seconds: int = _get_int("A2A_EXCHANGE_SIGNATURE_MAX_AGE", 300)

    # Key rotation grace period
    key_rotation_grace_minutes: int = _get_int("A2A_EXCHANGE_KEY_ROTATION_GRACE_MINUTES", 5)

    # Background expiry
    expiry_interval_seconds: int = _get_int("A2A_EXCHANGE_EXPIRY_INTERVAL_SECONDS", 60)
    dispute_ttl_minutes: int = _get_int("A2A_EXCHANGE_DISPUTE_TTL_MINUTES", 60)
    expiry_warning_minutes: int = _get_int("A2A_EXCHANGE_EXPIRY_WARNING_MINUTES", 5)

    # Spending guard
    spending_window_hours: int = _get_int("A2A_EXCHANGE_SPENDING_WINDOW_HOURS", 24)
    hourly_velocity_limit: int = _get_int("A2A_EXCHANGE_HOURLY_VELOCITY_LIMIT", 0)
    spending_freeze_minutes: int = _get_int("A2A_EXCHANGE_SPENDING_FREEZE_MINUTES", 30)

    # Webhooks
    webhook_timeout_seconds: int = _get_int("A2A_EXCHANGE_WEBHOOK_TIMEOUT", 10)
    webhook_max_retries: int = _get_int("A2A_EXCHANGE_WEBHOOK_MAX_RETRIES", 3)

    # KYA
    kya_enabled: bool = _get_bool("A2A_EXCHANGE_KYA_ENABLED", False)
    kya_did_cache_ttl_seconds: int = _get_int("A2A_EXCHANGE_KYA_DID_CACHE_TTL", 300)
    kya_did_http_timeout_seconds: int = _get_int("A2A_EXCHANGE_KYA_DID_HTTP_TIMEOUT", 10)
    kya_monitor_interval_seconds: int = _get_int("A2A_EXCHANGE_KYA_MONITOR_INTERVAL", 60)
    kya_expiry_warning_days: int = _get_int("A2A_EXCHANGE_KYA_EXPIRY_WARNING_DAYS", 7)
    kya_did_recheck_hours: int = _get_int("A2A_EXCHANGE_KYA_DID_RECHECK_HOURS", 24)
    kya_escrow_tier1_max: int = _get_int("A2A_EXCHANGE_KYA_ESCROW_TIER1_MAX", 100)
    kya_escrow_tier2_max: int = _get_int("A2A_EXCHANGE_KYA_ESCROW_TIER2_MAX", 10_000)
    kya_hitl_threshold: int = _get_int("A2A_EXCHANGE_KYA_HITL_THRESHOLD", 10_000)
    kya_operator_did: str = os.getenv("A2A_EXCHANGE_KYA_OPERATOR_DID", "did:web:exchange.a2a-settlement.org")
    kya_operator_private_key_path: str = os.getenv("A2A_EXCHANGE_KYA_OPERATOR_KEY_PATH", "")

    # Operator API key — if set, a bootstrap operator account is created at startup
    operator_api_key: str = os.getenv("A2A_EXCHANGE_OPERATOR_API_KEY", "")

    # Dashboard operator key for admin endpoints
    dashboard_api_key: str = os.getenv("A2A_EXCHANGE_DASHBOARD_API_KEY", "")

    # Settlement auth middleware (a2a-settlement-auth integration)
    settlement_auth_enabled: bool = _get_bool("A2A_EXCHANGE_SETTLEMENT_AUTH_ENABLED", False)
    settlement_auth_key: str = os.getenv("A2A_EXCHANGE_SETTLEMENT_AUTH_KEY", "")
    settlement_auth_issuer: str = os.getenv("A2A_EXCHANGE_SETTLEMENT_AUTH_ISSUER", "")
    settlement_auth_audience: str = os.getenv(
        "A2A_EXCHANGE_SETTLEMENT_AUTH_AUDIENCE", "https://exchange.a2a-settlement.org"
    )

    # Evidence API
    evidence_window_hours: int = _get_int("A2A_EXCHANGE_EVIDENCE_WINDOW_HOURS", 72)
    dispute_stake_min: int = _get_int("A2A_EXCHANGE_DISPUTE_STAKE_MIN", 10)
    max_inline_evidence_bytes: int = _get_int(
        "A2A_EXCHANGE_MAX_INLINE_EVIDENCE_BYTES", 5 * 1024 * 1024
    )

    # Instant settlement
    instant_settle_min_reputation: float = _get_float("A2A_EXCHANGE_INSTANT_SETTLE_MIN_REPUTATION", 0.65)
    instant_settle_max_amount: int = _get_int("A2A_EXCHANGE_INSTANT_SETTLE_MAX_AMOUNT", 1_000)

    # Oracle evidence
    oracle_min_reputation: float = _get_float("A2A_EXCHANGE_ORACLE_MIN_REPUTATION", 0.6)

    # Compliance audit (Merkle tree)
    compliance_enabled: bool = _get_bool("A2A_EXCHANGE_COMPLIANCE_ENABLED", False)
    compliance_db_path: str = os.getenv("A2A_EXCHANGE_COMPLIANCE_DB_PATH", "compliance_merkle.db")

    # Anti-self-dealing: diversity sweep
    diversity_sweep_interval_seconds: int = _get_int(
        "A2A_EXCHANGE_DIVERSITY_SWEEP_INTERVAL_SECONDS", 86400  # 24h
    )
    payment_graph_hops: int = _get_int("A2A_EXCHANGE_PAYMENT_GRAPH_HOPS", 2)

    # Federation
    federation_enabled: bool = _get_bool("A2A_EXCHANGE_FEDERATION_ENABLED", True)
    federation_node_did: str = os.getenv("A2A_EXCHANGE_FEDERATION_NODE_DID", "")
    federation_escrow_signing_secret: str = os.getenv("A2A_EXCHANGE_FEDERATION_ESCROW_SECRET", "")
    base_url: str = os.getenv("A2A_EXCHANGE_BASE_URL", "")
    exchange_name: str = os.getenv("A2A_EXCHANGE_NAME", "A2A Settlement Exchange")
    exchange_operator: str = os.getenv("A2A_EXCHANGE_OPERATOR", "")
    exchange_id: str = os.getenv("A2A_EXCHANGE_ID", "a2a-se-default")

    # Attestation TTL (global maximums — instances can configure stricter values)
    attestation_ttl_identity_days: int = _get_int("A2A_EXCHANGE_ATTESTATION_TTL_IDENTITY_DAYS", 365)
    attestation_ttl_reputation_days: int = _get_int("A2A_EXCHANGE_ATTESTATION_TTL_REPUTATION_DAYS", 90)
    attestation_ttl_capability_days: int = _get_int("A2A_EXCHANGE_ATTESTATION_TTL_CAPABILITY_DAYS", 180)
    attestation_ttl_warning_percent: int = _get_int("A2A_EXCHANGE_ATTESTATION_TTL_WARNING_PCT", 80)
    attestation_grace_period_hours: int = _get_int("A2A_EXCHANGE_ATTESTATION_GRACE_HOURS", 72)
    attestation_renewal_fee: int = _get_int("A2A_EXCHANGE_ATTESTATION_RENEWAL_FEE", 1)


settings = Settings()


def _connect_args(database_url: str) -> dict:
    if database_url.startswith("sqlite:"):
        return {"check_same_thread": False}
    return {}


engine = create_engine(
    settings.database_url,
    future=True,
    pool_pre_ping=True,
    connect_args=_connect_args(settings.database_url),
)

SessionLocal = sessionmaker(
    bind=engine,
    class_=Session,
    expire_on_commit=False,
    autoflush=False,
    autobegin=False,
)


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
