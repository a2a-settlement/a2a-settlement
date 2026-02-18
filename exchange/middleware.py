from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Request, Response
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from exchange.config import SessionLocal
from exchange.models import IdempotencyRecord


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Ensures every request/response carries an X-Request-Id header."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex[:12]}"
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """Caches POST responses keyed by the Idempotency-Key header."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method != "POST":
            return await call_next(request)

        idem_key = request.headers.get("idempotency-key")
        if not idem_key:
            return await call_next(request)

        body = await request.body()
        body_hash = hashlib.sha256(body).hexdigest()

        session = SessionLocal()
        try:
            with session.begin():
                now = datetime.now(timezone.utc)
                # Clean expired records opportunistically
                session.execute(
                    IdempotencyRecord.__table__.delete().where(IdempotencyRecord.expires_at < now)
                )

                record = session.execute(
                    select(IdempotencyRecord).where(IdempotencyRecord.key == idem_key)
                ).scalar_one_or_none()

                if record is not None:
                    if record.request_hash != body_hash:
                        return Response(
                            content=json.dumps({
                                "error": {
                                    "code": "IDEMPOTENCY_CONFLICT",
                                    "message": "Idempotency key reused with a different request body",
                                    "request_id": getattr(request.state, "request_id", ""),
                                }
                            }),
                            status_code=409,
                            media_type="application/json",
                        )
                    return Response(
                        content=record.response_body,
                        status_code=record.status_code,
                        media_type="application/json",
                    )
        finally:
            session.close()

        response = await call_next(request)

        if 200 <= response.status_code < 300:
            resp_body = b""
            async for chunk in response.body_iterator:
                if isinstance(chunk, str):
                    resp_body += chunk.encode("utf-8")
                else:
                    resp_body += chunk

            session = SessionLocal()
            try:
                with session.begin():
                    session.add(IdempotencyRecord(
                        key=idem_key,
                        request_hash=body_hash,
                        response_body=resp_body.decode("utf-8"),
                        status_code=response.status_code,
                        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
                    ))
            finally:
                session.close()

            return Response(
                content=resp_body,
                status_code=response.status_code,
                media_type=response.media_type,
                headers=dict(response.headers),
            )

        return response
