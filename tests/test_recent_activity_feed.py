"""GET /v1/stats/recent-activity shows parties and amounts to operators only.

The feed is public. Without operator credentials an entry says when an escrow
last changed and how it stands, and nothing about who is in it, how much it
holds or which task it pays for.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.test_escrow_self_dealing_visibility import _world

FEED = "/v1/stats/recent-activity"
PUBLIC_KEYS = {"timestamp", "outcome", "status"}
OPERATOR_KEYS = PUBLIC_KEYS | {
    "id",
    "source_agent",
    "target_agent",
    "escrow_id",
    "amount",
    "task_id",
}


def test_anonymous_caller_gets_redacted_entries(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        world = _world(client, auth_header)
        resp = client.get(FEED)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        entry = body["entries"][0]
        assert set(entry) == PUBLIC_KEYS
        assert entry["status"] == "held"
        assert entry["outcome"] == "pending"
        assert world["escrow_id"] not in resp.text
        for name in ("RequesterBot", "ProviderBot"):
            assert name not in resp.text


@pytest.mark.parametrize("caller", ["requester_key", "provider_key", "outsider_key"])
def test_non_operator_account_gets_redacted_entries(exchange_app, auth_header, caller):
    """Even a party to the escrow gets the public shape from the feed."""
    with TestClient(exchange_app) as client:
        world = _world(client, auth_header)
        resp = client.get(FEED, headers=auth_header(world[caller]))
        assert resp.status_code == 200, resp.text
        assert set(resp.json()["entries"][0]) == PUBLIC_KEYS
        assert world["escrow_id"] not in resp.text


def test_operator_gets_full_entries(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        world = _world(client, auth_header)
        resp = client.get(FEED, headers=auth_header(world["operator_key"]))
        assert resp.status_code == 200, resp.text
        entry = resp.json()["entries"][0]
        assert set(entry) == OPERATOR_KEYS
        assert entry["escrow_id"] == world["escrow_id"]
        assert entry["id"] == world["escrow_id"]
        assert entry["source_agent"] == "RequesterBot"
        assert entry["target_agent"] == "ProviderBot"
        assert entry["amount"] == 20


def test_invalid_credentials_are_refused(exchange_app, auth_header):
    """A bad key is an error, not a quiet downgrade to the public view."""
    with TestClient(exchange_app) as client:
        _world(client, auth_header)
        resp = client.get(FEED, headers=auth_header("ate_not_a_real_key"))
        assert resp.status_code == 401
