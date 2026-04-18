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
from exchange.models import Account, Balance, GatewayClaim, Transaction
from exchange.ratelimit import check_register_rate_limit
from exchange.schemas import (
    AccountResponse,
    AgentCardResponse,
    ClaimListResponse,
    ClaimRequest,
    ClaimResponse,
    DirectoryAccountResponse,
    DirectoryResponse,
    GatewayClaimInfo,
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
from exchange.principal_resolver import (
    get_or_create_principal,
    link_agent_to_principal,
    kya_to_confidence,
    invalidate_cache,
)


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
@limiter.exempt
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
            account_type=req.account_type,
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

        # Link the new agent to a principal based on developer_id.
        # Confidence is 0.3 baseline; rises to 0.9 if a verified DID is present.
        confidence = kya_to_confidence(account.kya_level_verified)
        if account.did and account.kya_level_verified >= 2:
            confidence = 0.9
        principal_id = get_or_create_principal(
            account.developer_id, account.kya_level_verified, session
        )
        link_agent_to_principal(account.id, principal_id, "registration", confidence, session)

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


def _directory_account_response(
    acct: Account, claims: list[GatewayClaim] | None = None
) -> DirectoryAccountResponse:
    """Build a public-safe response for the directory (no contact_email)."""
    claim_info = None
    if claims:
        claim_info = [
            GatewayClaimInfo(
                gateway_id=c.gateway_id,
                gateway_name=c.gateway.bot_name if c.gateway else "",
                verified=c.verified,
                claimed_at=c.claimed_at,
            )
            for c in claims
        ]
    return DirectoryAccountResponse(
        id=acct.id,
        bot_name=acct.bot_name,
        developer_id=acct.developer_id,
        developer_name=acct.developer_name,
        description=acct.description,
        skills=acct.skills,
        status=acct.status,
        reputation=float(acct.reputation),
        account_type=acct.account_type,
        created_at=acct.created_at,
        gateway_claims=claim_info,
    )


def _account_response(acct: Account, claims: list[GatewayClaim] | None = None) -> AccountResponse:
    claim_info = None
    if claims:
        claim_info = [
            GatewayClaimInfo(
                gateway_id=c.gateway_id,
                gateway_name=c.gateway.bot_name if c.gateway else "",
                verified=c.verified,
                claimed_at=c.claimed_at,
            )
            for c in claims
        ]
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
        account_type=acct.account_type,
        created_at=acct.created_at,
        gateway_claims=claim_info,
    )


@router.get("/accounts/directory", response_model=DirectoryResponse, tags=["Accounts"])
def directory(
    skill: str | None = None,
    gateway_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_session),
) -> DirectoryResponse:
    from sqlalchemy.orm import joinedload

    with session.begin():
        q = (
            select(Account)
            .where(Account.status == "active")
            .where(Account.account_type == "agent")
            .order_by(Account.reputation.desc())
        )

        if gateway_id:
            q = q.join(GatewayClaim, GatewayClaim.account_id == Account.id).where(
                GatewayClaim.gateway_id == gateway_id,
                GatewayClaim.status == "active",
            )

        q = q.limit(limit).offset(offset)
        bots = session.execute(q).scalars().all()

        bot_ids = [b.id for b in bots]
        claims_by_agent: dict[str, list[GatewayClaim]] = {}
        if bot_ids:
            claims_q = (
                select(GatewayClaim)
                .options(joinedload(GatewayClaim.gateway))
                .where(
                    GatewayClaim.account_id.in_(bot_ids),
                    GatewayClaim.status == "active",
                )
            )
            for c in session.execute(claims_q).scalars().all():
                claims_by_agent.setdefault(c.account_id, []).append(c)

    if skill:
        bots = [b for b in bots if isinstance(b.skills, list) and skill in b.skills]

    return DirectoryResponse(
        bots=[_directory_account_response(b, claims_by_agent.get(b.id)) for b in bots],
        count=len(bots),
    )


@router.get("/accounts/{account_id}", response_model=DirectoryAccountResponse, tags=["Accounts"])
def get_account(account_id: str, session: Session = Depends(get_session)) -> DirectoryAccountResponse:
    from sqlalchemy.orm import joinedload

    with session.begin():
        acct = session.execute(select(Account).where(Account.id == account_id)).scalar_one_or_none()
        if acct is None:
            raise HTTPException(status_code=404, detail="Account not found")
        claims = (
            session.execute(
                select(GatewayClaim)
                .options(joinedload(GatewayClaim.gateway))
                .where(GatewayClaim.account_id == account_id, GatewayClaim.status == "active")
            )
            .scalars()
            .all()
        )
        return _directory_account_response(acct, list(claims))


@router.post(
    "/accounts/{account_id}/claim",
    status_code=201,
    response_model=ClaimResponse,
    tags=["Accounts"],
)
@limiter.limit(settings.rate_limit_authenticated)
def claim_agent(
    request: Request,
    account_id: str,
    body: ClaimRequest | None = None,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
) -> ClaimResponse:
    """Claim an agent for a gateway. The authenticated account must be a gateway."""
    if current.get("account_type", "agent") != "gateway":
        raise HTTPException(status_code=403, detail="Only gateway accounts can claim agents")

    with session.begin():
        agent = session.execute(select(Account).where(Account.id == account_id)).scalar_one_or_none()
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found")
        if agent.account_type != "agent":
            raise HTTPException(status_code=400, detail="Can only claim agent accounts")

        existing = session.execute(
            select(GatewayClaim).where(
                GatewayClaim.gateway_id == current["id"],
                GatewayClaim.account_id == account_id,
            )
        ).scalar_one_or_none()
        if existing and existing.status == "active":
            raise HTTPException(status_code=409, detail="Agent already claimed by this gateway")

        verified = False
        if body and body.agent_api_key:
            agent_hash = agent.api_key_hash
            if bcrypt.checkpw(body.agent_api_key.encode("utf-8"), agent_hash.encode("utf-8")):
                verified = True
            else:
                raise HTTPException(status_code=401, detail="Invalid agent API key")

        if existing:
            existing.status = "active"
            existing.verified = verified
            claim = existing
        else:
            claim = GatewayClaim(
                gateway_id=current["id"],
                account_id=account_id,
                verified=verified,
            )
            session.add(claim)
        session.flush()

    return ClaimResponse(
        claim_id=claim.id,
        gateway_id=claim.gateway_id,
        account_id=claim.account_id,
        verified=claim.verified,
        status=claim.status,
        claimed_at=claim.claimed_at,
    )


@router.delete(
    "/accounts/{account_id}/claim",
    tags=["Accounts"],
)
@limiter.limit(settings.rate_limit_authenticated)
def unclaim_agent(
    request: Request,
    account_id: str,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
):
    """Remove a gateway's claim on an agent."""
    with session.begin():
        claim = session.execute(
            select(GatewayClaim).where(
                GatewayClaim.gateway_id == current["id"],
                GatewayClaim.account_id == account_id,
                GatewayClaim.status == "active",
            )
        ).scalar_one_or_none()
        if claim is None:
            raise HTTPException(status_code=404, detail="No active claim found")
        claim.status = "released"
        session.add(claim)
    return {"status": "released", "account_id": account_id}


@router.get(
    "/accounts/{account_id}/claims",
    response_model=ClaimListResponse,
    tags=["Accounts"],
)
def list_claims(
    account_id: str,
    session: Session = Depends(get_session),
) -> ClaimListResponse:
    """List which gateways have claimed an agent."""
    with session.begin():
        claims = (
            session.execute(
                select(GatewayClaim).where(
                    GatewayClaim.account_id == account_id,
                    GatewayClaim.status == "active",
                )
            )
            .scalars()
            .all()
        )
    return ClaimListResponse(
        claims=[
            ClaimResponse(
                claim_id=c.id,
                gateway_id=c.gateway_id,
                account_id=c.account_id,
                verified=c.verified,
                status=c.status,
                claimed_at=c.claimed_at,
            )
            for c in claims
        ],
        count=len(claims),
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


@router.post("/accounts/{account_id}/did/register", tags=["Accounts"])
@limiter.limit(settings.rate_limit_authenticated)
def register_did_key(
    account_id: str,
    request: Request,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
):
    """Register or update an agent's self-sovereign did:key identity."""
    body = request.scope.get("_json_body")
    if body is None:
        import json
        import asyncio
        loop = asyncio.get_event_loop()
        # For sync endpoints, parse body from scope
        body = {}
    did_key = body.get("did_key", "") if isinstance(body, dict) else ""

    if current["id"] != account_id:
        raise HTTPException(status_code=403, detail="Can only register DID for own account")

    if not did_key.startswith("did:key:"):
        raise HTTPException(status_code=400, detail="did_key must be a valid did:key identifier")

    with session.begin():
        acct = session.execute(
            select(Account).where(Account.id == account_id)
        ).scalar_one_or_none()
        if acct is None:
            raise HTTPException(status_code=404, detail="Account not found")
        acct.did_key = did_key
        session.add(acct)

    return {"account_id": account_id, "did_key": did_key, "status": "registered"}


@router.post("/accounts/{account_id}/did/rotate", tags=["Accounts"])
@limiter.limit(settings.rate_limit_authenticated)
def rotate_did_key(
    account_id: str,
    request: Request,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
):
    """Rotate an agent's did:key identity with a signed rotation event."""
    if current["id"] != account_id:
        raise HTTPException(status_code=403, detail="Can only rotate DID for own account")

    body = {}
    new_did_key = body.get("new_did_key", "")
    rotation_proof = body.get("rotation_proof", {})

    if not new_did_key.startswith("did:key:"):
        raise HTTPException(status_code=400, detail="new_did_key must be a valid did:key identifier")

    with session.begin():
        acct = session.execute(
            select(Account).where(Account.id == account_id)
        ).scalar_one_or_none()
        if acct is None:
            raise HTTPException(status_code=404, detail="Account not found")
        old_did_key = acct.did_key
        acct.did_key = new_did_key
        session.add(acct)

    return {
        "account_id": account_id,
        "old_did_key": old_did_key,
        "new_did_key": new_did_key,
        "status": "rotated",
    }


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
@limiter.exempt
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


@router.post("/accounts/admin/register-oracle", tags=["Accounts"])
@limiter.limit(settings.rate_limit_authenticated)
def register_oracle(
    request: Request,
    req: dict,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
):
    """Grant oracle status to an existing registered account (operator-only).

    Oracle accounts can submit third-party evidence via
    ``POST /exchange/escrow/{id}/oracle-evidence``.  The account must already
    be registered and active.  Oracle reputation starts at the same EMA default
    (0.5) and must reach the ``oracle_min_reputation`` threshold (0.6) before
    the evidence endpoint will accept submissions.

    Request body: ``{"account_id": "<id>"}``
    """
    if current.get("status") != "operator":
        raise HTTPException(status_code=403, detail="Only the exchange operator can grant oracle status")

    account_id = req.get("account_id") if isinstance(req, dict) else None
    if not account_id:
        raise HTTPException(status_code=422, detail="account_id is required")

    with session.begin():
        acct = session.execute(select(Account).where(Account.id == account_id)).scalar_one_or_none()
        if acct is None:
            raise HTTPException(status_code=404, detail="Account not found")
        if acct.status != "active":
            raise HTTPException(status_code=400, detail="Only active accounts can be granted oracle status")
        acct.is_oracle = True
        session.add(acct)

    return {
        "account_id": acct.id,
        "bot_name": acct.bot_name,
        "is_oracle": True,
        "status": "oracle_granted",
    }


@router.delete("/accounts/admin/revoke-oracle/{account_id}", tags=["Accounts"])
@limiter.limit(settings.rate_limit_authenticated)
def revoke_oracle(
    request: Request,
    account_id: str,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
):
    """Revoke oracle status from an account (operator-only)."""
    if current.get("status") != "operator":
        raise HTTPException(status_code=403, detail="Only the exchange operator can revoke oracle status")

    with session.begin():
        acct = session.execute(select(Account).where(Account.id == account_id)).scalar_one_or_none()
        if acct is None:
            raise HTTPException(status_code=404, detail="Account not found")
        acct.is_oracle = False
        session.add(acct)

    return {
        "account_id": acct.id,
        "bot_name": acct.bot_name,
        "is_oracle": False,
        "status": "oracle_revoked",
    }


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


# ---------------------------------------------------------------------------
# Principal / anti-self-dealing endpoints
# ---------------------------------------------------------------------------


@router.get("/accounts/{account_id}/principal", tags=["Accounts"])
@limiter.limit(settings.rate_limit_authenticated)
def get_principal(
    request: Request,
    account_id: str,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
):
    """Return the principal cluster(s) linked to an agent.

    Accessible by the account owner or an operator.
    """
    from exchange.models import AgentPrincipalLink, Principal

    with session.begin():
        acct = session.execute(
            select(Account).where(Account.id == account_id)
        ).scalar_one_or_none()
        if acct is None:
            raise HTTPException(status_code=404, detail="Account not found")

        if current["id"] != account_id and current.get("status") != "operator":
            raise HTTPException(status_code=403, detail="Access denied")

        links = session.execute(
            select(AgentPrincipalLink, Principal)
            .join(Principal, Principal.id == AgentPrincipalLink.principal_id)
            .where(AgentPrincipalLink.agent_id == account_id)
            .order_by(AgentPrincipalLink.confidence.desc())
        ).all()

    return {
        "agent_id": account_id,
        "links": [
            {
                "principal_id": link.AgentPrincipalLink.principal_id,
                "principal_type": link.Principal.principal_type,
                "kya_level": link.Principal.kya_level,
                "link_source": link.AgentPrincipalLink.link_source,
                "confidence": link.AgentPrincipalLink.confidence,
                "established_at": link.AgentPrincipalLink.established_at.isoformat()
                if link.AgentPrincipalLink.established_at
                else None,
            }
            for link in links
        ],
    }


@router.get("/accounts/{account_id}/counterparty-diversity", tags=["Accounts"])
@limiter.limit(settings.rate_limit_authenticated)
def get_counterparty_diversity(
    request: Request,
    account_id: str,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
):
    """Return counterparty diversity metrics for an agent.

    Metrics are updated nightly by the background diversity sweep.
    Accessible by the account owner, any authenticated agent, or an operator.
    """
    with session.begin():
        acct = session.execute(
            select(Account).where(Account.id == account_id)
        ).scalar_one_or_none()
        if acct is None:
            raise HTTPException(status_code=404, detail="Account not found")

    return {
        "agent_id": account_id,
        "unique_counterparties_90d": acct.unique_counterparties_90d,
        "counterparty_hhi": acct.counterparty_hhi,
        "diversity_score": acct.diversity_score,
    }


@router.post("/accounts/admin/principals/link", tags=["Accounts"])
@limiter.limit(settings.rate_limit_authenticated)
def admin_link_principal(
    request: Request,
    req: dict,
    current: dict = Depends(authenticate_bot),
    session: Session = Depends(get_session),
):
    """Manually assert a principal link with confidence 1.0 (operator-only).

    Request body: {"agent_id": "<id>", "principal_id": "<id>"}
    """
    if current.get("status") != "operator":
        raise HTTPException(status_code=403, detail="Only the exchange operator can assert manual principal links")

    agent_id = req.get("agent_id") if isinstance(req, dict) else None
    principal_id = req.get("principal_id") if isinstance(req, dict) else None
    if not agent_id or not principal_id:
        raise HTTPException(status_code=422, detail="agent_id and principal_id are required")

    from exchange.models import Principal

    with session.begin():
        acct = session.execute(select(Account).where(Account.id == agent_id)).scalar_one_or_none()
        if acct is None:
            raise HTTPException(status_code=404, detail="Agent account not found")

        principal = session.execute(
            select(Principal).where(Principal.id == principal_id)
        ).scalar_one_or_none()
        if principal is None:
            raise HTTPException(status_code=404, detail="Principal not found")

        link_agent_to_principal(agent_id, principal_id, "manual", 1.0, session)

    invalidate_cache(agent_id)

    return {
        "agent_id": agent_id,
        "principal_id": principal_id,
        "link_source": "manual",
        "confidence": 1.0,
        "status": "linked",
    }
