"""Operator dashboard paths that end a dispute must settle the filer's stake."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from test_timeout_observer import (
    _setup_escrow,
    _stake_return_count,
    _to_disputed,
)

DASHBOARD_KEY = "dashboard-test-key"


@pytest.fixture()
def dashboard_headers(exchange_app):
    # exchange_app reloads the modules, so take the settings the routes hold.
    from exchange.routes.dashboard import settings

    previous = settings.dashboard_api_key
    object.__setattr__(settings, "dashboard_api_key", DASHBOARD_KEY)
    try:
        yield {"Authorization": f"Bearer {DASHBOARD_KEY}"}
    finally:
        object.__setattr__(settings, "dashboard_api_key", previous)


def _disputed_escrow(client, auth_header):
    escrow, requester_key, provider_key, _pid = _setup_escrow(client, auth_header)
    eid = escrow["escrow_id"]
    _to_disputed(client, auth_header, requester_key, provider_key, eid)
    return eid, requester_key


def _assert_stake_returned(client, auth_header, eid, requester_key):
    detail = client.get(
        f"/v1/exchange/escrows/{eid}", headers=auth_header(requester_key)
    ).json()
    assert detail["dispute_stake_status"] == "returned"
    assert _stake_return_count(eid) == 1
    return client.get("/v1/exchange/balance", headers=auth_header(requester_key)).json()


def test_force_refund_of_disputed_escrow_returns_the_stake(
    exchange_app, auth_header, dashboard_headers
):
    with TestClient(exchange_app) as client:
        eid, requester_key = _disputed_escrow(client, auth_header)

        resp = client.post(
            f"/v1/dashboard/escrows/{eid}/force-refund", headers=dashboard_headers
        )
        assert resp.status_code == 200, resp.text

        bal = _assert_stake_returned(client, auth_header, eid, requester_key)
        assert bal["available"] == 100
        assert bal["held_in_escrow"] == 0


def test_force_refund_of_held_escrow_writes_no_stake_return(
    exchange_app, auth_header, dashboard_headers
):
    with TestClient(exchange_app) as client:
        escrow, requester_key, _pk, _pid = _setup_escrow(client, auth_header)
        eid = escrow["escrow_id"]

        resp = client.post(
            f"/v1/dashboard/escrows/{eid}/force-refund", headers=dashboard_headers
        )
        assert resp.status_code == 200, resp.text

        assert _stake_return_count(eid) == 0
        bal = client.get("/v1/exchange/balance", headers=auth_header(requester_key)).json()
        assert bal["available"] == 100
        assert bal["held_in_escrow"] == 0


@pytest.mark.parametrize("resolution", ["refund", "release"])
def test_dashboard_override_returns_the_stake(
    exchange_app, auth_header, dashboard_headers, resolution
):
    with TestClient(exchange_app) as client:
        eid, requester_key = _disputed_escrow(client, auth_header)

        resp = client.post(
            f"/v1/disputes/dispute-{eid}/resolve",
            headers=dashboard_headers,
            json={"resolution": resolution},
        )
        assert resp.status_code == 200, resp.text

        bal = _assert_stake_returned(client, auth_header, eid, requester_key)
        assert bal["held_in_escrow"] == 0
        if resolution == "refund":
            assert bal["available"] == 100
