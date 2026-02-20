from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from exchange.config import get_session, settings
from exchange.models import Account


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

    with session.begin():
        accounts = session.execute(select(Account).where(Account.status != "suspended")).scalars().all()
        now = datetime.now(timezone.utc)
        grace = timedelta(minutes=settings.key_rotation_grace_minutes)

        for acct in accounts:
            if _check_api_key(api_key, acct.api_key_hash):
                return {
                    "id": acct.id,
                    "bot_name": acct.bot_name,
                    "developer_id": acct.developer_id,
                    "status": acct.status,
                }
            if (
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
                }

    raise HTTPException(status_code=401, detail="Invalid API key")
