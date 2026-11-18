"""``self_dealing_class`` on GET /v1/exchange/escrows/{id} is operator-only.

The classification is stored on the escrow at creation. Operator callers (the
dispute mediator authenticates as one) read it from the escrow detail. The
requester, the provider and any other account must get a response that does
not contain the key at all.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


# Keys of the escrow detail response before ``self_dealing_class`` was added,
# in serialization order. Non-operator callers must keep getting exactly these.
NON_OPERATOR_KEYS = [
    "id",
    "requester_id",
    "provider_id",
    "amount",
    "fee_amount",
    "effective_fee_percent",
    "status",
    "dispute_reason",
    "dispute_filed_by",
    "dispute_stake_amount",
    "dispute_stake_status",
    "evidence_window_closes_at",
    "resolution_strategy",
    "expires_at",
    "task_id",
    "task_type",
    "group_id",
    "depends_on",
    "deliverables",
    "required_attestation_level",
    "delivered_content",
    "provenance",
    "provenance_result",
    "delivered_at",
    "released_amount",
    "released_fee",
    "holdback_amount",
    "holdback_fee",
    "score",
    "efficacy_check_at",
    "efficacy_criteria",
    "vi_credential_chain",
    "workflow_locked",
    "created_at",
    "resolved_at",
]

FIELD = "self_dealing_class"
STAKE = 10


def _register(client, name: str, developer_id: str) -> dict:
    resp = client.post(
        "/v1/accounts/register",
        json={
            "bot_name": name,
            "developer_id": developer_id,
            "developer_name": "Test Dev",
            "contact_email": "test@test.dev",
            "skills": ["sentiment-analysis"],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _session():
    from exchange.config import get_session

    return next(get_session())


def _promote_to_operator(account_id: str) -> None:
    from exchange.models import Account

    session = _session()
    with session.begin():
        acct = session.get(Account, account_id)
        acct.status = "operator"
        session.add(acct)
    session.close()


def _link_to_shared_principal(agent_ids: list[str], confidence: float) -> None:
    """Link every agent to one new principal at the given confidence."""
    from exchange.models import Principal
    from exchange.principal_resolver import link_agent_to_principal

    session = _session()
    with session.begin():
        principal = Principal(principal_type="unknown", kya_level="none")
        session.add(principal)
        session.flush()
        for agent_id in agent_ids:
            link_agent_to_principal(agent_id, principal.id, "manual", confidence, session)
    session.close()


def _stored_class(escrow_id: str) -> str | None:
    from exchange.models import Escrow

    session = _session()
    with session.begin():
        value = session.get(Escrow, escrow_id).self_dealing_class
    session.close()
    return value


def _clear_class(escrow_id: str) -> None:
    from exchange.models import Escrow

    session = _session()
    with session.begin():
        escrow = session.get(Escrow, escrow_id)
        escrow.self_dealing_class = None
        session.add(escrow)
    session.close()


def _world(client, auth_header, link_confidence: float | None = None) -> dict:
    """Requester, provider, an unrelated account and an operator, plus one escrow.

    Each account has its own developer_id, so the escrow is arms-length unless
    ``link_confidence`` links requester and provider to a shared principal.
    """
    requester = _register(client, "RequesterBot", "dev-requester")
    provider = _register(client, "ProviderBot", "dev-provider")
    outsider = _register(client, "OutsiderBot", "dev-outsider")
    operator = _register(client, "OperatorBot", "dev-operator")
    _promote_to_operator(operator["account"]["id"])

    if link_confidence is not None:
        _link_to_shared_principal(
            [requester["account"]["id"], provider["account"]["id"]], link_confidence
        )

    resp = client.post(
        "/v1/exchange/escrow",
        headers=auth_header(requester["api_key"]),
        json={"provider_id": provider["account"]["id"], "amount": 20},
    )
    assert resp.status_code == 201, resp.text
    return {
        "escrow_id": resp.json()["escrow_id"],
        "requester_key": requester["api_key"],
        "provider_key": provider["api_key"],
        "outsider_key": outsider["api_key"],
        "operator_key": operator["api_key"],
    }


def _detail(client, auth_header, key: str, escrow_id: str):
    resp = client.get(f"/v1/exchange/escrows/{escrow_id}", headers=auth_header(key))
    assert resp.status_code == 200, resp.text
    return resp


@pytest.mark.parametrize(
    ("link_confidence", "expected"),
    [
        (None, "arms_length"),
        (0.6, "suspected_self_dealing"),
        (1.0, "self_dealing"),
    ],
)
def test_operator_sees_stored_class(exchange_app, auth_header, link_confidence, expected):
    with TestClient(exchange_app) as client:
        world = _world(client, auth_header, link_confidence)
        assert _stored_class(world["escrow_id"]) == expected

        body = _detail(client, auth_header, world["operator_key"], world["escrow_id"]).json()

        assert body[FIELD] == expected
        # Top-level key, in addition to everything other callers get.
        assert list(body.keys()) == NON_OPERATOR_KEYS + [FIELD]


def test_operator_sees_null_for_unclassified_escrow(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        world = _world(client, auth_header)
        _clear_class(world["escrow_id"])

        body = _detail(client, auth_header, world["operator_key"], world["escrow_id"]).json()

        assert FIELD in body
        assert body[FIELD] is None


@pytest.mark.parametrize("caller", ["requester_key", "provider_key", "outsider_key"])
@pytest.mark.parametrize("link_confidence", [None, 0.6, 1.0])
def test_non_operator_never_sees_class(exchange_app, auth_header, caller, link_confidence):
    with TestClient(exchange_app) as client:
        world = _world(client, auth_header, link_confidence)

        resp = _detail(client, auth_header, world[caller], world["escrow_id"])

        assert FIELD not in resp.json()
        # Not as a key, not as a value, not under any other name.
        assert "self_dealing" not in resp.text
        assert "arms_length" not in resp.text


@pytest.mark.parametrize("caller", ["requester_key", "provider_key", "outsider_key"])
def test_non_operator_response_shape_is_unchanged(exchange_app, auth_header, caller):
    with TestClient(exchange_app) as client:
        world = _world(client, auth_header, 1.0)

        body = _detail(client, auth_header, world[caller], world["escrow_id"]).json()

        assert list(body.keys()) == NON_OPERATOR_KEYS


def test_operator_and_party_responses_differ_only_by_class(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        world = _world(client, auth_header, 1.0)

        party = _detail(client, auth_header, world["requester_key"], world["escrow_id"]).json()
        operator = _detail(client, auth_header, world["operator_key"], world["escrow_id"]).json()

        assert operator.pop(FIELD) == "self_dealing"
        assert operator == party


def test_escrow_list_omits_class(exchange_app, auth_header):
    """The list endpoint only returns the caller's own escrows, so never the class."""
    with TestClient(exchange_app) as client:
        world = _world(client, auth_header, 1.0)

        for key in ("requester_key", "provider_key"):
            resp = client.get("/v1/exchange/escrows", headers=auth_header(world[key]))
            assert resp.status_code == 200, resp.text
            escrows = resp.json()["escrows"]
            assert len(escrows) == 1
            assert list(escrows[0].keys()) == NON_OPERATOR_KEYS

        # The list is scoped to the caller's own escrows, for an operator too.
        resp = client.get(
            "/v1/exchange/escrows", headers=auth_header(world["operator_key"])
        )
        assert resp.json()["escrows"] == []


def test_party_webhook_payload_omits_class(exchange_app, auth_header):
    """Escrow webhooks go to the requester and provider; they must not carry it."""
    with TestClient(exchange_app) as client:
        world = _world(client, auth_header, 1.0)

        from exchange.models import Escrow
        from exchange.webhooks import ALL_EVENTS, _build_escrow_payload

        session = _session()
        with session.begin():
            escrow = session.get(Escrow, world["escrow_id"])
            for event in ALL_EVENTS:
                if not event.startswith("escrow."):
                    continue
                payload = _build_escrow_payload(escrow, event)
                assert set(payload["data"].keys()) == {
                    "escrow_id",
                    "requester_id",
                    "provider_id",
                    "amount",
                    "fee_amount",
                    "status",
                }
        session.close()


def test_operator_refund_of_self_dealing_dispute(exchange_app, auth_header):
    """What a mediator does with the class: read it, then refund via /resolve.

    The refund puts amount + fee back in the requester's available balance and,
    with no stake ruling, returns the dispute stake to whoever filed.
    """
    with TestClient(exchange_app) as client:
        world = _world(client, auth_header, 1.0)
        escrow_id = world["escrow_id"]

        def balance(key: str) -> dict:
            return client.get(
                "/v1/exchange/balance", headers=auth_header(world[key])
            ).json()

        resp = client.post(
            "/v1/exchange/dispute",
            headers=auth_header(world["provider_key"]),
            json={"escrow_id": escrow_id, "reason": "not paid", "stake_amount": STAKE},
        )
        assert resp.status_code == 200, resp.text
        assert balance("provider_key")["available"] == 100 - STAKE

        detail = _detail(client, auth_header, world["operator_key"], escrow_id).json()
        assert detail[FIELD] == "self_dealing"
        assert detail["status"] in ("disputed", "evidence_pending")

        resp = client.post(
            "/v1/exchange/resolve",
            headers=auth_header(world["operator_key"]),
            json={"escrow_id": escrow_id, "resolution": "refund"},
        )
        assert resp.status_code == 200, resp.text

        requester = balance("requester_key")
        provider = balance("provider_key")
        assert requester["available"] == 100
        assert requester["held_in_escrow"] == 0
        assert provider["available"] == 100
        assert provider["held_in_escrow"] == 0

        after = _detail(client, auth_header, world["operator_key"], escrow_id).json()
        assert after["status"] == "refunded"
        assert after["dispute_stake_status"] == "returned"
        assert after[FIELD] == "self_dealing"
