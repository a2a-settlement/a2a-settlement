"""Automatic terminal transitions settle a root-budget reservation once."""

from __future__ import annotations

import hashlib
import importlib
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests.test_acceptance_binding import TASK, _bootstrap, _register

AMOUNT = 10
FEE = 1
HOLD = AMOUNT + FEE
STAKE = 10
CANCEL_HASH = hashlib.sha256(b"workflow-cancel-secret").hexdigest()
# Evidence window is 72h and dispute TTL is 60m after that.
PAST_EVIDENCE = timedelta(hours=73)
PAST_DISPUTE = timedelta(hours=75)


class _Crash(RuntimeError):
    pass


def _budget(client, auth_header, key, root_budget_id) -> tuple[int, int]:
    resp = client.get(
        f"/v1/exchange/assurance/root-budgets/{root_budget_id}",
        headers=auth_header(key),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    reserved = int(body["reserved_units"])
    spent = int(body["spent_units"])
    assert reserved >= 0
    assert spent >= 0
    return reserved, spent


def _balance(client, auth_header, key) -> dict:
    resp = client.get("/v1/exchange/balance", headers=auth_header(key))
    assert resp.status_code == 200, resp.text
    return resp.json()


def _detail(client, auth_header, key, escrow_id) -> dict:
    resp = client.get(
        f"/v1/exchange/escrows/{escrow_id}",
        headers=auth_header(key),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _txns(client, auth_header, key) -> list[dict]:
    resp = client.get("/v1/exchange/transactions", headers=auth_header(key))
    assert resp.status_code == 200, resp.text
    return resp.json()["transactions"]


def _attestations(client, escrow_id) -> list[dict]:
    resp = client.get(f"/v1/exchange/escrow/{escrow_id}/attestations")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enabled"] is True
    return body["attestations"]


def _statuses(attestations: list[dict]) -> list[str]:
    return [item["payload"]["mediation"]["escrow_status"] for item in attestations]


def _sweep(moment: datetime) -> dict:
    from exchange.tasks import run_expiry_sweep

    with patch("exchange.observers._now", return_value=moment):
        return run_expiry_sweep()


def _boom_after(real):
    def _inner(*args, **kwargs):
        real(*args, **kwargs)
        raise _Crash("crash before commit")

    return _inner


def _open(client, auth_header, ctx, provider_id: str, key: str, **extra):
    body = {
        "provider_id": provider_id,
        "amount": AMOUNT,
        "task_id": TASK,
        **extra,
    }
    resp = client.post(
        "/v1/exchange/escrow",
        headers={**auth_header(ctx["requester"]["api_key"]), "Idempotency-Key": key},
        json=body,
    )
    assert resp.status_code == 201, resp.text
    payload = resp.json()
    assert payload["fee_amount"] == FEE
    assert payload["total_held"] == HOLD
    return payload


def _dispute(client, auth_header, key, escrow_id: str):
    resp = client.post(
        "/v1/exchange/dispute",
        headers=auth_header(key),
        json={"escrow_id": escrow_id, "reason": "bad work", "stake_amount": STAKE},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _evidence(client, auth_header, key, escrow_id: str):
    resp = client.post(
        f"/v1/exchange/escrow/{escrow_id}/evidence",
        headers=auth_header(key),
        json={"evidence_type": "service", "summary": "work was delivered as agreed"},
    )
    assert resp.status_code == 200, resp.text


def _refund(client, auth_header, key, escrow_id: str, idem: str):
    return client.post(
        "/v1/exchange/refund",
        headers={**auth_header(key), "Idempotency-Key": idem},
        json={"escrow_id": escrow_id},
    )


def test_expiry_releases_reservation_once(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        ctx = _bootstrap(
            client, auth_header, acceptance_required=False, limit_units=HOLD
        )
        created = _open(
            client,
            auth_header,
            ctx,
            ctx["provider"]["account"]["id"],
            "hold-expire",
            ttl_minutes=5,
        )
        root = ctx["root_budget_id"]
        key = ctx["requester"]["api_key"]
        assert _budget(client, auth_header, key, root) == (HOLD, 0)

        moment = datetime.now(timezone.utc) + timedelta(minutes=10)
        first = _sweep(moment)
        second = _sweep(moment)

        assert first["expired_held"] == 1
        assert second["expired_held"] == 0
        assert (
            _detail(client, auth_header, key, created["escrow_id"])["status"]
            == "expired"
        )
        assert _budget(client, auth_header, key, root) == (0, 0)
        bal = _balance(client, auth_header, key)
        assert bal["available"] == 100
        assert bal["held_in_escrow"] == 0
        assert bal["total_spent"] == 0
        refunds = [
            tx
            for tx in _txns(client, auth_header, key)
            if tx["type"] == "escrow_refund"
        ]
        assert [(tx["amount"], tx["description"]) for tx in refunds] == [
            (HOLD, "Auto-expired: TTL exceeded")
        ]
        assert (
            _statuses(_attestations(client, created["escrow_id"])).count("expired") == 1
        )


def test_dispute_expiry_releases_reservation_once(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        ctx = _bootstrap(
            client, auth_header, acceptance_required=False, limit_units=HOLD
        )
        created = _open(
            client, auth_header, ctx, ctx["provider"]["account"]["id"], "hold-dispute"
        )
        eid = created["escrow_id"]
        key = ctx["requester"]["api_key"]
        provider_key = ctx["provider"]["api_key"]
        _dispute(client, auth_header, key, eid)
        _evidence(client, auth_header, provider_key, eid)
        root = ctx["root_budget_id"]
        assert _budget(client, auth_header, key, root) == (HOLD, 0)

        moment = datetime.now(timezone.utc) + PAST_DISPUTE
        promoted = _sweep(moment)
        assert promoted["expired_disputes"] == 0
        assert promoted["defaulted_evidence"] == 0
        assert _detail(client, auth_header, key, eid)["status"] == "disputed"
        assert _budget(client, auth_header, key, root) == (HOLD, 0)
        held = _balance(client, auth_header, key)
        assert held["available"] == 100 - HOLD - STAKE
        assert held["held_in_escrow"] == HOLD + STAKE

        first = _sweep(moment)
        second = _sweep(moment)

        assert first["expired_disputes"] == 1
        assert second["expired_disputes"] == 0
        assert _detail(client, auth_header, key, eid)["status"] == "expired"
        assert _budget(client, auth_header, key, root) == (0, 0)
        bal = _balance(client, auth_header, key)
        # The dispute stake stays held; only amount + fee is returned.
        assert bal["available"] == 100 - STAKE
        assert bal["held_in_escrow"] == STAKE
        refunds = [
            tx
            for tx in _txns(client, auth_header, key)
            if tx["type"] == "escrow_refund"
        ]
        assert [(tx["amount"], tx["description"]) for tx in refunds] == [
            (HOLD, "Auto-expired: dispute TTL exceeded")
        ]
        assert _statuses(_attestations(client, eid)).count("expired") == 1


def test_default_judgment_refund_releases_reservation_once(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        ctx = _bootstrap(
            client, auth_header, acceptance_required=False, limit_units=HOLD
        )
        created = _open(
            client, auth_header, ctx, ctx["provider"]["account"]["id"], "hold-dj-refund"
        )
        eid = created["escrow_id"]
        key = ctx["requester"]["api_key"]
        _dispute(client, auth_header, key, eid)
        root = ctx["root_budget_id"]

        moment = datetime.now(timezone.utc) + PAST_EVIDENCE
        first = _sweep(moment)
        second = _sweep(moment)

        assert first["defaulted_evidence"] == 1
        assert second["defaulted_evidence"] == 0
        detail = _detail(client, auth_header, key, eid)
        assert detail["status"] == "refunded"
        assert detail["resolved_at"] is not None
        assert _budget(client, auth_header, key, root) == (0, 0)
        bal = _balance(client, auth_header, key)
        assert bal["available"] == 100 - STAKE
        assert bal["held_in_escrow"] == STAKE
        assert bal["total_spent"] == 0
        refunds = [
            tx
            for tx in _txns(client, auth_header, key)
            if tx["type"] == "escrow_refund"
        ]
        assert [(tx["amount"], tx["description"]) for tx in refunds] == [
            (HOLD, "Default judgment: provider failed to submit evidence")
        ]
        assert _statuses(_attestations(client, eid)).count("refunded") == 1


def test_default_judgment_release_commits_spend_once(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        ctx = _bootstrap(
            client, auth_header, acceptance_required=False, limit_units=HOLD
        )
        created = _open(
            client,
            auth_header,
            ctx,
            ctx["provider"]["account"]["id"],
            "hold-dj-release",
        )
        eid = created["escrow_id"]
        key = ctx["requester"]["api_key"]
        provider_key = ctx["provider"]["api_key"]
        _dispute(client, auth_header, provider_key, eid)

        moment = datetime.now(timezone.utc) + PAST_EVIDENCE
        first = _sweep(moment)
        second = _sweep(moment)

        assert first["defaulted_evidence"] == 1
        assert second["defaulted_evidence"] == 0
        detail = _detail(client, auth_header, key, eid)
        assert detail["status"] == "released"
        assert detail["resolved_at"] is not None
        assert _budget(client, auth_header, key, ctx["root_budget_id"]) == (0, HOLD)
        requester = _balance(client, auth_header, key)
        assert requester["available"] == 100 - HOLD
        assert requester["held_in_escrow"] == 0
        assert requester["total_spent"] == HOLD
        provider = _balance(client, auth_header, provider_key)
        assert provider["available"] == 100
        assert provider["held_in_escrow"] == STAKE
        assert provider["total_earned"] == AMOUNT
        releases = [
            tx
            for tx in _txns(client, auth_header, key)
            if tx["type"] == "escrow_release"
        ]
        assert [(tx["amount"], tx["description"]) for tx in releases] == [
            (AMOUNT, "Default judgment: requester failed to submit evidence")
        ]
        assert [
            tx for tx in _txns(client, auth_header, key) if tx["type"] == "fee"
        ] == []
        assert _statuses(_attestations(client, eid)).count("released") == 1


def test_expiry_of_one_hold_leaves_the_other_reservation(exchange_app, auth_header):
    """reserved_units drops by amount + fee and never crosses zero."""
    with TestClient(exchange_app) as client:
        ctx = _bootstrap(
            client, auth_header, acceptance_required=False, limit_units=HOLD * 2
        )
        provider_b = _register(client, "ProviderTwo", ["analysis"])
        short = _open(
            client,
            auth_header,
            ctx,
            ctx["provider"]["account"]["id"],
            "hold-short",
            ttl_minutes=5,
        )
        long = _open(
            client,
            auth_header,
            ctx,
            provider_b["account"]["id"],
            "hold-long",
            ttl_minutes=120,
        )
        key = ctx["requester"]["api_key"]
        root = ctx["root_budget_id"]
        assert _budget(client, auth_header, key, root) == (HOLD * 2, 0)

        soon = datetime.now(timezone.utc) + timedelta(minutes=10)
        assert _sweep(soon)["expired_held"] == 1
        assert _sweep(soon)["expired_held"] == 0
        assert (
            _detail(client, auth_header, key, short["escrow_id"])["status"] == "expired"
        )
        assert _detail(client, auth_header, key, long["escrow_id"])["status"] == "held"
        assert _budget(client, auth_header, key, root) == (HOLD, 0)

        later = datetime.now(timezone.utc) + timedelta(minutes=180)
        assert _sweep(later)["expired_held"] == 1
        assert _sweep(later)["expired_held"] == 0
        assert _budget(client, auth_header, key, root) == (0, 0)
        assert _balance(client, auth_header, key)["available"] == 100
        assert _balance(client, auth_header, key)["held_in_escrow"] == 0


def test_expiry_crash_rolls_back_then_retry_settles_once(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        ctx = _bootstrap(
            client, auth_header, acceptance_required=False, limit_units=HOLD
        )
        created = _open(
            client,
            auth_header,
            ctx,
            ctx["provider"]["account"]["id"],
            "hold-crash",
            ttl_minutes=5,
        )
        key = ctx["requester"]["api_key"]
        root = ctx["root_budget_id"]
        import exchange.observers as observers_mod

        moment = datetime.now(timezone.utc) + timedelta(minutes=10)
        with patch.object(
            observers_mod,
            "release_reservation",
            _boom_after(observers_mod.release_reservation),
        ):
            with pytest.raises(_Crash):
                _sweep(moment)

        assert (
            _detail(client, auth_header, key, created["escrow_id"])["status"] == "held"
        )
        assert _budget(client, auth_header, key, root) == (HOLD, 0)
        bal = _balance(client, auth_header, key)
        assert bal["available"] == 100 - HOLD
        assert bal["held_in_escrow"] == HOLD

        assert _sweep(moment)["expired_held"] == 1
        assert _sweep(moment)["expired_held"] == 0
        assert (
            _detail(client, auth_header, key, created["escrow_id"])["status"]
            == "expired"
        )
        assert _budget(client, auth_header, key, root) == (0, 0)
        assert _balance(client, auth_header, key)["available"] == 100
        assert _balance(client, auth_header, key)["held_in_escrow"] == 0


def test_dispute_expiry_crash_rolls_back_then_retry_settles_once(
    exchange_app, auth_header
):
    with TestClient(exchange_app) as client:
        ctx = _bootstrap(
            client, auth_header, acceptance_required=False, limit_units=HOLD
        )
        created = _open(
            client, auth_header, ctx, ctx["provider"]["account"]["id"], "hold-dcrash"
        )
        eid = created["escrow_id"]
        key = ctx["requester"]["api_key"]
        _dispute(client, auth_header, key, eid)
        _evidence(client, auth_header, ctx["provider"]["api_key"], eid)
        moment = datetime.now(timezone.utc) + PAST_DISPUTE
        assert _sweep(moment)["defaulted_evidence"] == 0
        assert _detail(client, auth_header, key, eid)["status"] == "disputed"

        import exchange.observers as observers_mod

        with patch.object(
            observers_mod,
            "release_reservation",
            _boom_after(observers_mod.release_reservation),
        ):
            with pytest.raises(_Crash):
                _sweep(moment)

        assert _detail(client, auth_header, key, eid)["status"] == "disputed"
        assert _budget(client, auth_header, key, ctx["root_budget_id"]) == (HOLD, 0)
        assert _balance(client, auth_header, key)["held_in_escrow"] == HOLD + STAKE

        assert _sweep(moment)["expired_disputes"] == 1
        assert _sweep(moment)["expired_disputes"] == 0
        assert _detail(client, auth_header, key, eid)["status"] == "expired"
        assert _budget(client, auth_header, key, ctx["root_budget_id"]) == (0, 0)


def test_default_refund_crash_rolls_back_then_retry_settles_once(
    exchange_app, auth_header
):
    with TestClient(exchange_app) as client:
        ctx = _bootstrap(
            client, auth_header, acceptance_required=False, limit_units=HOLD
        )
        created = _open(
            client, auth_header, ctx, ctx["provider"]["account"]["id"], "hold-rc"
        )
        eid = created["escrow_id"]
        key = ctx["requester"]["api_key"]
        _dispute(client, auth_header, key, eid)
        moment = datetime.now(timezone.utc) + PAST_EVIDENCE
        import exchange.observers as observers_mod

        with patch.object(
            observers_mod,
            "release_reservation",
            _boom_after(observers_mod.release_reservation),
        ):
            with pytest.raises(_Crash):
                _sweep(moment)

        assert _detail(client, auth_header, key, eid)["status"] == "evidence_pending"
        assert _budget(client, auth_header, key, ctx["root_budget_id"]) == (HOLD, 0)
        assert _balance(client, auth_header, key)["available"] == 100 - HOLD - STAKE

        assert _sweep(moment)["defaulted_evidence"] == 1
        assert _sweep(moment)["defaulted_evidence"] == 0
        assert _detail(client, auth_header, key, eid)["status"] == "refunded"
        assert _budget(client, auth_header, key, ctx["root_budget_id"]) == (0, 0)


def test_default_release_crash_rolls_back_then_retry_settles_once(
    exchange_app, auth_header
):
    with TestClient(exchange_app) as client:
        ctx = _bootstrap(
            client, auth_header, acceptance_required=False, limit_units=HOLD
        )
        created = _open(
            client, auth_header, ctx, ctx["provider"]["account"]["id"], "hold-relc"
        )
        eid = created["escrow_id"]
        key = ctx["requester"]["api_key"]
        _dispute(client, auth_header, ctx["provider"]["api_key"], eid)
        moment = datetime.now(timezone.utc) + PAST_EVIDENCE
        import exchange.observers as observers_mod

        with patch.object(
            observers_mod, "commit_spend", _boom_after(observers_mod.commit_spend)
        ):
            with pytest.raises(_Crash):
                _sweep(moment)

        assert _detail(client, auth_header, key, eid)["status"] == "evidence_pending"
        assert _budget(client, auth_header, key, ctx["root_budget_id"]) == (HOLD, 0)
        assert _balance(client, auth_header, key)["held_in_escrow"] == HOLD
        assert _balance(client, auth_header, key)["total_spent"] == 0

        assert _sweep(moment)["defaulted_evidence"] == 1
        assert _sweep(moment)["defaulted_evidence"] == 0
        assert _detail(client, auth_header, key, eid)["status"] == "released"
        assert _budget(client, auth_header, key, ctx["root_budget_id"]) == (0, HOLD)


def test_dependent_chain_releases_each_reservation_once(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        ctx = _bootstrap(
            client, auth_header, acceptance_required=False, limit_units=HOLD * 3
        )
        provider_b = _register(client, "ProviderTwo", ["analysis"])
        provider_c = _register(client, "ProviderThree", ["analysis"])
        upstream = _open(
            client, auth_header, ctx, ctx["provider"]["account"]["id"], "chain-a"
        )
        middle = _open(
            client,
            auth_header,
            ctx,
            provider_b["account"]["id"],
            "chain-b",
            depends_on=[upstream["escrow_id"]],
        )
        tail = _open(
            client,
            auth_header,
            ctx,
            provider_c["account"]["id"],
            "chain-c",
            depends_on=[middle["escrow_id"]],
        )
        key = ctx["requester"]["api_key"]
        root = ctx["root_budget_id"]
        assert _budget(client, auth_header, key, root) == (HOLD * 3, 0)

        refunded = _refund(
            client, auth_header, key, upstream["escrow_id"], "chain-refund"
        )
        assert refunded.status_code == 200, refunded.text
        assert (
            _detail(client, auth_header, key, upstream["escrow_id"])["status"]
            == "refunded"
        )
        assert (
            _detail(client, auth_header, key, middle["escrow_id"])["status"]
            == "refunded"
        )
        assert (
            _detail(client, auth_header, key, tail["escrow_id"])["status"] == "refunded"
        )
        assert _budget(client, auth_header, key, root) == (0, 0)
        assert _balance(client, auth_header, key)["available"] == 100
        assert _balance(client, auth_header, key)["held_in_escrow"] == 0

        from exchange.config import SessionLocal
        from exchange.routes.settlement import _auto_refund_dependents

        session = SessionLocal()
        try:
            with session.begin():
                _auto_refund_dependents(session, upstream["escrow_id"])
                _auto_refund_dependents(session, middle["escrow_id"])
        finally:
            session.close()

        assert _budget(client, auth_header, key, root) == (0, 0)
        refunds = [
            tx
            for tx in _txns(client, auth_header, key)
            if tx["type"] == "escrow_refund"
        ]
        assert sorted((tx["amount"], tx["description"]) for tx in refunds) == sorted(
            [
                (HOLD, "Task failed or cancelled"),
                (
                    HOLD,
                    f"Auto-refunded: upstream escrow {upstream['escrow_id']} was refunded",
                ),
                (
                    HOLD,
                    f"Auto-refunded: upstream escrow {middle['escrow_id']} was refunded",
                ),
            ]
        )


def test_dependent_on_two_refunded_upstreams_is_refunded_once(
    exchange_app, auth_header
):
    with TestClient(exchange_app) as client:
        ctx = _bootstrap(
            client, auth_header, acceptance_required=False, limit_units=HOLD * 3
        )
        provider_b = _register(client, "ProviderTwo", ["analysis"])
        provider_c = _register(client, "ProviderThree", ["analysis"])
        upstream = _open(
            client, auth_header, ctx, ctx["provider"]["account"]["id"], "diamond-a"
        )
        middle = _open(
            client,
            auth_header,
            ctx,
            provider_b["account"]["id"],
            "diamond-b",
            depends_on=[upstream["escrow_id"]],
        )
        both = _open(
            client,
            auth_header,
            ctx,
            provider_c["account"]["id"],
            "diamond-c",
            depends_on=[upstream["escrow_id"], middle["escrow_id"]],
        )
        key = ctx["requester"]["api_key"]
        root = ctx["root_budget_id"]
        assert _budget(client, auth_header, key, root) == (HOLD * 3, 0)

        refunded = _refund(
            client, auth_header, key, upstream["escrow_id"], "diamond-refund"
        )
        assert refunded.status_code == 200, refunded.text
        assert (
            _detail(client, auth_header, key, both["escrow_id"])["status"] == "refunded"
        )
        assert _budget(client, auth_header, key, root) == (0, 0)
        assert _balance(client, auth_header, key)["available"] == 100
        assert _balance(client, auth_header, key)["held_in_escrow"] == 0
        refunds = [
            tx
            for tx in _txns(client, auth_header, key)
            if tx["type"] == "escrow_refund"
        ]
        assert len(refunds) == 3


def test_dependent_refund_crash_rolls_back_then_retry_settles_once(
    exchange_app, auth_header
):
    with TestClient(exchange_app) as client:
        ctx = _bootstrap(
            client, auth_header, acceptance_required=False, limit_units=HOLD * 2
        )
        provider_b = _register(client, "ProviderDep", ["analysis"])
        upstream = _open(
            client, auth_header, ctx, ctx["provider"]["account"]["id"], "dep-a"
        )
        child = _open(
            client,
            auth_header,
            ctx,
            provider_b["account"]["id"],
            "dep-b",
            depends_on=[upstream["escrow_id"]],
        )
        key = ctx["requester"]["api_key"]
        root = ctx["root_budget_id"]
        import exchange.routes.settlement as settlement_mod

        calls = {"n": 0}
        real = settlement_mod.release_reservation

        def boom(session, escrow, units):
            calls["n"] += 1
            real(session, escrow, units)
            if calls["n"] >= 2:
                raise _Crash("crash before commit")

        with patch.object(settlement_mod, "release_reservation", boom):
            with pytest.raises(_Crash):
                _refund(client, auth_header, key, upstream["escrow_id"], "dep-crash")

        assert calls["n"] >= 2
        assert (
            _detail(client, auth_header, key, upstream["escrow_id"])["status"] == "held"
        )
        assert _detail(client, auth_header, key, child["escrow_id"])["status"] == "held"
        assert _budget(client, auth_header, key, root) == (HOLD * 2, 0)
        assert _balance(client, auth_header, key)["available"] == 100 - HOLD * 2
        assert _balance(client, auth_header, key)["held_in_escrow"] == HOLD * 2

        again = _refund(client, auth_header, key, upstream["escrow_id"], "dep-retry")
        assert again.status_code == 200, again.text
        assert (
            _detail(client, auth_header, key, child["escrow_id"])["status"]
            == "refunded"
        )
        assert _budget(client, auth_header, key, root) == (0, 0)
        assert _balance(client, auth_header, key)["available"] == 100
        replay = _refund(client, auth_header, key, upstream["escrow_id"], "dep-retry")
        assert replay.status_code == 200, replay.text
        assert _budget(client, auth_header, key, root) == (0, 0)
        assert (
            len(
                [
                    tx
                    for tx in _txns(client, auth_header, key)
                    if tx["type"] == "escrow_refund"
                ]
            )
            == 2
        )


def test_workflow_locked_past_ttl_keeps_status_and_reservation(
    exchange_app, auth_header
):
    with TestClient(exchange_app) as client:
        ctx = _bootstrap(
            client, auth_header, acceptance_required=False, limit_units=HOLD
        )
        created = _open(
            client,
            auth_header,
            ctx,
            ctx["provider"]["account"]["id"],
            "hold-locked",
            ttl_minutes=5,
            workflow_locked=True,
            cancel_secret_hash=CANCEL_HASH,
        )
        key = ctx["requester"]["api_key"]
        moment = datetime.now(timezone.utc) + timedelta(minutes=10)
        assert _sweep(moment)["expired_held"] == 0
        assert _sweep(moment)["expired_held"] == 0
        detail = _detail(client, auth_header, key, created["escrow_id"])
        assert detail["status"] == "held"
        assert detail["workflow_locked"] is True
        assert detail["resolved_at"] is None
        assert _budget(client, auth_header, key, ctx["root_budget_id"]) == (HOLD, 0)
        bal = _balance(client, auth_header, key)
        assert bal["available"] == 100 - HOLD
        assert bal["held_in_escrow"] == HOLD


def test_unbound_escrow_does_not_change_root_budget(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        operator = _register(client, "OperatorBot", ["ops"])
        requester = _register(client, "RequesterBot", ["orchestration"])
        provider = _register(client, "ProviderBot", ["analysis"])
        from tests.test_dispute import _promote_to_operator

        _promote_to_operator("OperatorBot")
        budget = client.post(
            "/v1/exchange/assurance/root-budgets",
            headers=auth_header(operator["api_key"]),
            json={
                "principal_account_id": requester["account"]["id"],
                "limit_units": 100,
            },
        )
        assert budget.status_code == 200, budget.text
        root = budget.json()["root_budget_id"]
        created = client.post(
            "/v1/exchange/escrow",
            headers=auth_header(requester["api_key"]),
            json={
                "provider_id": provider["account"]["id"],
                "amount": AMOUNT,
                "ttl_minutes": 5,
            },
        )
        assert created.status_code == 201, created.text
        assert _budget(client, auth_header, requester["api_key"], root) == (0, 0)

        from exchange.config import SessionLocal
        from exchange.models import Escrow

        session = SessionLocal()
        try:
            with session.begin():
                row = session.get(Escrow, created.json()["escrow_id"])
                assert row is not None
                assert row.root_budget_id is None
        finally:
            session.close()

        moment = datetime.now(timezone.utc) + timedelta(minutes=10)
        assert _sweep(moment)["expired_held"] == 1
        assert _sweep(moment)["expired_held"] == 0
        assert (
            _detail(
                client, auth_header, requester["api_key"], created.json()["escrow_id"]
            )["status"]
            == "expired"
        )
        assert _budget(client, auth_header, requester["api_key"], root) == (0, 0)
        assert _balance(client, auth_header, requester["api_key"])["available"] == 100


def test_unbound_expiry_snapshot_matches_current_settlement(exchange_app, auth_header):
    """Balances, transactions, and events for an unbound expiry stay as they are."""
    with TestClient(exchange_app) as client:
        requester = _register(client, "RequesterBot", ["orchestration"])
        provider = _register(client, "ProviderBot", ["analysis"])
        requester_key = requester["api_key"]
        provider_key = provider["api_key"]
        requester_id = requester["account"]["id"]
        provider_id = provider["account"]["id"]

        import exchange.routes.settlement as settlement_mod
        import exchange.tasks as tasks_mod
        import exchange.webhooks as webhooks_mod

        events: list[dict] = []
        real_webhook = webhooks_mod.fire_webhook_event

        def spy(session, escrow, event):
            events.append(
                {
                    "event": event,
                    "status": escrow.status,
                    "amount": int(escrow.amount),
                    "fee_amount": int(escrow.fee_amount),
                }
            )
            return real_webhook(session, escrow, event)

        with (
            patch.object(settlement_mod, "fire_webhook_event", spy),
            patch.object(tasks_mod, "fire_webhook_event", spy),
        ):
            created = client.post(
                "/v1/exchange/escrow",
                headers=auth_header(requester_key),
                json={"provider_id": provider_id, "amount": AMOUNT, "ttl_minutes": 5},
            )
            assert created.status_code == 201, created.text
            eid = created.json()["escrow_id"]
            moment = datetime.now(timezone.utc) + timedelta(minutes=10)
            assert _sweep(moment)["expired_held"] == 1
            assert _sweep(moment)["expired_held"] == 0

        detail = _detail(client, auth_header, requester_key, eid)
        assert detail["status"] == "expired"
        assert detail["amount"] == AMOUNT
        assert detail["fee_amount"] == FEE
        assert detail["resolved_at"] is not None
        assert detail["workflow_locked"] is False

        for key in (requester_key, provider_key):
            bal = _balance(client, auth_header, key)
            assert bal["available"] == 100
            assert bal["held_in_escrow"] == 0
            assert bal["total_earned"] == 0
            assert bal["total_spent"] == 0

        def _rows(key):
            return {
                (
                    tx["type"],
                    tx["amount"],
                    tx["description"],
                    tx["from_account"],
                    tx["to_account"],
                )
                for tx in _txns(client, auth_header, key)
            }

        assert _rows(requester_key) == {
            (
                "mint",
                100,
                "Starter token allocation on registration",
                None,
                requester_id,
            ),
            ("escrow_hold", HOLD, "Escrow for task: unspecified", requester_id, None),
            ("escrow_refund", HOLD, "Auto-expired: TTL exceeded", None, requester_id),
        }
        assert _rows(provider_key) == {
            (
                "mint",
                100,
                "Starter token allocation on registration",
                None,
                provider_id,
            ),
        }
        assert events == [
            {
                "event": "escrow.created",
                "status": "held",
                "amount": AMOUNT,
                "fee_amount": FEE,
            },
            {
                "event": "escrow.expired",
                "status": "expired",
                "amount": AMOUNT,
                "fee_amount": FEE,
            },
        ]
        attestations = _attestations(client, eid)
        assert [
            {
                "schema_id": item["schema_id"],
                "escrow_status": item["payload"]["mediation"]["escrow_status"],
                "dispute_reason": item["payload"]["mediation"].get("dispute_reason"),
                "resolution_strategy": item["payload"]["mediation"].get(
                    "resolution_strategy"
                ),
            }
            for item in attestations
        ] == [
            {
                "schema_id": "urn:a2a-se:pre-dispute-attestation:v1",
                "escrow_status": "held",
                "dispute_reason": None,
                "resolution_strategy": None,
            },
            {
                "schema_id": "urn:a2a-se:pre-dispute-attestation:v1",
                "escrow_status": "expired",
                "dispute_reason": None,
                "resolution_strategy": None,
            },
        ]


def _normalize_pg_url(url: str) -> str:
    if url.startswith(("postgresql+psycopg://", "postgresql+psycopg2://")):
        return url
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def _reload_app():
    import exchange.app as app_mod
    import exchange.compliance_log as compliance_log_mod
    import exchange.config as config_mod
    import exchange.observers as observers_mod
    import exchange.ratelimit as ratelimit_mod
    import exchange.reputation_metrics as reputation_metrics_mod
    import exchange.routes.accounts as accounts_mod
    import exchange.routes.kya_admin as kya_admin_mod
    import exchange.routes.reputation as reputation_mod
    import exchange.routes.settlement as settlement_mod
    import exchange.routes.webhooks as webhooks_routes_mod
    import exchange.spending_guard as spending_guard_mod
    import exchange.tasks as tasks_mod
    import exchange.trust_tiers as trust_tiers_mod
    import exchange.webhooks as webhooks_mod

    importlib.reload(config_mod)
    importlib.reload(compliance_log_mod)
    importlib.reload(ratelimit_mod)
    importlib.reload(observers_mod)
    importlib.reload(spending_guard_mod)
    importlib.reload(tasks_mod)
    importlib.reload(webhooks_mod)
    importlib.reload(reputation_metrics_mod)
    importlib.reload(trust_tiers_mod)
    importlib.reload(accounts_mod)
    importlib.reload(reputation_mod)
    importlib.reload(settlement_mod)
    importlib.reload(webhooks_routes_mod)
    importlib.reload(kya_admin_mod)
    importlib.reload(app_mod)
    return app_mod.create_app()


def _bound_unique(client, auth_header, limit_units: int) -> dict:
    """Bound requester with names that can share a long-lived Postgres."""
    from tests.test_acceptance_binding import CRITERIA
    from tests.test_dispute import _promote_to_operator

    tag = uuid.uuid4().hex[:10]
    operator = _register(client, f"Op{tag}", ["ops"])
    requester = _register(client, f"Req{tag}", ["orchestration"])
    provider = _register(client, f"Prov{tag}", ["analysis"])
    approver = _register(client, f"Appr{tag}", ["review"])
    _promote_to_operator(f"Op{tag}")
    op_key = operator["api_key"]
    budget = client.post(
        "/v1/exchange/assurance/root-budgets",
        headers=auth_header(op_key),
        json={
            "principal_account_id": requester["account"]["id"],
            "limit_units": limit_units,
        },
    )
    assert budget.status_code == 200, budget.text
    policy = client.post(
        "/v1/exchange/assurance/policies",
        headers=auth_header(op_key),
        json={
            "principal_account_id": requester["account"]["id"],
            "task_id": TASK,
            "criteria_hash": CRITERIA,
            "authorized_approver_id": approver["account"]["id"],
            "root_budget_id": budget.json()["root_budget_id"],
            "acceptance_required": False,
        },
    )
    assert policy.status_code == 200, policy.text
    bound = client.post(
        "/v1/exchange/assurance/bind-account",
        headers=auth_header(op_key),
        json={
            "account_id": requester["account"]["id"],
            "policy_id": policy.json()["policy_id"],
        },
    )
    assert bound.status_code == 200, bound.text
    return {
        "requester": requester,
        "provider": provider,
        "root_budget_id": budget.json()["root_budget_id"],
    }


@pytest.mark.skipif(
    not os.environ.get("EXCHANGE_PG_URL"),
    reason="EXCHANGE_PG_URL is not set",
)
def test_postgres_concurrent_sweeps_settle_reservation_once(
    tmp_path, monkeypatch, auth_header
):
    """Two sweeps on separate sessions settle one held reservation once."""
    pg_url = _normalize_pg_url(os.environ["EXCHANGE_PG_URL"])
    suffix = uuid.uuid4().hex[:8]
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("A2A_EXCHANGE_DATABASE_URL", pg_url)
    monkeypatch.setenv("A2A_EXCHANGE_AUTO_CREATE_SCHEMA", "true")
    monkeypatch.setenv("A2A_EXCHANGE_STARTER_TOKENS", "100")
    monkeypatch.setenv("A2A_EXCHANGE_FEE_PERCENT", "0.25")
    monkeypatch.setenv("A2A_EXCHANGE_REGISTER_RATE_LIMIT_HOUR", "0")
    monkeypatch.setenv("A2A_EXCHANGE_REGISTER_RATE_LIMIT_DAY", "0")
    monkeypatch.setenv("A2A_EXCHANGE_INVITE_CODE", "")
    monkeypatch.setenv("A2A_EXCHANGE_COMPLIANCE_ENABLED", "true")
    monkeypatch.setenv(
        "A2A_EXCHANGE_COMPLIANCE_DB_PATH", str(tmp_path / "compliance_merkle.db")
    )
    monkeypatch.setenv("A2A_EXCHANGE_EXPIRY_INTERVAL_SECONDS", "86400")
    app = _reload_app()
    try:
        with TestClient(app) as client:
            ctx = _bound_unique(client, auth_header, HOLD)
            created = _open(
                client,
                auth_header,
                ctx,
                ctx["provider"]["account"]["id"],
                f"pg-hold-{suffix}",
                ttl_minutes=5,
            )
            key = ctx["requester"]["api_key"]
            root = ctx["root_budget_id"]
            assert _budget(client, auth_header, key, root) == (HOLD, 0)

            from exchange.observers import PaymentTimeoutObserver

            gate = threading.Barrier(2)
            real = PaymentTimeoutObserver.expire_stale_held
            errors: list[BaseException] = []
            counts: list[dict] = []
            moment = datetime.now(timezone.utc) + timedelta(minutes=10)

            def racing(self, session):
                gate.wait(timeout=15)
                return real(self, session)

            def run() -> None:
                from exchange.tasks import run_expiry_sweep

                try:
                    counts.append(run_expiry_sweep())
                except BaseException as exc:  # noqa: BLE001 - reported below
                    errors.append(exc)

            with (
                patch("exchange.observers._now", return_value=moment),
                patch.object(PaymentTimeoutObserver, "expire_stale_held", racing),
            ):
                threads = [threading.Thread(target=run) for _ in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=30)
                assert not any(thread.is_alive() for thread in threads)

            assert errors == []
            assert sum(item["expired_held"] for item in counts) == 1
            assert (
                _detail(client, auth_header, key, created["escrow_id"])["status"]
                == "expired"
            )
            assert _budget(client, auth_header, key, root) == (0, 0)
            bal = _balance(client, auth_header, key)
            assert bal["available"] == 100
            assert bal["held_in_escrow"] == 0
            refunds = [
                tx
                for tx in _txns(client, auth_header, key)
                if tx["type"] == "escrow_refund"
            ]
            assert len(refunds) == 1
            assert refunds[0]["amount"] == HOLD
    finally:
        import exchange.config as config_mod

        config_mod.engine.dispose()
        restore = f"sqlite:///{tmp_path / 'restore.db'}"
        monkeypatch.setenv("DATABASE_URL", restore)
        monkeypatch.setenv("A2A_EXCHANGE_DATABASE_URL", restore)
        _reload_app()
