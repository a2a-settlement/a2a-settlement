"""Experiment obligation policy, acceptance binding, and root-budget lock.

Workers cannot write these rows. Bootstrap uses an operator account. The root
budget row is locked in the same database transaction as the balance change.
"""

from __future__ import annotations

import hashlib

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from exchange.models import (
    AcceptanceRecord,
    Account,
    Escrow,
    ObligationPolicy,
    RootBudget,
)


def artifact_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _lock(stmt):
    return stmt.with_for_update()


def load_account(session: Session, account_id: str) -> Account:
    account = session.execute(
        _lock(select(Account).where(Account.id == account_id))
    ).scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


def account_is_bound(account: Account) -> bool:
    return bool(account.obligation_policy_id or account.root_budget_id)


def load_policy(session: Session, policy_id: str) -> ObligationPolicy:
    policy = session.get(ObligationPolicy, policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Obligation policy not found")
    return policy


def reject_instant_settle_if_bound(session: Session, account_id: str) -> None:
    with session.begin():
        account = session.get(Account, account_id)
        bound = account is not None and account_is_bound(account)
    if bound:
        raise HTTPException(
            status_code=403,
            detail="Instant settlement is not available for experiment-bound accounts",
        )


def stamp_escrow(
    session: Session,
    *,
    requester_id: str,
    task_id: str | None,
    group_id: str | None,
    acceptance_required: bool | None,
    root_budget_id: str | None,
    criteria_hash: str | None,
    obligation_policy_id: str | None,
    total_hold: int,
) -> dict:
    """Copy the trusted policy onto a new escrow and reserve the root budget.

    Returns kwargs for the Escrow constructor. Unbound accounts are unchanged.
    """
    account = load_account(session, requester_id)
    if not account_is_bound(account):
        if acceptance_required or root_budget_id or obligation_policy_id:
            raise HTTPException(
                status_code=400,
                detail="Unbound accounts cannot set an obligation policy or root budget",
            )
        return {}

    if not account.obligation_policy_id or not account.root_budget_id:
        raise HTTPException(
            status_code=400,
            detail="Bound account is missing an obligation policy or root budget",
        )
    policy = load_policy(session, account.obligation_policy_id)
    if account.root_budget_id != policy.root_budget_id:
        raise HTTPException(
            status_code=400,
            detail="Account root budget does not match the obligation policy",
        )
    if task_id and task_id != policy.task_id:
        raise HTTPException(
            status_code=400,
            detail="Task id does not match the obligation policy",
        )
    if group_id and group_id != policy.root_budget_id:
        raise HTTPException(
            status_code=400,
            detail="Caller group_id does not match the bound root budget",
        )
    if root_budget_id and root_budget_id != policy.root_budget_id:
        raise HTTPException(
            status_code=400,
            detail="Caller root_budget_id does not match the bound root budget",
        )
    if obligation_policy_id and obligation_policy_id != policy.id:
        raise HTTPException(
            status_code=400,
            detail="Caller obligation_policy_id does not match the bound policy",
        )
    if criteria_hash and criteria_hash != policy.criteria_hash:
        raise HTTPException(
            status_code=400,
            detail="Caller criteria_hash does not match the bound policy",
        )
    if acceptance_required is not None and acceptance_required != policy.acceptance_required:
        raise HTTPException(
            status_code=400,
            detail="Caller cannot change acceptance_required on a bound account",
        )

    reserve(session, policy.root_budget_id, total_hold)
    return {
        "obligation_policy_id": policy.id,
        "root_budget_id": policy.root_budget_id,
        "criteria_hash": policy.criteria_hash,
        "acceptance_required": policy.acceptance_required,
        "task_id": policy.task_id,
        "group_id": policy.root_budget_id,
    }


def reserve(session: Session, root_budget_id: str, units: int) -> None:
    """Conditional update so two sibling holds cannot both pass the limit.

    SELECT FOR UPDATE is a no-op on SQLite. The WHERE clause is the lock:
    the database applies at most one increment that still fits.
    """
    loaded = session.get(RootBudget, root_budget_id)
    result = session.execute(
        update(RootBudget)
        .where(RootBudget.id == root_budget_id)
        .where(RootBudget.reserved_units + RootBudget.spent_units + units <= RootBudget.limit_units)
        .values(reserved_units=RootBudget.reserved_units + units)
    )
    if loaded is not None:
        session.expire(loaded)
    if result.rowcount == 1:
        return
    budget = session.execute(
        _lock(select(RootBudget).where(RootBudget.id == root_budget_id))
    ).scalar_one_or_none()
    if budget is None:
        raise HTTPException(status_code=404, detail="Root budget not found")
    raise HTTPException(
        status_code=400,
        detail=(
            f"Root budget exceeded. limit={budget.limit_units} "
            f"reserved={budget.reserved_units} spent={budget.spent_units} "
            f"requested={units}"
        ),
    )


def commit_spend(session: Session, escrow: Escrow, units: int) -> None:
    """Move units from reserved to spent. Does not add them a second time."""
    if not escrow.root_budget_id or units <= 0:
        return
    budget = session.execute(
        _lock(select(RootBudget).where(RootBudget.id == escrow.root_budget_id))
    ).scalar_one()
    budget.reserved_units -= units
    budget.spent_units += units
    if budget.reserved_units < 0:
        raise HTTPException(status_code=409, detail="Root budget reservation underflow")
    session.add(budget)


def release_reservation(session: Session, escrow: Escrow, units: int) -> None:
    if not escrow.root_budget_id or units <= 0:
        return
    budget = session.execute(
        _lock(select(RootBudget).where(RootBudget.id == escrow.root_budget_id))
    ).scalar_one()
    budget.reserved_units -= units
    if budget.reserved_units < 0:
        raise HTTPException(status_code=409, detail="Root budget reservation underflow")
    session.add(budget)


def require_acceptance(session: Session, escrow: Escrow) -> None:
    if not escrow.acceptance_required:
        return
    artifact = escrow.delivered_artifact_sha256
    if not artifact or not escrow.criteria_hash or not escrow.obligation_policy_id:
        raise HTTPException(
            status_code=409,
            detail="Release blocked: no acceptance record bound to the current artifact",
        )
    policy = load_policy(session, escrow.obligation_policy_id)
    record = session.execute(
        select(AcceptanceRecord).where(
            AcceptanceRecord.policy_id == policy.id,
            AcceptanceRecord.task_id == policy.task_id,
            AcceptanceRecord.criteria_hash == escrow.criteria_hash,
            AcceptanceRecord.artifact_sha256 == artifact,
            AcceptanceRecord.approver_id == policy.authorized_approver_id,
        )
    ).scalar_one_or_none()
    if record is None:
        raise HTTPException(
            status_code=409,
            detail="Release blocked: acceptance is not bound to this task, criteria, and artifact",
        )
