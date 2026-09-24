"""Idempotent column adds for volumes created before the assurance tables.

``create_all`` creates missing tables and does not alter existing ones.
"""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

_ACCOUNT_COLUMNS = {
    "obligation_policy_id": "VARCHAR(36)",
    "root_budget_id": "VARCHAR(36)",
}
_ESCROW_COLUMNS = {
    "obligation_policy_id": "VARCHAR(36)",
    "root_budget_id": "VARCHAR(36)",
    "criteria_hash": "VARCHAR(64)",
    "acceptance_required": "BOOLEAN NOT NULL DEFAULT FALSE",
    "delivered_artifact_sha256": "VARCHAR(64)",
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


def ensure_assurance_schema(engine: Engine) -> None:
    _add_missing(engine, "accounts", _ACCOUNT_COLUMNS)
    _add_missing(engine, "escrows", _ESCROW_COLUMNS)
