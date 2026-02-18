from __future__ import annotations

import secrets

import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from exchange.auth import authenticate_bot
from exchange.config import get_session, settings
from exchange.models import Account, Balance, Transaction
from exchange.schemas import (
    AccountResponse,
    DirectoryResponse,
    RegisterAccountInfo,
    RegisterRequest,
    RegisterResponse,
    RotateKeyResponse,
    UpdateSkillsRequest,
    UpdateSkillsResponse,
)


router = APIRouter()


@router.post("/accounts/register", status_code=201, response_model=RegisterResponse, tags=["Accounts"])
def register(req: RegisterRequest, session: Session = Depends(get_session)) -> RegisterResponse:
    api_key = f"ate_{secrets.token_hex(16)}"
    api_key_hash = bcrypt.hashpw(
        api_key.encode("utf-8"),
        bcrypt.gensalt(rounds=settings.api_key_salt_rounds),
    ).decode("utf-8")

    with session.begin():
        existing = session.execute(select(Account.id).where(Account.bot_name == req.bot_name)).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(status_code=409, detail="A bot with this name already exists")

        account = Account(
            bot_name=req.bot_name,
            developer_id=req.developer_id,
            api_key_hash=api_key_hash,
            description=req.description,
            skills=req.skills or [],
        )
        session.add(account)
        session.flush()

        session.add(Balance(account_id=account.id, available=settings.starter_tokens))
        session.add(
            Transaction(
                from_account=None,
                to_account=account.id,
                amount=settings.starter_tokens,
                tx_type="mint",
                description="Starter token allocation on registration",
            )
        )

    return RegisterResponse(
        account=RegisterAccountInfo(
            id=account.id,
            bot_name=account.bot_name,
            developer_id=account.developer_id,
            description=account.description,
            skills=account.skills,
            status=account.status,
            reputation=float(account.reputation),
            created_at=account.created_at,
        ),
        api_key=api_key,
        starter_tokens=settings.starter_tokens,
    )


@router.get("/accounts/directory", response_model=DirectoryResponse, tags=["Accounts"])
def directory(
    skill: str | None = None,
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_session),
) -> DirectoryResponse:
    with session.begin():
        q = (
            select(Account)
            .where(Account.status == "active")
            .order_by(Account.reputation.desc())
            .limit(limit)
            .offset(offset)
        )
        bots = session.execute(q).scalars().all()

    if skill:
        bots = [b for b in bots if isinstance(b.skills, list) and skill in b.skills]

    return DirectoryResponse(
        bots=[
            AccountResponse(
                id=b.id,
                bot_name=b.bot_name,
                description=b.description,
                skills=b.skills,
                status=b.status,
                reputation=float(b.reputation),
                created_at=b.created_at,
            )
            for b in bots
        ],
        count=len(bots),
    )


@router.get("/accounts/{account_id}", response_model=AccountResponse, tags=["Accounts"])
def get_account(account_id: str, session: Session = Depends(get_session)) -> AccountResponse:
    with session.begin():
        acct = session.execute(select(Account).where(Account.id == account_id)).scalar_one_or_none()
        if acct is None:
            raise HTTPException(status_code=404, detail="Account not found")
        return AccountResponse(
            id=acct.id,
            bot_name=acct.bot_name,
            developer_id=acct.developer_id,
            description=acct.description,
            skills=acct.skills,
            status=acct.status,
            reputation=float(acct.reputation),
            created_at=acct.created_at,
        )


@router.put("/accounts/skills", response_model=UpdateSkillsResponse, tags=["Accounts"])
def update_skills(
    req: UpdateSkillsRequest,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> UpdateSkillsResponse:
    with session.begin():
        acct = session.execute(select(Account).where(Account.id == current["id"])).scalar_one_or_none()
        if acct is None:
            raise HTTPException(status_code=404, detail="Account not found")
        acct.skills = req.skills
        session.add(acct)
    return UpdateSkillsResponse(account_id=acct.id, skills=acct.skills)


@router.post("/accounts/rotate-key", response_model=RotateKeyResponse, tags=["Accounts"])
def rotate_key(
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> RotateKeyResponse:
    new_key = f"ate_{secrets.token_hex(16)}"
    new_hash = bcrypt.hashpw(
        new_key.encode("utf-8"),
        bcrypt.gensalt(rounds=settings.api_key_salt_rounds),
    ).decode("utf-8")

    with session.begin():
        acct = session.execute(select(Account).where(Account.id == current["id"])).scalar_one_or_none()
        if acct is None:
            raise HTTPException(status_code=404, detail="Account not found")
        acct.previous_api_key_hash = acct.api_key_hash
        acct.key_rotated_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        acct.api_key_hash = new_hash
        session.add(acct)

    return RotateKeyResponse(
        api_key=new_key,
        grace_period_minutes=settings.key_rotation_grace_minutes,
    )
