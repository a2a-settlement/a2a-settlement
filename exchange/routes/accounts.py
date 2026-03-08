from __future__ import annotations

import secrets
from datetime import datetime, timezone

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from exchange.auth import authenticate_bot
from exchange.config import get_session, settings
from exchange.ratelimit import limiter
from exchange.models import Account, Balance, Transaction
from exchange.ratelimit import check_register_rate_limit
from exchange.schemas import (
    AccountResponse,
    AgentCardResponse,
    DirectoryResponse,
    KYARegisterResponse,
    KYAVerificationDetail,
    RegisterAccountInfo,
    RegisterRequest,
    RegisterResponse,
    RotateKeyResponse,
    SuspendRequest,
    SuspendResponse,
    UpdateSkillsRequest,
    UpdateSkillsResponse,
    VerificationStatusResponse,
)
from exchange.identity.did_resolver import DIDResolver
from exchange.identity.issuer_registry import IssuerRegistry
from exchange.identity.models import AgentCardModel
from exchange.identity.vc_verifier import VCVerifier


_did_resolver: DIDResolver | None = None
_issuer_registry = IssuerRegistry()


def get_vc_verifier(session: Session = Depends(get_session)) -> VCVerifier:
    """Build a VCVerifier using the shared DID resolver and current trusted issuers."""
    global _did_resolver
    if _did_resolver is None:
        _did_resolver = DIDResolver(
            cache_ttl_seconds=settings.kya_did_cache_ttl_seconds,
            http_timeout=settings.kya_did_http_timeout_seconds,
        )
    with session.begin():
        trusted = _issuer_registry.get_active_dids(session)
    return VCVerifier(_did_resolver, trusted)


router = APIRouter()


@router.post(
    "/accounts/register",
    status_code=201,
    response_model=RegisterResponse,
    tags=["Accounts"],
    dependencies=[Depends(check_register_rate_limit)],
)
def register(req: RegisterRequest, session: Session = Depends(get_session)) -> RegisterResponse:
    if settings.invite_code and req.invite_code != settings.invite_code:
        raise HTTPException(status_code=403, detail="Invalid or missing invite code")

    api_key = f"ate_{secrets.token_hex(16)}"
    api_key_hash = bcrypt.hashpw(
        api_key.encode("utf-8"),
        bcrypt.gensalt(rounds=settings.api_key_salt_rounds),
    ).decode("utf-8")

    with session.begin():
        existing = session.execute(select(Account.id).where(Account.bot_name == req.bot_name)).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(status_code=409, detail="A bot with this name already exists")

        spend_limit = req.daily_spend_limit
        if spend_limit is None and settings.default_daily_spend_limit > 0:
            spend_limit = settings.default_daily_spend_limit

        account = Account(
            bot_name=req.bot_name,
            developer_id=req.developer_id,
            developer_name=req.developer_name,
            contact_email=req.contact_email,
            api_key_hash=api_key_hash,
            description=req.description,
            skills=req.skills or [],
            daily_spend_limit=spend_limit,
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
            developer_name=account.developer_name,
            contact_email=account.contact_email,
            description=account.description,
            skills=account.skills,
            status=account.status,
            reputation=float(account.reputation),
            daily_spend_limit=account.daily_spend_limit,
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
                developer_id=b.developer_id,
                developer_name=b.developer_name,
                contact_email=b.contact_email,
                description=b.description,
                skills=b.skills,
                status=b.status,
                reputation=float(b.reputation),
                daily_spend_limit=b.daily_spend_limit,
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
            developer_name=acct.developer_name,
            contact_email=acct.contact_email,
            description=acct.description,
            skills=acct.skills,
            status=acct.status,
            reputation=float(acct.reputation),
            daily_spend_limit=acct.daily_spend_limit,
            created_at=acct.created_at,
        )


@router.put("/accounts/skills", response_model=UpdateSkillsResponse, tags=["Accounts"])
@limiter.limit(settings.rate_limit_authenticated)
def update_skills(
    request: Request,
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
@limiter.limit(settings.rate_limit_authenticated)
def rotate_key(
    request: Request,
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


@router.post("/accounts/admin/suspend", response_model=SuspendResponse, tags=["Accounts"])
@limiter.limit(settings.rate_limit_authenticated)
def suspend_account(
    request: Request,
    req: SuspendRequest,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> SuspendResponse:
    if current.get("status") != "operator":
        raise HTTPException(status_code=403, detail="Only the exchange operator can suspend accounts")

    with session.begin():
        acct = session.execute(select(Account).where(Account.id == req.account_id)).scalar_one_or_none()
        if acct is None:
            raise HTTPException(status_code=404, detail="Account not found")
        if acct.status == "operator":
            raise HTTPException(status_code=400, detail="Cannot suspend an operator account")
        acct.status = "suspended"
        session.add(acct)

    return SuspendResponse(account_id=acct.id, reason=req.reason)


# ---------------------------------------------------------------------------
# KYA Agent Card Registration
# ---------------------------------------------------------------------------


@router.post(
    "/accounts/register-agent",
    status_code=201,
    response_model=KYARegisterResponse,
    tags=["Accounts"],
    dependencies=[Depends(check_register_rate_limit)],
)
def register_agent(
    card_body: dict,
    session: Session = Depends(get_session),
    verifier: VCVerifier = Depends(get_vc_verifier),
) -> KYARegisterResponse:
    """Register an agent with a KYA-enhanced Agent Card.

    Accepts raw JSON so signature verification uses the exact bytes the
    agent signed, avoiding datetime re-serialization mismatches.
    """
    try:
        card = AgentCardModel.model_validate(card_body)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if settings.invite_code:
        raise HTTPException(status_code=403, detail="Invite code required; use /accounts/register for legacy flow")

    api_key = f"ate_{secrets.token_hex(16)}"
    api_key_hash = bcrypt.hashpw(
        api_key.encode("utf-8"),
        bcrypt.gensalt(rounds=settings.api_key_salt_rounds),
    ).decode("utf-8")

    card_dict = card_body

    vr = verifier.verify_agent_card(card_dict)

    if card.kya_level >= 1 and not vr.card_signature_valid:
        raise HTTPException(status_code=401, detail=vr.error_summary or "Card signature verification failed")

    if card.kya_level >= 2 and vr.kya_level_verified < 2:
        pass  # warning only; we store verified level

    att_expires: datetime | None = None
    if card.attestations:
        future_expiries = [a.expires_at for a in card.attestations if a.expires_at > datetime.now(timezone.utc)]
        if future_expiries:
            att_expires = min(future_expiries)

    with session.begin():
        existing = session.execute(select(Account.id).where(Account.bot_name == card.name)).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(status_code=409, detail="A bot with this name already exists")

        if card.kya_level >= 1 and card.id:
            dup_did = session.execute(select(Account.id).where(Account.did == card.id)).scalar_one_or_none()
            if dup_did is not None:
                raise HTTPException(status_code=409, detail="An agent with this DID is already registered")

        spend_limit = settings.default_daily_spend_limit if settings.default_daily_spend_limit > 0 else None

        account = Account(
            bot_name=card.name,
            developer_id=card.id,
            developer_name=card.name,
            contact_email="",
            api_key_hash=api_key_hash,
            description=card.description,
            skills=card.capabilities.skills if card.capabilities else [],
            daily_spend_limit=spend_limit,
            kya_level_verified=vr.kya_level_verified,
            did=card.id if card.kya_level >= 1 else None,
            agent_card_json=card_dict,
            card_verified_at=datetime.now(timezone.utc),
            attestation_expires_at=att_expires,
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
                description="Starter token allocation on KYA registration",
            )
        )

    cred_details = [
        KYAVerificationDetail(
            credential_claim=cr.credential_claim,
            issuer_did=cr.issuer_did,
            status=cr.status.value,
        )
        for cr in vr.credential_results
    ]

    return KYARegisterResponse(
        account=RegisterAccountInfo(
            id=account.id,
            bot_name=account.bot_name,
            developer_id=account.developer_id,
            developer_name=account.developer_name,
            contact_email=account.contact_email,
            description=account.description,
            skills=account.skills,
            status=account.status,
            reputation=float(account.reputation),
            daily_spend_limit=account.daily_spend_limit,
            created_at=account.created_at,
        ),
        api_key=api_key,
        starter_tokens=settings.starter_tokens,
        kya_level_claimed=vr.kya_level_claimed,
        kya_level_verified=vr.kya_level_verified,
        card_signature_valid=vr.card_signature_valid,
        did_resolved=vr.did_resolved,
        credential_results=cred_details,
        error_summary=vr.error_summary,
    )


@router.get("/accounts/{account_id}/card", response_model=AgentCardResponse, tags=["Accounts"])
def get_agent_card(account_id: str, session: Session = Depends(get_session)) -> AgentCardResponse:
    with session.begin():
        acct = session.execute(select(Account).where(Account.id == account_id)).scalar_one_or_none()
        if acct is None:
            raise HTTPException(status_code=404, detail="Account not found")
        if acct.agent_card_json is None:
            raise HTTPException(status_code=404, detail="No Agent Card stored for this account")
        return AgentCardResponse(
            agent_id=acct.id,
            kya_level_verified=acct.kya_level_verified,
            card=acct.agent_card_json,
        )


@router.put("/accounts/{account_id}/card", response_model=AgentCardResponse, tags=["Accounts"])
@limiter.limit(settings.rate_limit_authenticated)
def update_agent_card(
    request: Request,
    account_id: str,
    card_body: dict,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
    verifier: VCVerifier = Depends(get_vc_verifier),
) -> AgentCardResponse:
    if current["id"] != account_id:
        raise HTTPException(status_code=403, detail="Can only update your own Agent Card")

    try:
        card = AgentCardModel.model_validate(card_body)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    card_dict = card_body
    vr = verifier.verify_agent_card(card_dict)

    att_expires: datetime | None = None
    if card.attestations:
        future_expiries = [a.expires_at for a in card.attestations if a.expires_at > datetime.now(timezone.utc)]
        if future_expiries:
            att_expires = min(future_expiries)

    with session.begin():
        acct = session.execute(select(Account).where(Account.id == account_id)).scalar_one_or_none()
        if acct is None:
            raise HTTPException(status_code=404, detail="Account not found")
        acct.agent_card_json = card_dict
        acct.kya_level_verified = vr.kya_level_verified
        acct.card_verified_at = datetime.now(timezone.utc)
        acct.attestation_expires_at = att_expires
        if card.kya_level >= 1:
            acct.did = card.id
        session.add(acct)

    return AgentCardResponse(
        agent_id=acct.id,
        kya_level_verified=acct.kya_level_verified,
        card=acct.agent_card_json,
    )


@router.get(
    "/accounts/{account_id}/verification",
    response_model=VerificationStatusResponse,
    tags=["Accounts"],
)
def get_verification_status(
    account_id: str, session: Session = Depends(get_session)
) -> VerificationStatusResponse:
    with session.begin():
        acct = session.execute(select(Account).where(Account.id == account_id)).scalar_one_or_none()
        if acct is None:
            raise HTTPException(status_code=404, detail="Account not found")
        return VerificationStatusResponse(
            agent_id=acct.id,
            kya_level_verified=acct.kya_level_verified,
            did=acct.did,
            card_verified_at=acct.card_verified_at,
            attestation_expires_at=acct.attestation_expires_at,
        )
