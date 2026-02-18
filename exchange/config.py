from __future__ import annotations

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


class Settings:
    database_url: str = os.getenv("DATABASE_URL") or os.getenv("A2A_EXCHANGE_DATABASE_URL", "sqlite:///./a2a_exchange.db")

    fee_percent: float = _get_float("A2A_EXCHANGE_FEE_PERCENT", 3.0)
    starter_tokens: int = _get_int("A2A_EXCHANGE_STARTER_TOKENS", 100)
    min_escrow: int = _get_int("A2A_EXCHANGE_MIN_ESCROW", 1)
    max_escrow: int = _get_int("A2A_EXCHANGE_MAX_ESCROW", 10_000)
    default_ttl_minutes: int = _get_int("A2A_EXCHANGE_DEFAULT_TTL_MINUTES", 30)
    api_key_salt_rounds: int = _get_int("A2A_EXCHANGE_API_KEY_SALT_ROUNDS", 10)

    auto_create_schema: bool = _get_bool("A2A_EXCHANGE_AUTO_CREATE_SCHEMA", True)

    host: str = os.getenv("A2A_EXCHANGE_HOST", "127.0.0.1")
    port: int = _get_int("A2A_EXCHANGE_PORT", 3000)

    # Rate limiting
    rate_limit_authenticated: str = os.getenv("A2A_EXCHANGE_RATE_LIMIT", "60/minute")
    rate_limit_public: str = os.getenv("A2A_EXCHANGE_RATE_LIMIT_PUBLIC", "120/minute")

    # Key rotation grace period
    key_rotation_grace_minutes: int = _get_int("A2A_EXCHANGE_KEY_ROTATION_GRACE_MINUTES", 5)

    # Webhooks
    webhook_timeout_seconds: int = _get_int("A2A_EXCHANGE_WEBHOOK_TIMEOUT", 10)
    webhook_max_retries: int = _get_int("A2A_EXCHANGE_WEBHOOK_MAX_RETRIES", 3)


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
