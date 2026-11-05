from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from exchange.config import get_session, settings
from exchange.models import Account

# Optional request header naming the caller's account, so only that account's
# key hash is checked instead of every account's.
ACCOUNT_ID_HEADER = "X-A2A-Account-Id"


def _check_api_key(api_key: str, api_key_hash: str) -> bool:
    try:
        return bcrypt.checkpw(api_key.encode("utf-8"), api_key_hash.encode("utf-8"))
    except Exception:
        return False


def _verify_signature(
    api_key: str,
    method: str,
    path: str,
    body: bytes,
    signature: str,
    timestamp: str,
) -> bool:
    """Verify HMAC-SHA256 signature: sign(timestamp + method + path + body)."""
    try:
        ts = int(timestamp)
    except (ValueError, TypeError):
        return False

    now_ts = int(datetime.now(timezone.utc).timestamp())
    if abs(now_ts - ts) > settings.signature_max_age_seconds:
        return False

    message = f"{timestamp}{method}{path}".encode("utf-8") + body
    expected = hmac.new(api_key.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


async def authenticate_bot(
    request: Request,
    authorization: str | None = Header(default=None),
    x_a2a_signature: str | None = Header(default=None),
    x_a2a_timestamp: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid Authorization header. Use: Bearer ate_<your_api_key>",
        )
    api_key = authorization.split(" ", 1)[1].strip()
    if not api_key.startswith("ate_"):
        raise HTTPException(status_code=401, detail="Invalid API key format")

    has_signature = x_a2a_signature is not None and x_a2a_timestamp is not None
    if settings.require_signatures and not has_signature:
        raise HTTPException(
            status_code=401,
            detail="Request signature required. Provide X-A2A-Signature and X-A2A-Timestamp headers.",
        )

    if has_signature:
        body = await request.body()
        if not _verify_signature(
            api_key,
            request.method,
            request.url.path,
            body,
            x_a2a_signature,  # type: ignore[arg-type]
            x_a2a_timestamp,  # type: ignore[arg-type]
        ):
            raise HTTPException(status_code=401, detail="Invalid request signature")

    account_hint = request.headers.get(ACCOUNT_ID_HEADER)
    if account_hint is not None:
        account_hint = account_hint.strip()
        if not account_hint:
            raise HTTPException(status_code=401, detail="Invalid API key")

    # bcrypt is CPU-bound; running it on the event loop would stall every
    # request this worker is serving.
    account = await run_in_threadpool(_find_account, session, api_key, account_hint)
    if account is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return account


def _find_account(session: Session, api_key: str, account_id: str | None) -> dict | None:
    """Return the active or operator account whose key matches.

    With ``account_id`` only that account's hashes are checked. Without it,
    every active and operator account is checked in turn, one bcrypt
    comparison each.
    """
    stmt = select(Account).where(Account.status.in_(("active", "operator")))
    if account_id is not None:
        stmt = stmt.where(Account.id == account_id)
    with session.begin():
        accounts = session.execute(stmt).scalars().all()
        now = datetime.now(timezone.utc)
        grace = timedelta(minutes=settings.key_rotation_grace_minutes)

        for acct in accounts:
            if _check_api_key(api_key, acct.api_key_hash) or (
                acct.previous_api_key_hash
                and acct.key_rotated_at
                and (now - acct.key_rotated_at) < grace
                and _check_api_key(api_key, acct.previous_api_key_hash)
            ):
                return {
                    "id": acct.id,
                    "bot_name": acct.bot_name,
                    "developer_id": acct.developer_id,
                    "status": acct.status,
                    "account_type": getattr(acct, "account_type", "agent"),
                }
    return None


async def authenticate_bot_optional(
    request: Request,
    authorization: str | None = Header(default=None),
    x_a2a_signature: str | None = Header(default=None),
    x_a2a_timestamp: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> dict | None:
    """Like authenticate_bot, but returns None when no Authorization header is present.

    Invalid credentials still raise 401 — only a missing header is treated as anonymous.
    """
    if not authorization:
        return None
    return await authenticate_bot(
        request,
        authorization=authorization,
        x_a2a_signature=x_a2a_signature,
        x_a2a_timestamp=x_a2a_timestamp,
        session=session,
    )