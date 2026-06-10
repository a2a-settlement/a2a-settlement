"""Map AlgoVoi cross-extension v0 settlement vectors onto A2A-SE exchange behavior."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conformance.algovoi_vectors import (
    A2A_SE_SETTLEMENT_VECTORS,
    get_vector,
    load_artefact,
    map_verdict_to_http,
    verify_vector_jws,
)

ARTEFACT = load_artefact()


@pytest.mark.parametrize("vector_id", [v["vector_id"] for v in ARTEFACT["vectors"]])
def test_vector_jws_verifies(vector_id: str):
    vector = get_vector(ARTEFACT, vector_id)
    ok, detail = verify_vector_jws(vector)
    assert ok, f"{vector_id}: {detail}"


def _register_pair(client: TestClient, auth_header):
    provider = client.post(
        "/v1/accounts/register",
        json={
            "bot_name": "ConfProvider",
            "developer_id": "conf",
            "developer_name": "Conf",
            "contact_email": "conf@test.dev",
            "skills": ["conformance"],
        },
    ).json()
    requester = client.post(
        "/v1/accounts/register",
        json={
            "bot_name": "ConfRequester",
            "developer_id": "conf",
            "developer_name": "Conf",
            "contact_email": "conf2@test.dev",
            "skills": ["conformance"],
        },
    ).json()
    return (
        provider["account"]["id"],
        requester["api_key"],
        provider["api_key"],
    )


def test_escrow_double_release_vector(exchange_app, auth_header):
    """cross-ext-v0-escrow-double-release-001 → second release is BLOCKed."""
    vector = get_vector(ARTEFACT, "cross-ext-v0-escrow-double-release-001")
    assert vector["expected_verdict"] == "BLOCK"
    assert vector["attack_class"] == "escrow_double_release"

    with TestClient(exchange_app) as client:
        provider_id, requester_key, _ = _register_pair(client, auth_header)
        escrow = client.post(
            "/v1/exchange/escrow",
            headers=auth_header(requester_key),
            json={"provider_id": provider_id, "amount": 10},
        ).json()
        escrow_id = escrow["escrow_id"]

        first = client.post(
            "/v1/exchange/release",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow_id},
        )
        assert first.status_code == 200

        second = client.post(
            "/v1/exchange/release",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow_id},
        )
        assert second.status_code == map_verdict_to_http(vector["expected_verdict"])
        assert "already released" in second.json()["detail"].lower()


def test_refund_replay_vector(exchange_app, auth_header):
    """cross-ext-v0-refund-replay-001 → replayed refund on terminal escrow is BLOCKed."""
    vector = get_vector(ARTEFACT, "cross-ext-v0-refund-replay-001")
    assert vector["expected_verdict"] == "BLOCK"
    assert vector["attack_class"] == "refund_replay"

    with TestClient(exchange_app) as client:
        provider_id, requester_key, _ = _register_pair(client, auth_header)

        # Settle escrow A (analogue: esc_PRIOR_settled)
        escrow_a = client.post(
            "/v1/exchange/escrow",
            headers=auth_header(requester_key),
            json={"provider_id": provider_id, "amount": 10},
        ).json()
        client.post(
            "/v1/exchange/refund",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow_a["escrow_id"]},
        )

        # Replay refund against the already-refunded escrow (tampered replay target)
        replay = client.post(
            "/v1/exchange/refund",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow_a["escrow_id"]},
        )
        assert replay.status_code == map_verdict_to_http(vector["expected_verdict"])
        assert "already refunded" in replay.json()["detail"].lower()

        # Separate held escrow B — release path also terminal; refund after release blocked
        escrow_b = client.post(
            "/v1/exchange/escrow",
            headers=auth_header(requester_key),
            json={"provider_id": provider_id, "amount": 10},
        ).json()
        client.post(
            "/v1/exchange/release",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow_b["escrow_id"]},
        )
        cross = client.post(
            "/v1/exchange/refund",
            headers=auth_header(requester_key),
            json={"escrow_id": escrow_b["escrow_id"]},
        )
        assert cross.status_code == 400
        assert "already released" in cross.json()["detail"].lower()


@pytest.mark.parametrize("vector_id", A2A_SE_SETTLEMENT_VECTORS)
def test_settlement_vectors_are_a2a_se_kind(vector_id: str):
    vector = get_vector(ARTEFACT, vector_id)
    assert vector["input_envelope"]["settlement_kind"] == "a2a-se"
