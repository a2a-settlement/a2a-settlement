from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from exchange.config import settings
from exchange.models import Account, DeveloperIdOwner


def assert_controls_developer_id(
    session: Session, caller_account_id: str, developer_id: str
) -> None:
    """Single enforcement point for developer_id control (claimed owner only)."""
    row = session.execute(
        select(DeveloperIdOwner).where(DeveloperIdOwner.developer_id == developer_id)
    ).scalar_one_or_none()
    if (
        row is None
        or row.status != "claimed"
        or row.owner_account_id != caller_account_id
    ):
        raise HTTPException(
            status_code=403,
            detail="Not authorized to manage accounts for this developer_id",
        )


def _check_api_key(api_key: str, api_key_hash: str) -> bool:
    try:
        return bcrypt.checkpw(api_key.encode("utf-8"), api_key_hash.encode("utf-8"))
    except Exception:
        return False


def assert_registration_allowed(
    session: Session, developer_id: str, owner_api_key: str | None
) -> None:
    """Block new registrations under a claimed developer_id without the owner key."""
    row = session.execute(
        select(DeveloperIdOwner).where(DeveloperIdOwner.developer_id == developer_id)
    ).scalar_one_or_none()
    if row is None or row.status != "claimed" or not row.owner_account_id:
        return
    if not owner_api_key or not owner_api_key.startswith("ate_"):
        raise HTTPException(
            status_code=403,
            detail=(
                "This developer ID is claimed. Pass owner_api_key with the "
                "controlling account's API key to register under it."
            ),
        )
    owner = session.execute(
        select(Account).where(Account.id == row.owner_account_id)
    ).scalar_one_or_none()
    if owner is None or not _check_api_key(owner_api_key, owner.api_key_hash):
        raise HTTPException(
            status_code=403,
            detail="owner_api_key does not match the developer ID owner",
        )


def grandfather_legacy_open(session: Session) -> int:
    """Insert legacy_open rows for every distinct accounts.developer_id lacking a row."""
    existing = {
        r
        for r in session.execute(select(DeveloperIdOwner.developer_id)).scalars().all()
    }
    ids = session.execute(select(Account.developer_id).distinct()).scalars().all()
    added = 0
    for developer_id in ids:
        if not developer_id or developer_id in existing:
            continue
        session.add(
            DeveloperIdOwner(developer_id=developer_id, status="legacy_open")
        )
        added += 1
    if added:
        session.flush()
    return added


def claim_developer_id_for(
    session: Session, caller: Account, developer_id: str
) -> DeveloperIdOwner:
    row = session.execute(
        select(DeveloperIdOwner).where(DeveloperIdOwner.developer_id == developer_id)
    ).scalar_one_or_none()

    if row is None:
        row = DeveloperIdOwner(
            developer_id=developer_id,
            owner_account_id=caller.id,
            status="claimed",
            claimed_at=datetime.now(timezone.utc),
        )
        session.add(row)
        session.flush()
        return row

    if row.status == "legacy_open":
        row.status = "claimed"
        row.owner_account_id = caller.id
        row.claimed_at = datetime.now(timezone.utc)
        session.add(row)
        session.flush()
        return row

    if row.status == "claimed":
        if row.owner_account_id == caller.id:
            return row
        raise HTTPException(
            status_code=409,
            detail="developer_id already claimed by another account",
        )

    raise HTTPException(status_code=500, detail="Invalid developer_id ownership status")


def claim_developer_id(session: Session, caller: Account) -> DeveloperIdOwner:
    return claim_developer_id_for(session, caller, caller.developer_id)


def try_claim_own_developer_id(
    session: Session, caller: Account
) -> DeveloperIdOwner | None:
    """Auto-claim caller.developer_id if unclaimed, legacy_open, or owned by caller.

    Does not raise 409 when caller.developer_id is already claimed by another account,
    allowing GET /accounts/managed to list any other developer_ids owned by caller.
    """
    row = session.execute(
        select(DeveloperIdOwner).where(
            DeveloperIdOwner.developer_id == caller.developer_id
        )
    ).scalar_one_or_none()
    if (
        row is not None
        and row.status == "claimed"
        and row.owner_account_id != caller.id
    ):
        return None
    return claim_developer_id_for(session, caller, caller.developer_id)


def attach_developer_id_by_key(
    session: Session, caller: Account, agent_api_key: str
) -> DeveloperIdOwner:
    """Prove control of an agent via its API key and claim its developer_id for caller."""
    if (
        not agent_api_key
        or not isinstance(agent_api_key, str)
        or not agent_api_key.startswith("ate_")
    ):
        raise HTTPException(status_code=401, detail="Invalid agent API key")

    accounts = (
        session.execute(
            select(Account).where(
                Account.status.in_(("active", "suspended", "operator"))
            )
        )
        .scalars()
        .all()
    )
    now = datetime.now(timezone.utc)
    grace = timedelta(minutes=settings.key_rotation_grace_minutes)

    matched: Account | None = None
    for acct in accounts:
        if _check_api_key(agent_api_key, acct.api_key_hash):
            matched = acct
            break
        if acct.previous_api_key_hash and acct.key_rotated_at:
            rotated_at = (
                acct.key_rotated_at
                if acct.key_rotated_at.tzinfo is not None
                else acct.key_rotated_at.replace(tzinfo=timezone.utc)
            )
            if (now - rotated_at) < grace and _check_api_key(
                agent_api_key, acct.previous_api_key_hash
            ):
                matched = acct
                break

    if matched is None:
        raise HTTPException(status_code=401, detail="Invalid agent API key")

    return claim_developer_id_for(session, caller, matched.developer_id)


def detach_developer_id(
    session: Session, caller: Account, developer_id: str
) -> DeveloperIdOwner:
    """Release a claimed non-primary developer_id back to legacy_open."""
    assert_controls_developer_id(session, caller.id, developer_id)
    if developer_id == caller.developer_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot detach your primary developer ID",
        )
    row = session.execute(
        select(DeveloperIdOwner).where(DeveloperIdOwner.developer_id == developer_id)
    ).scalar_one()
    row.status = "legacy_open"
    row.owner_account_id = None
    row.claimed_at = None
    session.add(row)
    session.flush()
    return row


def ensure_legacy_open(session: Session, developer_id: str) -> None:
    row = session.execute(
        select(DeveloperIdOwner).where(DeveloperIdOwner.developer_id == developer_id)
    ).scalar_one_or_none()
    if row is None:
        session.add(
            DeveloperIdOwner(
                developer_id=developer_id,
                status="legacy_open",
            )
        )
        session.flush()