from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from exchange.config import settings
from exchange.models import Account, DeveloperIdAuditEvent, DeveloperIdOwner


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


LEGACY_OPEN_CLAIM_DENIED = (
    "This developer ID is unclaimed. Only the API key of the oldest agent "
    "registered under it can claim or attach it."
)
LEGACY_OPEN_REGISTRATION_DENIED = (
    "This developer ID already has agents. Pass owner_api_key with the API key "
    "of an agent registered under it to register another agent."
)


def _check_api_key(api_key: str, api_key_hash: str) -> bool:
    try:
        return bcrypt.checkpw(api_key.encode("utf-8"), api_key_hash.encode("utf-8"))
    except Exception:
        return False


def _key_matches(acct: Account, api_key: str, now: datetime) -> bool:
    """Current key, or the previous key inside the rotation grace window."""
    if _check_api_key(api_key, acct.api_key_hash):
        return True
    if acct.previous_api_key_hash and acct.key_rotated_at:
        rotated_at = (
            acct.key_rotated_at
            if acct.key_rotated_at.tzinfo is not None
            else acct.key_rotated_at.replace(tzinfo=timezone.utc)
        )
        grace = timedelta(minutes=settings.key_rotation_grace_minutes)
        if (now - rotated_at) < grace and _check_api_key(
            api_key, acct.previous_api_key_hash
        ):
            return True
    return False


def oldest_account_for(session: Session, developer_id: str) -> Account | None:
    """Earliest-created account in developer_id; ties broken by account id."""
    return (
        session.execute(
            select(Account)
            .where(Account.developer_id == developer_id)
            .order_by(Account.created_at.asc(), Account.id.asc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def _assert_legacy_open_member_key(
    session: Session, developer_id: str, owner_api_key: str | None
) -> None:
    members = (
        session.execute(select(Account).where(Account.developer_id == developer_id))
        .scalars()
        .all()
    )
    if not members:
        return
    if not owner_api_key or not owner_api_key.startswith("ate_"):
        raise HTTPException(status_code=403, detail=LEGACY_OPEN_REGISTRATION_DENIED)
    now = datetime.now(timezone.utc)
    if not any(_key_matches(acct, owner_api_key, now) for acct in members):
        raise HTTPException(status_code=403, detail=LEGACY_OPEN_REGISTRATION_DENIED)


def assert_registration_allowed(
    session: Session, developer_id: str, owner_api_key: str | None
) -> None:
    """Gate new registrations under an existing developer_id.

    claimed: owner_api_key must be the claimed owner's key.
    legacy_open with agents: owner_api_key must belong to an agent in the ID.
    No row, or legacy_open with no agents: open.
    """
    row = session.execute(
        select(DeveloperIdOwner).where(DeveloperIdOwner.developer_id == developer_id)
    ).scalar_one_or_none()
    if row is None:
        return
    if row.status == "legacy_open":
        _assert_legacy_open_member_key(session, developer_id, owner_api_key)
        return
    if row.status != "claimed" or not row.owner_account_id:
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
    session: Session,
    caller: Account,
    developer_id: str,
    prover: Account | None = None,
) -> DeveloperIdOwner:
    """Claim developer_id for caller.

    ``prover`` is the account whose API key was presented (defaults to caller).
    A legacy_open ID with agents can only be claimed when prover is the oldest
    agent in that ID.
    """
    prover = prover or caller
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
        oldest = oldest_account_for(session, developer_id)
        if oldest is not None and oldest.id != prover.id:
            raise HTTPException(status_code=403, detail=LEGACY_OPEN_CLAIM_DENIED)
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

    Does not raise when caller.developer_id is claimed by another account, or is
    legacy_open and caller is not its oldest agent, allowing GET /accounts/managed
    to list any other developer_ids owned by caller.
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
    if row is not None and row.status == "legacy_open":
        oldest = oldest_account_for(session, caller.developer_id)
        if oldest is not None and oldest.id != caller.id:
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
    matched = next(
        (acct for acct in accounts if _key_matches(acct, agent_api_key, now)), None
    )
    if matched is None:
        raise HTTPException(status_code=401, detail="Invalid agent API key")

    existing = session.execute(
        select(DeveloperIdOwner).where(
            DeveloperIdOwner.developer_id == matched.developer_id
        )
    ).scalar_one_or_none()
    if (
        existing is not None
        and existing.status == "claimed"
        and existing.owner_account_id != caller.id
    ):
        raise HTTPException(
            status_code=403,
            detail="developer_id already claimed by another account",
        )

    return claim_developer_id_for(
        session, caller, matched.developer_id, prover=matched
    )


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


def admin_bind_developer_id(
    session: Session,
    actor: Account,
    developer_id: str,
    owner_account_id: str,
    reason: str,
) -> tuple[DeveloperIdOwner, DeveloperIdAuditEvent]:
    """Operator override: bind developer_id to owner_account_id regardless of status.

    Writes exactly one audit event. Rejects a no-op rebind to the same owner with 409.
    """
    reason = reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail="reason is required")
    target = session.execute(
        select(Account).where(Account.id == owner_account_id)
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Target owner account not found")

    row = session.execute(
        select(DeveloperIdOwner).where(DeveloperIdOwner.developer_id == developer_id)
    ).scalar_one_or_none()
    previous_status = row.status if row is not None else None
    previous_owner = row.owner_account_id if row is not None else None
    if previous_status == "claimed" and previous_owner == target.id:
        raise HTTPException(
            status_code=409,
            detail="developer_id is already bound to this owner",
        )

    now = datetime.now(timezone.utc)
    if row is None:
        row = DeveloperIdOwner(developer_id=developer_id, status="claimed")
    row.status = "claimed"
    row.owner_account_id = target.id
    row.claimed_at = now
    session.add(row)

    event = DeveloperIdAuditEvent(
        developer_id=developer_id,
        action="admin_bind",
        actor_account_id=actor.id,
        previous_status=previous_status,
        previous_owner_account_id=previous_owner,
        new_status="claimed",
        new_owner_account_id=target.id,
        reason=reason,
        created_at=now,
    )
    session.add(event)
    session.flush()
    return row, event


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