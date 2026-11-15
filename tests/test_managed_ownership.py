from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib
import itertools
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_REG = {
    "developer_name": "Test Dev",
    "contact_email": "test@test.dev",
}

SHARED_DEV = "dev-shared-ownership"
OTHER_DEV = "dev-other-ownership"
SECOND_DEV = "dev-second-ownership"


def _sample_card(name: str = "ManagedAgent", agent_id: str = "did:web:agent.test") -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "protocol_version": "2026.1",
        "name": name,
        "id": agent_id,
        "description": "Managed agent card",
        "kya_level": 0,
        "identity": {"type": "api_key"},
        "attestations": [],
        "settlement": {
            "supported_methods": ["escrow-v1"],
            "exchange_url": "https://exchange.test",
        },
        "capabilities": {"skills": ["research", "analysis"]},
        "policies": {},
        "metadata": {"created": now, "updated": now},
    }


_CREATED_BASE = datetime(2020, 1, 1, tzinfo=timezone.utc)
_created_seq = itertools.count(1)


def _set_created_at(account_id: str, when: datetime) -> None:
    from sqlalchemy import select

    from exchange.config import get_session
    from exchange.models import Account

    session = next(get_session())
    try:
        with session.begin():
            acct = session.execute(
                select(Account).where(Account.id == account_id)
            ).scalar_one()
            acct.created_at = when
    finally:
        session.close()


def _register(client: TestClient, bot_name: str, developer_id: str, **extra) -> dict:
    """Register and stamp a strictly increasing created_at.

    SQLite CURRENT_TIMESTAMP has one-second resolution, so registration order
    would otherwise tie and "oldest agent" would depend on random account ids.
    """
    payload = {
        "bot_name": bot_name,
        "developer_id": developer_id,
        **_REG,
        **extra,
    }
    resp = client.post("/v1/accounts/register", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    _set_created_at(
        body["account"]["id"], _CREATED_BASE + timedelta(seconds=next(_created_seq))
    )
    return body


def _db_session():
    from exchange.config import get_session

    return next(get_session())


def _owner_row(developer_id: str) -> tuple[str, str | None] | None:
    from sqlalchemy import select

    from exchange.models import DeveloperIdOwner

    session = _db_session()
    try:
        with session.begin():
            row = session.execute(
                select(DeveloperIdOwner).where(
                    DeveloperIdOwner.developer_id == developer_id
                )
            ).scalar_one_or_none()
            return None if row is None else (row.status, row.owner_account_id)
    finally:
        session.close()


def _claim_via_list(client: TestClient, api_key: str) -> dict:
    resp = client.get(
        "/v1/accounts/managed",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture()
def client(exchange_app):
    with TestClient(exchange_app) as c:
        yield c


def test_owner_manages_siblings_after_claim(client: TestClient):
    owner = _register(client, "OwnerBot", SHARED_DEV)
    sibling = _register(
        client, "SiblingBot", SHARED_DEV, is_test=True, owner_api_key=owner["api_key"]
    )

    owner_key = owner["api_key"]
    sibling_id = sibling["account"]["id"]

    body = _claim_via_list(client, owner_key)
    assert body["developer_id"] == SHARED_DEV
    assert body["count"] >= 2
    ids = {b["id"] for b in body["bots"]}
    assert owner["account"]["id"] in ids
    assert sibling_id in ids
    sibling_entry = next(b for b in body["bots"] if b["id"] == sibling_id)
    assert sibling_entry["is_test"] is True

    flat = json.dumps(body)
    assert "api_key" not in flat
    assert "api_key_hash" not in flat

    resp = client.post(
        f"/v1/accounts/{sibling_id}/suspend",
        headers={"Authorization": f"Bearer {owner_key}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "suspended"

    listed = _claim_via_list(client, owner_key)
    sib = next(b for b in listed["bots"] if b["id"] == sibling_id)
    assert sib["status"] == "suspended"

    resp = client.post(
        f"/v1/accounts/{sibling_id}/unsuspend",
        headers={"Authorization": f"Bearer {owner_key}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"

    resp = client.post(
        f"/v1/accounts/{sibling_id}/rotate-key-managed",
        headers={"Authorization": f"Bearer {owner_key}"},
    )
    assert resp.status_code == 200
    rot = resp.json()
    assert rot["api_key"].startswith("ate_")
    assert "grace_period_minutes" in rot


def test_cross_developer_id_forbidden(client: TestClient):
    owner = _register(client, "CrossOwner", SHARED_DEV)
    other = _register(client, "CrossTarget", OTHER_DEV)

    _claim_via_list(client, owner["api_key"])
    target_id = other["account"]["id"]
    hdr = {"Authorization": f"Bearer {owner['api_key']}"}

    for path in ("suspend", "unsuspend", "rotate-key-managed"):
        resp = client.post(f"/v1/accounts/{target_id}/{path}", headers=hdr)
        assert resp.status_code == 403, path


def test_second_claimer_gets_409(client: TestClient):
    first = _register(client, "ClaimFirst", SHARED_DEV)
    second = _register(client, "ClaimSecond", SHARED_DEV, owner_api_key=first["api_key"])

    _claim_via_list(client, first["api_key"])

    resp = client.post(
        "/v1/accounts/developer-ids/claim",
        headers={"Authorization": f"Bearer {second['api_key']}"},
    )
    assert resp.status_code == 409


def test_managed_list_excludes_api_key_fields(client: TestClient):
    reg = _register(client, "ListSafeBot", SHARED_DEV)
    body = _claim_via_list(client, reg["api_key"])
    for bot in body["bots"]:
        assert "api_key" not in bot
        assert "api_key_hash" not in bot


def test_claimed_developer_id_blocks_registration_without_owner_key(client: TestClient):
    owner = _register(client, "GateOwner", SHARED_DEV)
    sibling = _register(client, "GateSibling", SHARED_DEV, owner_api_key=owner["api_key"])
    stranger = _register(client, "GateStranger", OTHER_DEV)
    _claim_via_list(client, owner["api_key"])

    denied = client.post(
        "/v1/accounts/register",
        json={
            "bot_name": "GateBlocked",
            "developer_id": SHARED_DEV,
            **_REG,
        },
    )
    assert denied.status_code == 403

    foreign_key = client.post(
        "/v1/accounts/register",
        json={
            "bot_name": "GateForeignKey",
            "developer_id": SHARED_DEV,
            "owner_api_key": stranger["api_key"],
            **_REG,
        },
    )
    assert foreign_key.status_code == 403

    sibling_key = client.post(
        "/v1/accounts/register",
        json={
            "bot_name": "GateSiblingKey",
            "developer_id": SHARED_DEV,
            "owner_api_key": sibling["api_key"],
            **_REG,
        },
    )
    assert sibling_key.status_code == 403

    allowed = client.post(
        "/v1/accounts/register",
        json={
            "bot_name": "GateAllowed",
            "developer_id": SHARED_DEV,
            "owner_api_key": owner["api_key"],
            **_REG,
        },
    )
    assert allowed.status_code == 201


def test_register_agent_claimed_developer_id_blocks_non_owner(client: TestClient):
    """Card registration uses card.id as developer_id and must hit the same gate."""
    claimed_id = "did:web:claimed.owner"
    owner = _register(client, "CardGateOwner", claimed_id)
    stranger = _register(client, "CardGateStranger", "did:web:other.stranger")
    _claim_via_list(client, owner["api_key"])

    fresh = client.post(
        "/v1/accounts/register-agent",
        json=_sample_card(name="CardGateFresh", agent_id="did:web:fresh.unclaimed"),
    )
    assert fresh.status_code == 201, fresh.text

    denied = client.post(
        "/v1/accounts/register-agent",
        json=_sample_card(name="CardGateBlocked", agent_id=claimed_id),
    )
    assert denied.status_code == 403

    foreign = client.post(
        "/v1/accounts/register-agent",
        json={
            **_sample_card(name="CardGateForeign", agent_id=claimed_id),
            "owner_api_key": stranger["api_key"],
        },
    )
    assert foreign.status_code == 403

    allowed = client.post(
        "/v1/accounts/register-agent",
        json={
            **_sample_card(name="CardGateAllowed", agent_id=claimed_id),
            "owner_api_key": owner["api_key"],
        },
        headers={"X-Owner-Api-Key": owner["api_key"]},
    )
    assert allowed.status_code == 201, allowed.text
    account_id = allowed.json()["account"]["id"]
    stored = client.get(f"/v1/accounts/{account_id}/card")
    assert stored.status_code == 200
    flat = stored.text
    assert "owner_api_key" not in flat
    assert owner["api_key"] not in flat


def test_multi_id_attach_and_managed_list(client: TestClient):
    owner = _register(client, "PrimaryOwner", SHARED_DEV)
    ext_a = _register(client, "SecondaryAgentA", SECOND_DEV)
    ext_b = _register(
        client, "SecondaryAgentB", SECOND_DEV, is_test=True, owner_api_key=ext_a["api_key"]
    )

    owner_hdr = {"Authorization": f"Bearer {owner['api_key']}"}

    # Invalid keys rejected with 401 and never echoed
    bad_key = "ate_000000000000000000000000deadbeef"
    bad_resp = client.post(
        "/v1/accounts/developer-ids/attach",
        headers=owner_hdr,
        json={"agent_api_key": bad_key},
    )
    assert bad_resp.status_code == 401
    assert bad_key not in bad_resp.text

    non_ate_key = "secret_not_an_ate_key"
    non_ate_resp = client.post(
        "/v1/accounts/developer-ids/attach",
        headers=owner_hdr,
        json={"agent_api_key": non_ate_key},
    )
    assert non_ate_resp.status_code == 401
    assert non_ate_key not in non_ate_resp.text

    # Valid attach binds SECOND_DEV to owner
    attach_resp = client.post(
        "/v1/accounts/developer-ids/attach",
        headers=owner_hdr,
        json={"agent_api_key": ext_a["api_key"]},
    )
    assert attach_resp.status_code == 200, attach_resp.text
    assert ext_a["api_key"] not in attach_resp.text
    assert attach_resp.json() == {
        "developer_id": SECOND_DEV,
        "owner_account_id": owner["account"]["id"],
        "status": "claimed",
    }

    # Re-attaching with sibling key is idempotent
    reattach = client.post(
        "/v1/accounts/developer-ids/attach",
        headers=owner_hdr,
        json={"agent_api_key": ext_b["api_key"]},
    )
    assert reattach.status_code == 200
    assert reattach.json()["status"] == "claimed"

    # Managed listing returns both developer IDs and agents from both groups
    listed = _claim_via_list(client, owner["api_key"])
    assert listed["developer_id"] == SHARED_DEV
    assert listed["developer_ids"] == sorted([SHARED_DEV, SECOND_DEV])
    assert listed["caller_account_type"] == "agent"
    assert listed["gateway_claimed_agents"] == []
    assert listed["count"] == 3

    by_id = {b["id"]: b for b in listed["bots"]}
    assert set(by_id.keys()) == {
        owner["account"]["id"],
        ext_a["account"]["id"],
        ext_b["account"]["id"],
    }
    for bot in listed["bots"]:
        assert bot["has_agent_card"] is False
        assert bot["last_activity_at"] is not None
        assert bot["gateway_claims"] == []
        assert "api_key" not in bot
        assert "api_key_hash" not in bot
        assert "previous_api_key_hash" not in bot

    flat = json.dumps(listed)
    assert ext_a["api_key"] not in flat
    assert owner["api_key"] not in flat


def test_attach_suspended_agent_key_succeeds(client: TestClient):
    # First owner suspends a sibling, detaches the developer_id, then new owner attaches via suspended agent's key
    first_owner = _register(client, "TempOwner", SHARED_DEV)
    sec_agent = _register(client, "SuspAgent", SECOND_DEV)
    first_hdr = {"Authorization": f"Bearer {first_owner['api_key']}"}

    _claim_via_list(client, first_owner["api_key"])
    r = client.post(
        "/v1/accounts/developer-ids/attach",
        headers=first_hdr,
        json={"agent_api_key": sec_agent["api_key"]},
    )
    assert r.status_code == 200

    susp = client.post(
        f"/v1/accounts/{sec_agent['account']['id']}/suspend",
        headers=first_hdr,
    )
    assert susp.status_code == 200

    det = client.delete(
        f"/v1/accounts/developer-ids/{SECOND_DEV}",
        headers=first_hdr,
    )
    assert det.status_code == 200

    new_owner = _register(client, "NewOwner", OTHER_DEV)
    reclaim = client.post(
        "/v1/accounts/developer-ids/attach",
        headers={"Authorization": f"Bearer {new_owner['api_key']}"},
        json={"agent_api_key": sec_agent["api_key"]},
    )
    assert reclaim.status_code == 200
    assert reclaim.json()["owner_account_id"] == new_owner["account"]["id"]


def test_attach_cross_owner_forbidden(client: TestClient):
    owner_a = _register(client, "ConflictOwnerA", SHARED_DEV)
    sibling_a = _register(
        client, "ConflictSiblingA", SHARED_DEV, owner_api_key=owner_a["api_key"]
    )
    owner_b = _register(client, "ConflictOwnerB", OTHER_DEV)

    _claim_via_list(client, owner_a["api_key"])

    conflict = client.post(
        "/v1/accounts/developer-ids/attach",
        headers={"Authorization": f"Bearer {owner_b['api_key']}"},
        json={"agent_api_key": sibling_a["api_key"]},
    )
    assert conflict.status_code == 403
    assert sibling_a["api_key"] not in conflict.text


def test_unclaimed_and_legacy_open_registration_does_not_auto_claim(client: TestClient):
    """A missing ownership row allows registration; legacy_open needs a member key.

    Registration inserts legacy_open when the row is missing and must not claim
    or rewrite that row. A second registration under the same id is the check.
    """
    fresh = "dev-legacy-open-untouched"
    first = _register(client, "LegacyOpenFirst", fresh)
    second = _register(client, "LegacyOpenSecond", fresh, owner_api_key=first["api_key"])
    assert first["account"]["developer_id"] == fresh
    assert second["account"]["developer_id"] == fresh
    assert _owner_row(fresh) == ("legacy_open", None)


def test_foreign_operator_cannot_register_or_attach_claimed_developer_id(
    client: TestClient,
):
    """Operator B cannot register into A's claimed developer_id or attach it."""
    owner = _register(client, "ProofOwner", SHARED_DEV)
    sibling = _register(client, "ProofSibling", SHARED_DEV, owner_api_key=owner["api_key"])
    outsider = _register(client, "ProofOutsider", OTHER_DEV)
    _claim_via_list(client, owner["api_key"])

    denied = client.post(
        "/v1/accounts/register",
        json={
            "bot_name": "ProofIntruder",
            "developer_id": SHARED_DEV,
            **_REG,
        },
    )
    assert denied.status_code == 403

    denied_with_own_key = client.post(
        "/v1/accounts/register",
        json={
            "bot_name": "ProofIntruderKey",
            "developer_id": SHARED_DEV,
            "owner_api_key": outsider["api_key"],
            **_REG,
        },
    )
    assert denied_with_own_key.status_code == 403

    attach = client.post(
        "/v1/accounts/developer-ids/attach",
        headers={"Authorization": f"Bearer {outsider['api_key']}"},
        json={"agent_api_key": sibling["api_key"]},
    )
    assert attach.status_code == 403
    assert sibling["api_key"] not in attach.text
    assert owner["api_key"] not in attach.text

    allowed = client.post(
        "/v1/accounts/register",
        json={
            "bot_name": "ProofOwnerChild",
            "developer_id": SHARED_DEV,
            "owner_api_key": owner["api_key"],
            **_REG,
        },
    )
    assert allowed.status_code == 201, allowed.text

    reattach = client.post(
        "/v1/accounts/developer-ids/attach",
        headers={"Authorization": f"Bearer {owner['api_key']}"},
        json={"agent_api_key": sibling["api_key"]},
    )
    assert reattach.status_code == 200, reattach.text
    assert reattach.json()["developer_id"] == SHARED_DEV
    assert reattach.json()["owner_account_id"] == owner["account"]["id"]
    assert reattach.json()["status"] == "claimed"
    assert sibling["api_key"] not in reattach.text


def test_detach_developer_id_and_cannot_detach_primary(client: TestClient):
    owner = _register(client, "DetachOwner", SHARED_DEV)
    sec = _register(client, "DetachSecondary", SECOND_DEV)
    outsider = _register(client, "DetachOutsider", OTHER_DEV)

    owner_hdr = {"Authorization": f"Bearer {owner['api_key']}"}
    outsider_hdr = {"Authorization": f"Bearer {outsider['api_key']}"}

    _claim_via_list(client, owner["api_key"])
    attach = client.post(
        "/v1/accounts/developer-ids/attach",
        headers=owner_hdr,
        json={"agent_api_key": sec["api_key"]},
    )
    assert attach.status_code == 200

    # Cross-owner cannot detach
    cross_del = client.delete(
        f"/v1/accounts/developer-ids/{SECOND_DEV}",
        headers=outsider_hdr,
    )
    assert cross_del.status_code == 403

    # Owner cannot detach primary developer_id
    primary_del = client.delete(
        f"/v1/accounts/developer-ids/{SHARED_DEV}",
        headers=owner_hdr,
    )
    assert primary_del.status_code == 400
    assert primary_del.json()["detail"] == "Cannot detach your primary developer ID"

    # Detaching secondary resets to legacy_open and removes from managed listing
    ok_del = client.delete(
        f"/v1/accounts/developer-ids/{SECOND_DEV}",
        headers=owner_hdr,
    )
    assert ok_del.status_code == 200
    assert ok_del.json() == {
        "developer_id": SECOND_DEV,
        "status": "legacy_open",
    }

    listed = _claim_via_list(client, owner["api_key"])
    assert listed["developer_ids"] == [SHARED_DEV]
    assert {b["id"] for b in listed["bots"]} == {owner["account"]["id"]}

    # Owner no longer controls detached agent
    susp = client.post(
        f"/v1/accounts/{sec['account']['id']}/suspend",
        headers=owner_hdr,
    )
    assert susp.status_code == 403


def test_managed_list_when_primary_dev_id_claimed_by_another(client: TestClient):
    first = _register(client, "SharedFirst", SHARED_DEV)
    second = _register(client, "SharedSecond", SHARED_DEV, owner_api_key=first["api_key"])
    extra = _register(client, "ExtraOwned", SECOND_DEV)

    _claim_via_list(client, first["api_key"])

    attach = client.post(
        "/v1/accounts/developer-ids/attach",
        headers={"Authorization": f"Bearer {second['api_key']}"},
        json={"agent_api_key": extra["api_key"]},
    )
    assert attach.status_code == 200

    listed = _claim_via_list(client, second["api_key"])
    assert listed["developer_id"] == SHARED_DEV
    assert listed["developer_ids"] == [SECOND_DEV]
    assert {b["id"] for b in listed["bots"]} == {extra["account"]["id"]}


def test_put_card_controller_scoped_and_has_agent_card_flag(client: TestClient):
    owner = _register(client, "CardOwner", SHARED_DEV)
    sibling = _register(client, "CardSibling", SHARED_DEV, owner_api_key=owner["api_key"])
    attached = _register(client, "CardAttached", SECOND_DEV)
    outsider = _register(client, "CardOutsider", OTHER_DEV)

    owner_hdr = {"Authorization": f"Bearer {owner['api_key']}"}
    outsider_hdr = {"Authorization": f"Bearer {outsider['api_key']}"}

    _claim_via_list(client, owner["api_key"])
    client.post(
        "/v1/accounts/developer-ids/attach",
        headers=owner_hdr,
        json={"agent_api_key": attached["api_key"]},
    )

    # Missing account -> 404
    missing = client.put(
        "/v1/accounts/nonexistent-account/card",
        headers=owner_hdr,
        json=_sample_card("Missing"),
    )
    assert missing.status_code == 404

    # Cross-owner -> 403
    denied_out = client.put(
        f"/v1/accounts/{outsider['account']['id']}/card",
        headers=owner_hdr,
        json=_sample_card("CardOutsider"),
    )
    assert denied_out.status_code == 403

    denied_in = client.put(
        f"/v1/accounts/{sibling['account']['id']}/card",
        headers=outsider_hdr,
        json=_sample_card("CardSibling"),
    )
    assert denied_in.status_code == 403

    # Owner can publish card on sibling under primary developer_id
    sib_card = _sample_card("CardSibling", "did:web:sibling.test")
    ok_sib = client.put(
        f"/v1/accounts/{sibling['account']['id']}/card",
        headers=owner_hdr,
        json=sib_card,
    )
    assert ok_sib.status_code == 200, ok_sib.text
    assert ok_sib.json()["agent_id"] == sibling["account"]["id"]
    assert ok_sib.json()["card"]["name"] == "CardSibling"

    # Owner can publish card on agent under attached developer_id
    att_card = _sample_card("CardAttached", "did:web:attached.test")
    ok_att = client.put(
        f"/v1/accounts/{attached['account']['id']}/card",
        headers=owner_hdr,
        json=att_card,
    )
    assert ok_att.status_code == 200, ok_att.text

    # Self-update still works without claiming developer_id
    self_ok = client.put(
        f"/v1/accounts/{outsider['account']['id']}/card",
        headers=outsider_hdr,
        json=_sample_card("CardOutsider", "did:web:outsider.test"),
    )
    assert self_ok.status_code == 200

    # Invalid card schema -> 422 for authorized caller
    bad_schema = client.put(
        f"/v1/accounts/{sibling['account']['id']}/card",
        headers=owner_hdr,
        json={"invalid": True},
    )
    assert bad_schema.status_code == 422

    # Managed listing reflects has_agent_card accurately
    listed = _claim_via_list(client, owner["api_key"])
    cards_by_id = {b["id"]: b["has_agent_card"] for b in listed["bots"]}
    assert cards_by_id[owner["account"]["id"]] is False
    assert cards_by_id[sibling["account"]["id"]] is True
    assert cards_by_id[attached["account"]["id"]] is True


def test_get_settlements_controller_scoped_and_excludes_mint(client: TestClient):
    owner = _register(client, "SettleOwner", SHARED_DEV)
    sibling = _register(client, "SettleSibling", SHARED_DEV, owner_api_key=owner["api_key"])
    counterparty = _register(client, "SettleCounterparty", OTHER_DEV)

    owner_hdr = {"Authorization": f"Bearer {owner['api_key']}"}
    sib_hdr = {"Authorization": f"Bearer {sibling['api_key']}"}
    cp_hdr = {"Authorization": f"Bearer {counterparty['api_key']}"}

    _claim_via_list(client, owner["api_key"])

    # 404 on unknown account
    missing = client.get("/v1/accounts/no-such-account/settlements", headers=owner_hdr)
    assert missing.status_code == 404

    # 403 for cross-owner
    forbidden = client.get(
        f"/v1/accounts/{sibling['account']['id']}/settlements",
        headers=cp_hdr,
    )
    assert forbidden.status_code == 403

    forbidden_cp = client.get(
        f"/v1/accounts/{counterparty['account']['id']}/settlements",
        headers=owner_hdr,
    )
    assert forbidden_cp.status_code == 403

    # Initially no non-mint transactions (starter mint is excluded)
    empty = client.get(
        f"/v1/accounts/{sibling['account']['id']}/settlements",
        headers=owner_hdr,
    )
    assert empty.status_code == 200
    assert empty.json() == {
        "account_id": sibling["account"]["id"],
        "settlements": [],
    }

    # Outbound escrow from sibling -> counterparty, released
    esc_out = client.post(
        "/v1/exchange/escrow",
        headers=sib_hdr,
        json={"provider_id": counterparty["account"]["id"], "amount": 20},
    )
    assert esc_out.status_code == 201, esc_out.text
    esc_out_id = esc_out.json()["escrow_id"]
    rel_out = client.post(
        "/v1/exchange/release",
        headers=sib_hdr,
        json={"escrow_id": esc_out_id},
    )
    assert rel_out.status_code == 200, rel_out.text

    # Inbound escrow from counterparty -> sibling, released
    esc_in = client.post(
        "/v1/exchange/escrow",
        headers=cp_hdr,
        json={"provider_id": sibling["account"]["id"], "amount": 15},
    )
    assert esc_in.status_code == 201, esc_in.text
    esc_in_id = esc_in.json()["escrow_id"]
    rel_in = client.post(
        "/v1/exchange/release",
        headers=cp_hdr,
        json={"escrow_id": esc_in_id},
    )
    assert rel_in.status_code == 200, rel_in.text

    # Owner and self both see non-mint settlements with direction & escrow_id
    owner_view = client.get(
        f"/v1/accounts/{sibling['account']['id']}/settlements",
        headers=owner_hdr,
    )
    assert owner_view.status_code == 200
    body = owner_view.json()
    assert body["account_id"] == sibling["account"]["id"]
    items = body["settlements"]
    assert len(items) >= 2
    assert len(items) <= 10
    assert all(item["tx_type"] != "mint" for item in items)

    directions = {item["direction"] for item in items}
    assert "in" in directions
    assert "out" in directions
    escrow_ids = {item["escrow_id"] for item in items if item["escrow_id"]}
    assert esc_out_id in escrow_ids
    assert esc_in_id in escrow_ids
    for item in items:
        assert set(item.keys()) == {
            "id",
            "tx_type",
            "amount",
            "direction",
            "escrow_id",
            "description",
            "created_at",
        }
        assert isinstance(item["amount"], int)

    self_view = client.get(
        f"/v1/accounts/{sibling['account']['id']}/settlements",
        headers=sib_hdr,
    )
    assert self_view.status_code == 200
    assert len(self_view.json()["settlements"]) == len(items)


def test_managed_list_includes_gateway_claims_and_gateway_caller_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    db_url = f"sqlite:///{tmp_path / 'gw_managed.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("A2A_EXCHANGE_DATABASE_URL", db_url)
    monkeypatch.setenv("A2A_EXCHANGE_AUTO_CREATE_SCHEMA", "true")
    monkeypatch.setenv("A2A_EXCHANGE_STARTER_TOKENS", "100")
    monkeypatch.setenv("A2A_EXCHANGE_REGISTER_RATE_LIMIT_HOUR", "0")
    monkeypatch.setenv("A2A_EXCHANGE_REGISTER_RATE_LIMIT_DAY", "0")
    monkeypatch.setenv("A2A_EXCHANGE_INVITE_CODE", "")
    monkeypatch.setenv("A2A_EXCHANGE_GATEWAY_INVITE_CODE", "gw-secret")

    import exchange.config as config_mod
    import exchange.ratelimit as ratelimit_mod
    import exchange.auth as auth_mod
    import exchange.ownership as ownership_mod
    import exchange.routes.accounts as accounts_mod
    import exchange.app as app_mod

    importlib.reload(config_mod)
    importlib.reload(ratelimit_mod)
    importlib.reload(auth_mod)
    importlib.reload(ownership_mod)
    importlib.reload(accounts_mod)
    importlib.reload(app_mod)

    with TestClient(app_mod.create_app()) as gw_client:
        owner = _register(gw_client, "GwTestOwner", SHARED_DEV)
        agent_v = _register(
            gw_client, "GwVerifiedAgent", SHARED_DEV, owner_api_key=owner["api_key"]
        )
        agent_s = _register(
            gw_client, "GwSoftAgent", SHARED_DEV, owner_api_key=owner["api_key"]
        )

        gw_resp = gw_client.post(
            "/v1/accounts/register",
            json={
                "bot_name": "ProdGateway",
                "developer_id": "dev-gateway-op",
                "account_type": "gateway",
                "invite_code": "gw-secret",
                **_REG,
            },
        )
        assert gw_resp.status_code == 201, gw_resp.text
        gw = gw_resp.json()
        gw_hdr = {"Authorization": f"Bearer {gw['api_key']}"}

        # Verified claim on agent_v, soft claim on agent_s
        r1 = gw_client.post(
            f"/v1/accounts/{agent_v['account']['id']}/claim",
            headers=gw_hdr,
            json={"agent_api_key": agent_v["api_key"]},
        )
        assert r1.status_code == 201
        r2 = gw_client.post(
            f"/v1/accounts/{agent_s['account']['id']}/claim",
            headers=gw_hdr,
            json={},
        )
        assert r2.status_code == 201

        # Owner sees both verified and soft claims on their managed agents
        owner_list = _claim_via_list(gw_client, owner["api_key"])
        assert owner_list["caller_account_type"] == "agent"
        assert owner_list["gateway_claimed_agents"] == []
        bots_by_id = {b["id"]: b for b in owner_list["bots"]}

        v_claims = bots_by_id[agent_v["account"]["id"]]["gateway_claims"]
        assert len(v_claims) == 1
        assert v_claims[0]["gateway_id"] == gw["account"]["id"]
        assert v_claims[0]["gateway_name"] == "ProdGateway"
        assert v_claims[0]["verified"] is True

        s_claims = bots_by_id[agent_s["account"]["id"]]["gateway_claims"]
        assert len(s_claims) == 1
        assert s_claims[0]["gateway_name"] == "ProdGateway"
        assert s_claims[0]["verified"] is False

        # Gateway caller gets caller_account_type == "gateway" and gateway_claimed_agents
        gw_list = _claim_via_list(gw_client, gw["api_key"])
        assert gw_list["caller_account_type"] == "gateway"
        assert gw_list["developer_ids"] == ["dev-gateway-op"]
        assert gw_list["bots"] == []
        assert gw_list["count"] == 0
        claimed_ids = {a["id"] for a in gw_list["gateway_claimed_agents"]}
        assert claimed_ids == {agent_v["account"]["id"], agent_s["account"]["id"]}
        flat_gw = json.dumps(gw_list)
        assert "api_key" not in flat_gw

# --- legacy_open: oldest-agent claim and member-key registration ---

LEGACY_DEV = "dev-legacy-open-rules"
LEGACY_DID = "did:web:legacy.open.rules"


def _delete_owner_row(developer_id: str) -> None:
    from sqlalchemy import delete

    from exchange.models import DeveloperIdOwner

    session = _db_session()
    try:
        with session.begin():
            session.execute(
                delete(DeveloperIdOwner).where(
                    DeveloperIdOwner.developer_id == developer_id
                )
            )
    finally:
        session.close()


def _insert_legacy_open_row(developer_id: str) -> None:
    from exchange.models import DeveloperIdOwner

    session = _db_session()
    try:
        with session.begin():
            session.add(DeveloperIdOwner(developer_id=developer_id, status="legacy_open"))
    finally:
        session.close()


def test_legacy_open_claim_requires_oldest_agent_key(client: TestClient):
    oldest = _register(client, "LegacyOldest", LEGACY_DEV)
    newer = _register(client, "LegacyNewer", LEGACY_DEV, owner_api_key=oldest["api_key"])
    outsider = _register(client, "LegacyOutsider", OTHER_DEV)
    assert _owner_row(LEGACY_DEV) == ("legacy_open", None)

    claim = client.post(
        "/v1/accounts/developer-ids/claim",
        headers={"Authorization": f"Bearer {newer['api_key']}"},
    )
    assert claim.status_code == 403
    assert "oldest" in claim.json()["detail"]

    listed = _claim_via_list(client, newer["api_key"])
    assert listed["developer_ids"] == []
    assert listed["bots"] == []
    assert _owner_row(LEGACY_DEV) == ("legacy_open", None)

    attach = client.post(
        "/v1/accounts/developer-ids/attach",
        headers={"Authorization": f"Bearer {outsider['api_key']}"},
        json={"agent_api_key": newer["api_key"]},
    )
    assert attach.status_code == 403
    assert "oldest" in attach.json()["detail"]
    assert newer["api_key"] not in attach.text
    assert _owner_row(LEGACY_DEV) == ("legacy_open", None)

    ok = client.post(
        "/v1/accounts/developer-ids/claim",
        headers={"Authorization": f"Bearer {oldest['api_key']}"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json() == {
        "developer_id": LEGACY_DEV,
        "owner_account_id": oldest["account"]["id"],
        "status": "claimed",
    }

    second = client.post(
        "/v1/accounts/developer-ids/claim",
        headers={"Authorization": f"Bearer {newer['api_key']}"},
    )
    assert second.status_code == 409


def test_legacy_open_attach_with_oldest_key_succeeds(client: TestClient):
    oldest = _register(client, "AttachOldest", LEGACY_DEV)
    _register(client, "AttachNewer", LEGACY_DEV, owner_api_key=oldest["api_key"])
    operator = _register(client, "AttachOperator", OTHER_DEV)

    resp = client.post(
        "/v1/accounts/developer-ids/attach",
        headers={"Authorization": f"Bearer {operator['api_key']}"},
        json={"agent_api_key": oldest["api_key"]},
    )
    assert resp.status_code == 200, resp.text
    assert oldest["api_key"] not in resp.text
    assert resp.json() == {
        "developer_id": LEGACY_DEV,
        "owner_account_id": operator["account"]["id"],
        "status": "claimed",
    }


def test_legacy_open_oldest_tie_broken_by_account_id(client: TestClient):
    a = _register(client, "TieA", LEGACY_DEV)
    b = _register(client, "TieB", LEGACY_DEV, owner_api_key=a["api_key"])
    same = _CREATED_BASE
    _set_created_at(a["account"]["id"], same)
    _set_created_at(b["account"]["id"], same)
    first, second = sorted([a, b], key=lambda r: r["account"]["id"])

    denied = client.post(
        "/v1/accounts/developer-ids/claim",
        headers={"Authorization": f"Bearer {second['api_key']}"},
    )
    assert denied.status_code == 403
    ok = client.post(
        "/v1/accounts/developer-ids/claim",
        headers={"Authorization": f"Bearer {first['api_key']}"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["owner_account_id"] == first["account"]["id"]


def test_legacy_open_registration_requires_member_key_on_both_paths(client: TestClient):
    oldest = _register(client, "MemberOldest", LEGACY_DID)
    member = _register(client, "MemberNewer", LEGACY_DID, owner_api_key=oldest["api_key"])
    outsider = _register(client, "MemberOutsider", OTHER_DEV)

    def reg(name: str, **extra):
        return client.post(
            "/v1/accounts/register",
            json={"bot_name": name, "developer_id": LEGACY_DID, **_REG, **extra},
        )

    no_key = reg("RegNoKey")
    assert no_key.status_code == 403
    assert "already has agents" in no_key.json()["detail"]
    assert reg("RegOutsiderKey", owner_api_key=outsider["api_key"]).status_code == 403
    assert reg("RegNotAKey", owner_api_key="not_an_ate_key").status_code == 403

    card_no_key = client.post(
        "/v1/accounts/register-agent",
        json=_sample_card(name="CardNoKey", agent_id=LEGACY_DID),
    )
    assert card_no_key.status_code == 403
    card_outsider_body = client.post(
        "/v1/accounts/register-agent",
        json={
            **_sample_card(name="CardOutsiderBody", agent_id=LEGACY_DID),
            "owner_api_key": outsider["api_key"],
        },
    )
    assert card_outsider_body.status_code == 403
    card_outsider_header = client.post(
        "/v1/accounts/register-agent",
        json=_sample_card(name="CardOutsiderHeader", agent_id=LEGACY_DID),
        headers={"X-Owner-Api-Key": outsider["api_key"]},
    )
    assert card_outsider_header.status_code == 403

    allowed = reg("RegMemberKey", owner_api_key=member["api_key"])
    assert allowed.status_code == 201, allowed.text
    assert _owner_row(LEGACY_DID) == ("legacy_open", None)

    card_allowed = client.post(
        "/v1/accounts/register-agent",
        json=_sample_card(name="CardMemberKey", agent_id=LEGACY_DID),
        headers={"X-Owner-Api-Key": member["api_key"]},
    )
    assert card_allowed.status_code == 201, card_allowed.text
    assert _owner_row(LEGACY_DID) == ("legacy_open", None)

    card_body_key = client.post(
        "/v1/accounts/register-agent",
        json={
            **_sample_card(name="CardMemberBodyKey", agent_id=LEGACY_DID),
            "owner_api_key": oldest["api_key"],
        },
    )
    assert card_body_key.status_code == 201, card_body_key.text
    stored = client.get(f"/v1/accounts/{card_body_key.json()['account']['id']}/card")
    assert stored.status_code == 200
    assert "owner_api_key" not in stored.text
    assert oldest["api_key"] not in stored.text
    assert _owner_row(LEGACY_DID) == ("legacy_open", None)


def test_empty_or_unknown_developer_id_stays_open(client: TestClient):
    unknown = "dev-never-seen-before"
    assert _owner_row(unknown) is None
    first = client.post(
        "/v1/accounts/register",
        json={"bot_name": "UnknownFirst", "developer_id": unknown, **_REG},
    )
    assert first.status_code == 201, first.text
    assert _owner_row(unknown) == ("legacy_open", None)

    card = client.post(
        "/v1/accounts/register-agent",
        json=_sample_card(name="UnknownCard", agent_id="did:web:never.seen"),
    )
    assert card.status_code == 201, card.text

    empty = "dev-legacy-open-empty"
    _insert_legacy_open_row(empty)
    empty_reg = client.post(
        "/v1/accounts/register",
        json={"bot_name": "EmptyFirst", "developer_id": empty, **_REG},
    )
    assert empty_reg.status_code == 201, empty_reg.text
    assert _owner_row(empty) == ("legacy_open", None)


def test_missing_owner_row_keeps_first_claimer_wins(client: TestClient):
    a = _register(client, "MissingRowA", LEGACY_DEV)
    b = _register(client, "MissingRowB", LEGACY_DEV, owner_api_key=a["api_key"])
    _delete_owner_row(LEGACY_DEV)

    resp = client.post(
        "/v1/accounts/developer-ids/claim",
        headers={"Authorization": f"Bearer {b['api_key']}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["owner_account_id"] == b["account"]["id"]
