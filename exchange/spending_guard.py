from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import and_, select, update
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from exchange.config import SessionLocal
from exchange.models import Account, Transaction
from exchange.webhooks import fire_account_webhook_event

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_aware(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; assume UTC when tzinfo is absent."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class SpendingLimitGuard:
    """Circuit breaker that enforces rolling-window spending limits and hourly
    velocity caps, auto-freezing accounts on breach."""

    def __init__(
        self,
        spending_window_hours: int,
        hourly_velocity_limit: int,
        spending_freeze_minutes: int,
    ) -> None:
        self.spending_window_hours = spending_window_hours
        self.hourly_velocity_limit = hourly_velocity_limit
        self.spending_freeze_minutes = spending_freeze_minutes

    def _spent_since(self, session: Session, account_id: str, since: datetime) -> int:
        return int(
            session.execute(
                select(sa_func.coalesce(sa_func.sum(Transaction.amount), 0)).where(
                    and_(
                        Transaction.from_account == account_id,
                        Transaction.tx_type == "escrow_hold",
                        Transaction.created_at >= since,
                    )
                )
            ).scalar_one()
        )

    def _freeze_account(self, account_id: str, frozen_until: datetime, reason: str) -> None:
        """Persist the freeze in an independent session so it survives caller rollback."""
        db = SessionLocal()
        try:
            with db.begin():
                db.execute(
                    update(Account)
                    .where(Account.id == account_id)
                    .values(frozen_until=frozen_until)
                )
        finally:
            db.close()

        logger.warning("Account %s frozen until %s: %s", account_id, frozen_until.isoformat(), reason)
        fire_account_webhook_event(
            account_id,
            "account.spending_limit_breached",
            {"account_id": account_id, "frozen_until": frozen_until.isoformat(), "reason": reason},
        )

    def check(self, session: Session, account_id: str, new_hold: int) -> None:
        """Validate spending limits. Raises HTTPException on violation."""
        acct = session.execute(
            select(Account).where(Account.id == account_id)
        ).scalar_one_or_none()
        if acct is None:
            return

        now = _now()
        if acct.frozen_until is not None and _ensure_aware(acct.frozen_until) > now:
            raise HTTPException(
                status_code=423,
                detail=(
                    f"Account is temporarily frozen until {acct.frozen_until.isoformat()}. "
                    "Spending limit was exceeded."
                ),
            )
        if acct.frozen_until is not None and _ensure_aware(acct.frozen_until) <= now:
            acct.frozen_until = None
            session.add(acct)

        limit = acct.daily_spend_limit
        if limit is not None and limit > 0:
            window_start = now - timedelta(hours=self.spending_window_hours)
            spent = self._spent_since(session, account_id, window_start)
            if spent + new_hold > limit:
                frozen_until = now + timedelta(minutes=self.spending_freeze_minutes)
                reason = (
                    f"Rolling {self.spending_window_hours}h spend limit breached "
                    f"(limit={limit}, spent={spent}, requested={new_hold})"
                )
                self._freeze_account(account_id, frozen_until, reason)
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Daily spend limit exceeded. Limit: {limit}, "
                        f"spent in last {self.spending_window_hours}h: {spent}, "
                        f"requested: {new_hold}. Account frozen for "
                        f"{self.spending_freeze_minutes} minutes."
                    ),
                )

        if self.hourly_velocity_limit > 0:
            hour_start = now - timedelta(hours=1)
            spent_hour = self._spent_since(session, account_id, hour_start)
            if spent_hour + new_hold > self.hourly_velocity_limit:
                frozen_until = now + timedelta(minutes=self.spending_freeze_minutes)
                reason = (
                    f"Hourly velocity limit breached "
                    f"(limit={self.hourly_velocity_limit}, spent={spent_hour}, requested={new_hold})"
                )
                self._freeze_account(account_id, frozen_until, reason)
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Hourly spending velocity exceeded. Limit: {self.hourly_velocity_limit}, "
                        f"spent in last hour: {spent_hour}, requested: {new_hold}. "
                        f"Account frozen for {self.spending_freeze_minutes} minutes."
                    ),
                )
