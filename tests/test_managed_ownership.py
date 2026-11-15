from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

_REG = {
    "developer_name": "Test Dev",
    "contact_email": "test@test.dev",
}

SHARED_DEV = "dev-shared-ownership"
OTHER_DEV = "dev-other-ownership"


def _register(client: TestClient, bot_name: str, developer_id: str, **extra) -> dict:
    payload = {
        "bot_name": bot_name,
        "developer_id": developer_id,
        **_REG,
        **extra,
    }
    resp = client.post("/v1/accounts/register", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


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
    sibling = _register(client, "SiblingBot", SHARED_DEV, is_test=True)

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
    second = _register(client, "ClaimSecond", SHARED_DEV)

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