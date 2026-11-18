"""Idempotent column adds for volumes created before the assurance tables.

``create_all`` creates missing tables and does not alter existing ones.
"""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

_ACCOUNT_COLUMNS = {
    "obligation_policy_id": "VARCHAR(36)",
    "root_budget_id": "VARCHAR(36)",
    "is_test": "BOOLEAN NOT NULL DEFAULT FALSE",
    "tempo_address": "VARCHAR(42)",
    "tempo_linked_at": "TIMESTAMP WITH TIME ZONE",
}
_ESCROW_COLUMNS = {
    "obligation_policy_id": "VARCHAR(36)",
    "root_budget_id": "VARCHAR(36)",
    "criteria_hash": "VARCHAR(64)",
    "acceptance_required": "BOOLEAN NOT NULL DEFAULT FALSE",
    "delivered_artifact_sha256": "VARCHAR(64)",
    "workflow_locked": "BOOLEAN NOT NULL DEFAULT FALSE",
    "cancel_secret_hash": "VARCHAR(64)",
}


def _add_missing(engine: Engine, table: str, columns: dict[str, str]) -> None:
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return
    present = {col["name"] for col in insp.get_columns(table)}
    with engine.begin() as conn:
        for name, ddl in columns.items():
            if name in present:
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def _ensure_unique_tempo_address(engine: Engine) -> None:
    """A wallet may be linked to one account; enforce it on existing volumes."""
    if "accounts" not in inspect(engine).get_table_names():
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_accounts_tempo_address "
                "ON accounts (tempo_address)"
            )
        )


def ensure_assurance_schema(engine: Engine) -> None:
    _add_missing(engine, "accounts", _ACCOUNT_COLUMNS)
    _ensure_unique_tempo_address(engine)
    _add_missing(engine, "escrows", _ESCROW_COLUMNS)
    from sqlalchemy.orm import sessionmaker

    from exchange.models import Base

    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as session:
        with session.begin():
            from exchange.ownership import grandfather_legacy_open

            grandfather_legacy_open(session)
