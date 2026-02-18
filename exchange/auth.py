from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from exchange.config import get_session, settings
from exchange.models import Account


def _check_api_key(api_key: str, api_key_hash: str) -> bool:
    try:
        return bcrypt.checkpw(api_key.encode("utf-8"), api_key_hash.encode("utf-8"))
    except Exception:
        return False


def authenticate_bot(
    authorization: str | None = Header(default=None),
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
            # Check previous key during grace period
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
