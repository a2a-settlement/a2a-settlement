"""Background attestation and identity monitor.

Runs periodically to detect expiring attestations, downgrade agents whose
credentials have lapsed, and re-resolve DIDs for agents at Level 1+.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from exchange.config import get_session, settings
from exchange.identity.did_resolver import DIDResolutionError, DIDResolver
from exchange.models import Account

logger = logging.getLogger(__name__)


class KYAMonitor:
    """Periodic background monitor for KYA attestation health.

    Parameters
    ----------
    did_resolver:
        Shared resolver used for periodic DID re-checks.
    check_interval_seconds:
        Seconds between monitor cycles (default from settings).
    expiry_warning_days:
        How many days before expiry to emit warnings.
    did_recheck_interval_hours:
        How often to re-resolve agent DIDs.
    """

    def __init__(
        self,
        did_resolver: DIDResolver | None = None,
        check_interval_seconds: int | None = None,
        expiry_warning_days: int | None = None,
        did_recheck_interval_hours: int | None = None,
    ):
        self.did_resolver = did_resolver or DIDResolver(
            cache_ttl_seconds=settings.kya_did_cache_ttl_seconds,
            http_timeout=settings.kya_did_http_timeout_seconds,
        )
        self.check_interval = check_interval_seconds or settings.kya_monitor_interval_seconds
        self.expiry_warning_days = expiry_warning_days or settings.kya_expiry_warning_days
        self.did_recheck_hours = did_recheck_interval_hours or settings.kya_did_recheck_hours

    async def run(self) -> None:
        """Main loop — launch via ``asyncio.create_task``."""
        while True:
            try:
                self._tick()
            except Exception:
                logger.exception("KYA monitor tick failed")
            await asyncio.sleep(self.check_interval)

    def _tick(self) -> None:
        gen = get_session()
        session = next(gen)
        try:
            with session.begin():
                self.check_expiring_attestations(session)
                self.check_expired_attestations(session)
                self.recheck_agent_identities(session)
        finally:
            try:
                next(gen)
            except StopIteration:
                pass

    def check_expiring_attestations(self, session: Session) -> list[str]:
        """Emit warnings for agents whose attestations expire soon."""
        now = datetime.now(timezone.utc)
        warning_boundary = now + timedelta(days=self.expiry_warning_days)
        agents = session.execute(
            select(Account).where(
                and_(
                    Account.attestation_expires_at.isnot(None),
                    Account.attestation_expires_at <= warning_boundary,
                    Account.attestation_expires_at > now,
                    Account.kya_level_verified >= 2,
                )
            )
        ).scalars().all()
        warned: list[str] = []
        for agent in agents:
            logger.warning(
                "attestation_expiring agent=%s did=%s expires=%s",
                agent.id, agent.did, agent.attestation_expires_at,
            )
            warned.append(agent.id)
        return warned

    def check_expired_attestations(self, session: Session) -> list[str]:
        """Downgrade agents whose attestations have expired."""
        now = datetime.now(timezone.utc)
        agents = session.execute(
            select(Account).where(
                and_(
                    Account.attestation_expires_at.isnot(None),
                    Account.attestation_expires_at <= now,
                    Account.kya_level_verified >= 2,
                )
            )
        ).scalars().all()
        downgraded: list[str] = []
        for agent in agents:
            logger.warning(
                "attestation_expired agent=%s — downgrading from level %d to 1",
                agent.id, agent.kya_level_verified,
            )
            agent.kya_level_verified = 1
            agent.attestation_expires_at = None
            session.add(agent)
            downgraded.append(agent.id)
        return downgraded

    def recheck_agent_identities(self, session: Session) -> list[str]:
        """Re-resolve DIDs for Level 1+ agents not checked recently."""
        now = datetime.now(timezone.utc)
        stale_before = now - timedelta(hours=self.did_recheck_hours)
        agents = session.execute(
            select(Account).where(
                and_(
                    Account.kya_level_verified >= 1,
                    Account.did.isnot(None),
                    Account.card_verified_at <= stale_before,
                )
            )
        ).scalars().all()
        unresolvable: list[str] = []
        for agent in agents:
            try:
                self.did_resolver.resolve(agent.did, force_refresh=True)
                agent.card_verified_at = now
                session.add(agent)
            except DIDResolutionError:
                logger.error(
                    "identity_unresolvable agent=%s did=%s", agent.id, agent.did,
                )
                unresolvable.append(agent.id)
        return unresolvable
