"""Operator bootstrap and acceptance records for experiment obligations."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from exchange.auth import authenticate_bot
from exchange.config import get_session
from exchange.models import AcceptanceRecord, Account, Balance, Escrow, ObligationPolicy, RootBudget

router = APIRouter(tags=["Settlement"])


class RootBudgetRequest(BaseModel):
    principal_account_id: str
    limit_units: int = Field(..., gt=0)


class RootBudgetResponse(BaseModel):
    root_budget_id: str
    limit_units: int
    reserved_units: int
    spent_units: int


class PolicyRequest(BaseModel):
    principal_account_id: str
    task_id: str
    criteria_hash: str = Field(..., min_length=64, max_length=64)
    authorized_approver_id: str
    root_budget_id: str
    acceptance_required: bool = True


class PolicyResponse(BaseModel):
    policy_id: str
    task_id: str
    criteria_hash: str
    acceptance_required: bool
    root_budget_id: str
    authorized_approver_id: str


class BindAccountRequest(BaseModel):
    account_id: str
    policy_id: str


class AcceptanceRequest(BaseModel):
    policy_id: str
    task_id: str
    criteria_hash: str = Field(..., min_length=64, max_length=64)
    artifact_sha256: str = Field(..., min_length=64, max_length=64)


class AcceptanceResponse(BaseModel):
    acceptance_id: str
    policy_id: str
    task_id: str
    criteria_hash: str
    artifact_sha256: str
    approver_id: str


def _operator(current: dict) -> None:
    if current.get("status") != "operator":
        raise HTTPException(status_code=403, detail="Only the exchange operator can bootstrap obligations")


@router.post("/exchange/assurance/root-budgets", response_model=RootBudgetResponse)
def create_root_budget(
    req: RootBudgetRequest,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> RootBudgetResponse:
    _operator(current)
    with session.begin():
        if session.get(Account, req.principal_account_id) is None:
            raise HTTPException(status_code=404, detail="Principal account not found")
        budget = RootBudget(
            principal_account_id=req.principal_account_id,
            limit_units=req.limit_units,
            reserved_units=0,
            spent_units=0,
        )
        session.add(budget)
        session.flush()
        return RootBudgetResponse(
            root_budget_id=budget.id,
            limit_units=budget.limit_units,
            reserved_units=budget.reserved_units,
            spent_units=budget.spent_units,
        )


@router.post("/exchange/assurance/policies", response_model=PolicyResponse)
def create_policy(
    req: PolicyRequest,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> PolicyResponse:
    _operator(current)
    with session.begin():
        budget = session.get(RootBudget, req.root_budget_id)
        if budget is None:
            raise HTTPException(status_code=404, detail="Root budget not found")
        if session.get(Account, req.authorized_approver_id) is None:
            raise HTTPException(status_code=404, detail="Approver account not found")
        policy = ObligationPolicy(
            principal_account_id=req.principal_account_id,
            task_id=req.task_id,
            criteria_hash=req.criteria_hash,
            authorized_approver_id=req.authorized_approver_id,
            root_budget_id=req.root_budget_id,
            acceptance_required=req.acceptance_required,
        )
        session.add(policy)
        session.flush()
        return PolicyResponse(
            policy_id=policy.id,
            task_id=policy.task_id,
            criteria_hash=policy.criteria_hash,
            acceptance_required=policy.acceptance_required,
            root_budget_id=policy.root_budget_id,
            authorized_approver_id=policy.authorized_approver_id,
        )


@router.post("/exchange/assurance/bind-account")
def bind_account(
    req: BindAccountRequest,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> dict:
    _operator(current)
    with session.begin():
        policy = session.get(ObligationPolicy, req.policy_id)
        if policy is None:
            raise HTTPException(status_code=404, detail="Obligation policy not found")
        account = session.get(Account, req.account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="Account not found")
        account.obligation_policy_id = policy.id
        account.root_budget_id = policy.root_budget_id
        session.add(account)
        root_budget_id = policy.root_budget_id
    return {"account_id": req.account_id, "policy_id": req.policy_id, "root_budget_id": root_budget_id}


@router.post("/exchange/acceptance", response_model=AcceptanceResponse)
def record_acceptance(
    req: AcceptanceRequest,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> AcceptanceResponse:
    with session.begin():
        policy = session.get(ObligationPolicy, req.policy_id)
        if policy is None:
            raise HTTPException(status_code=404, detail="Obligation policy not found")
        if current["id"] != policy.authorized_approver_id:
            raise HTTPException(
                status_code=403,
                detail="Only the policy's authorized approver can record acceptance",
            )
        if req.task_id != policy.task_id or req.criteria_hash != policy.criteria_hash:
            raise HTTPException(
                status_code=400,
                detail="Acceptance task or criteria hash does not match the policy",
            )
        record = AcceptanceRecord(
            policy_id=policy.id,
            task_id=req.task_id,
            criteria_hash=req.criteria_hash,
            artifact_sha256=req.artifact_sha256,
            approver_id=current["id"],
        )
        session.add(record)
        try:
            session.flush()
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Acceptance record already exists") from exc
        return AcceptanceResponse(
            acceptance_id=record.id,
            policy_id=record.policy_id,
            task_id=record.task_id,
            criteria_hash=record.criteria_hash,
            artifact_sha256=record.artifact_sha256,
            approver_id=record.approver_id,
        )


@router.get("/exchange/assurance/escrows/{escrow_id}")
def read_escrow_binding(
    escrow_id: str,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> dict:
    """Read-only binding view. Does not approve or release."""
    del current
    with session.begin():
        escrow = session.get(Escrow, escrow_id)
        if escrow is None:
            raise HTTPException(status_code=404, detail="Escrow not found")
        budget = None
        if escrow.root_budget_id:
            budget = session.get(RootBudget, escrow.root_budget_id)
        policy = None
        if escrow.obligation_policy_id:
            policy = session.get(ObligationPolicy, escrow.obligation_policy_id)
        acceptance = None
        if policy is not None and escrow.delivered_artifact_sha256 and escrow.criteria_hash:
            acceptance = session.execute(
                select(AcceptanceRecord).where(
                    AcceptanceRecord.policy_id == policy.id,
                    AcceptanceRecord.task_id == policy.task_id,
                    AcceptanceRecord.criteria_hash == escrow.criteria_hash,
                    AcceptanceRecord.artifact_sha256 == escrow.delivered_artifact_sha256,
                    AcceptanceRecord.approver_id == policy.authorized_approver_id,
                )
            ).scalar_one_or_none()
        provider_balance = session.get(Balance, escrow.provider_id)
        return {
            "escrow_id": escrow.id,
            "status": escrow.status,
            "task_id": escrow.task_id,
            "amount": int(escrow.amount),
            "fee_amount": int(escrow.fee_amount),
            "acceptance_required": bool(escrow.acceptance_required),
            "criteria_hash": escrow.criteria_hash,
            "delivered_artifact_sha256": escrow.delivered_artifact_sha256,
            "obligation_policy_id": escrow.obligation_policy_id,
            "authorized_approver_id": None if policy is None else policy.authorized_approver_id,
            "root_budget_id": escrow.root_budget_id,
            "reserved_units": None if budget is None else int(budget.reserved_units),
            "spent_units": None if budget is None else int(budget.spent_units),
            "limit_units": None if budget is None else int(budget.limit_units),
            "provider_available": None if provider_balance is None else int(provider_balance.available),
            "acceptance": None
            if acceptance is None
            else {
                "acceptance_id": acceptance.id,
                "approver_id": acceptance.approver_id,
                "criteria_hash": acceptance.criteria_hash,
                "artifact_sha256": acceptance.artifact_sha256,
                "created_at": acceptance.created_at.isoformat(),
            },
        }


@router.get("/exchange/assurance/root-budgets/{root_budget_id}", response_model=RootBudgetResponse)
def read_root_budget(
    root_budget_id: str,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> RootBudgetResponse:
    del current
    with session.begin():
        budget = session.get(RootBudget, root_budget_id)
        if budget is None:
            raise HTTPException(status_code=404, detail="Root budget not found")
        return RootBudgetResponse(
            root_budget_id=budget.id,
            limit_units=int(budget.limit_units),
            reserved_units=int(budget.reserved_units),
            spent_units=int(budget.spent_units),
        )
