"""Idempotency for experiment financial routes.

The ledger mutation and the completed operation row commit in one transaction.
A missing key is required only for bound accounts. Unbound callers keep the
optional header. There is no pre-handler insert.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from exchange.models import Account, FinancialOperation

_FINANCIAL_ROUTES = {
    "/exchange/escrow",
    "/exchange/release",
    "/exchange/refund",
    "/exchange/instant-settle",
    "/exchange/escrow/{escrow_id}/partial-release",
}


def canonical_route(path: str) -> str:
    stripped = path
    for prefix in ("/api/v1", "/v1"):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix):]
            break
    if re.fullmatch(r"/exchange/escrow/[^/]+/partial-release", stripped):
        return "/exchange/escrow/{escrow_id}/partial-release"
    return stripped


def route_is_financial(path: str) -> bool:
    return canonical_route(path) in _FINANCIAL_ROUTES


@dataclass
class PendingOperation:
    principal_id: str
    method: str
    route: str
    idempotency_key: str
    request_hash: str


@dataclass
class Replay:
    status_code: int
    body: dict


def _body_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def prepare(
    request: Request,
    session: Session,
    principal_id: str,
    body: bytes,
) -> PendingOperation | Replay | None:
    route = canonical_route(request.url.path)
    if route not in _FINANCIAL_ROUTES:
        return None
    key = request.headers.get("idempotency-key")
    with session.begin():
        account = session.get(Account, principal_id)
        bound = account is not None and bool(account.obligation_policy_id or account.root_budget_id)
        existing = None
        request_hash = _body_hash(body)
        if key:
            existing = session.execute(
                select(FinancialOperation).where(
                    FinancialOperation.principal_id == principal_id,
                    FinancialOperation.method == request.method.upper(),
                    FinancialOperation.route == route,
                    FinancialOperation.idempotency_key == key,
                )
            ).scalar_one_or_none()
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise HTTPException(
                        status_code=409,
                        detail="Idempotency key reused with a different request body",
                    )
                replay_status = existing.status_code
                replay_body = json.loads(existing.response_body)
            else:
                replay_status = None
                replay_body = None
        else:
            request_hash = _body_hash(body)
            replay_status = None
            replay_body = None
    if not key:
        if bound:
            raise HTTPException(
                status_code=400,
                detail="Idempotency-Key is required for experiment financial operations",
            )
        return None
    if replay_status is not None:
        return Replay(status_code=replay_status, body=replay_body)
    return PendingOperation(
        principal_id=principal_id,
        method=request.method.upper(),
        route=route,
        idempotency_key=key,
        request_hash=request_hash,
    )


def store(session: Session, pending: PendingOperation, status_code: int, body: dict) -> None:
    session.add(
        FinancialOperation(
            principal_id=pending.principal_id,
            method=pending.method,
            route=pending.route,
            idempotency_key=pending.idempotency_key,
            request_hash=pending.request_hash,
            response_body=json.dumps(body, default=str),
            status_code=status_code,
        )
    )


def lookup_replay(
    session: Session,
    pending: PendingOperation,
) -> Replay | None:
    with session.begin():
        existing = session.execute(
            select(FinancialOperation).where(
                FinancialOperation.principal_id == pending.principal_id,
                FinancialOperation.method == pending.method,
                FinancialOperation.route == pending.route,
                FinancialOperation.idempotency_key == pending.idempotency_key,
            )
        ).scalar_one_or_none()
        if existing is None:
            return None
        if existing.request_hash != pending.request_hash:
            raise HTTPException(
                status_code=409,
                detail="Idempotency key reused with a different request body",
            )
        return Replay(status_code=existing.status_code, body=json.loads(existing.response_body))
