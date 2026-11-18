"""Root budget is locked with the escrow transaction. Fees count once."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_acceptance_binding import CRITERIA, TASK, _bootstrap, _register

def test_sibling_hold_cannot_exceed_root_budget(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        ctx = _bootstrap(client, auth_header, acceptance_required=False, limit_units=20)
        sibling = _register(client, "SiblingBot", ["orchestration"])
        client.post(
            "/v1/exchange/assurance/bind-account",
            headers=auth_header(ctx["operator_key"]),
            json={"account_id": sibling["account"]["id"], "policy_id": ctx["policy_id"]},
        )
        # Give the sibling the starter balance by registering; both spend the shared root.
        first = client.post(
            "/v1/exchange/escrow",
            headers={**auth_header(ctx["requester"]["api_key"]), "Idempotency-Key": "sib-a"},
            json={"provider_id": ctx["provider"]["account"]["id"], "amount": 10, "task_id": TASK},
        )
        assert first.status_code == 201, first.text
        provider2 = _register(client, "ProviderTwo", ["analysis"])
        second = client.post(
            "/v1/exchange/escrow",
            headers={**auth_header(sibling["api_key"]), "Idempotency-Key": "sib-b"},
            json={"provider_id": provider2["account"]["id"], "amount": 10, "task_id": TASK},
        )
        assert second.status_code == 400, second.text
        assert "Root budget exceeded" in second.text


def test_concurrent_sibling_holds_cannot_both_commit(exchange_app, auth_header):
    import threading

    with TestClient(exchange_app) as client:
        ctx = _bootstrap(client, auth_header, acceptance_required=False, limit_units=20)
        sibling = _register(client, "SiblingRace", ["orchestration"])
        client.post(
            "/v1/exchange/assurance/bind-account",
            headers=auth_header(ctx["operator_key"]),
            json={"account_id": sibling["account"]["id"], "policy_id": ctx["policy_id"]},
        )
        provider2 = _register(client, "ProviderRace", ["analysis"])
        results: list[int] = []

        def hold(api_key: str, provider_id: str, key: str) -> None:
            resp = client.post(
                "/v1/exchange/escrow",
                headers={**auth_header(api_key), "Idempotency-Key": key},
                json={"provider_id": provider_id, "amount": 10, "task_id": TASK},
            )
            results.append(resp.status_code)

        threads = [
            threading.Thread(
                target=hold,
                args=(ctx["requester"]["api_key"], ctx["provider"]["account"]["id"], "race-a"),
            ),
            threading.Thread(
                target=hold,
                args=(sibling["api_key"], provider2["account"]["id"], "race-b"),
            ),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sorted(results) == [201, 400]


def test_refund_restores_reserved_capacity(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        ctx = _bootstrap(client, auth_header, acceptance_required=False, limit_units=11)
        created = client.post(
            "/v1/exchange/escrow",
            headers={**auth_header(ctx["requester"]["api_key"]), "Idempotency-Key": "ref-create"},
            json={"provider_id": ctx["provider"]["account"]["id"], "amount": 10, "task_id": TASK},
        )
        assert created.status_code == 201, created.text
        refund = client.post(
            "/v1/exchange/refund",
            headers={**auth_header(ctx["requester"]["api_key"]), "Idempotency-Key": "ref-refund"},
            json={"escrow_id": created.json()["escrow_id"]},
        )
        assert refund.status_code == 200, refund.text
        provider2 = _register(client, "ProviderReuse", ["analysis"])
        again = client.post(
            "/v1/exchange/escrow",
            headers={**auth_header(ctx["requester"]["api_key"]), "Idempotency-Key": "ref-again"},
            json={"provider_id": provider2["account"]["id"], "amount": 10, "task_id": TASK},
        )
        assert again.status_code == 201, again.text


def test_caller_cannot_replace_root_id(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        ctx = _bootstrap(client, auth_header, acceptance_required=False)
        resp = client.post(
            "/v1/exchange/escrow",
            headers={**auth_header(ctx["requester"]["api_key"]), "Idempotency-Key": "bad-root"},
            json={
                "provider_id": ctx["provider"]["account"]["id"],
                "amount": 10,
                "task_id": TASK,
                "root_budget_id": "not-the-bound-root",
            },
        )
        assert resp.status_code == 400, resp.text


def test_release_moves_reservation_to_spent_once(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        ctx = _bootstrap(client, auth_header, acceptance_required=False, limit_units=11)
        created = client.post(
            "/v1/exchange/escrow",
            headers={**auth_header(ctx["requester"]["api_key"]), "Idempotency-Key": "spend-create"},
            json={"provider_id": ctx["provider"]["account"]["id"], "amount": 10, "task_id": TASK},
        )
        escrow_id = created.json()["escrow_id"]
        client.post(
            "/v1/exchange/release",
            headers={**auth_header(ctx["requester"]["api_key"]), "Idempotency-Key": "spend-rel"},
            json={"escrow_id": escrow_id},
        )
        view = client.get(
            f"/v1/exchange/assurance/root-budgets/{ctx['root_budget_id']}",
            headers=auth_header(ctx["requester"]["api_key"]),
        )
        assert view.status_code == 200, view.text
        body = view.json()
        assert body["reserved_units"] == 0
        assert body["spent_units"] == 11


def _plain_escrow_world(client, auth_header):
    """An ordinary escrow with no policy or root budget, plus an outsider and an operator."""
    from tests.test_acceptance_binding import _promote_to_operator

    requester = _register(client, "PlainRequester", ["orchestration"])
    provider = _register(client, "PlainProvider", ["analysis"])
    outsider = _register(client, "PlainOutsider", ["audit"])
    operator = _register(client, "PlainOperator", ["ops"])
    _promote_to_operator("PlainOperator")
    created = client.post(
        "/v1/exchange/escrow",
        headers=auth_header(requester["api_key"]),
        json={"provider_id": provider["account"]["id"], "amount": 10},
    )
    assert created.status_code == 201, created.text
    return {
        "escrow_id": created.json()["escrow_id"],
        "requester": requester,
        "provider": provider,
        "outsider": outsider,
        "operator": operator,
    }


def test_binding_view_of_a_plain_escrow_is_party_or_operator_only(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        world = _plain_escrow_world(client, auth_header)
        url = f"/v1/exchange/assurance/escrows/{world['escrow_id']}"
        for who in ("requester", "provider", "operator"):
            resp = client.get(url, headers=auth_header(world[who]["api_key"]))
            assert resp.status_code == 200, (who, resp.text)
        resp = client.get(url, headers=auth_header(world["outsider"]["api_key"]))
        assert resp.status_code == 403, resp.text
        assert "provider_available" not in resp.text


def test_binding_view_and_budget_of_a_bound_escrow(exchange_app, auth_header):
    """Parties, the approver, the operator and bound accounts read; an outsider does not."""
    with TestClient(exchange_app) as client:
        ctx = _bootstrap(client, auth_header, acceptance_required=True)
        outsider = _register(client, "OutsiderBot", ["audit"])
        observer = _register(client, "ObserverBot", ["audit"])
        created = client.post(
            "/v1/exchange/escrow",
            headers={**auth_header(ctx["requester"]["api_key"]), "Idempotency-Key": "read-check"},
            json={"provider_id": ctx["provider"]["account"]["id"], "amount": 10, "task_id": TASK},
        )
        assert created.status_code == 201, created.text
        escrow_url = f"/v1/exchange/assurance/escrows/{created.json()['escrow_id']}"
        budget_url = f"/v1/exchange/assurance/root-budgets/{ctx['root_budget_id']}"

        def status(url, key):
            return client.get(url, headers=auth_header(key)).status_code

        for who in ("requester", "provider", "approver"):
            assert status(escrow_url, ctx[who]["api_key"]) == 200, who
        assert status(escrow_url, ctx["operator_key"]) == 200
        assert status(budget_url, ctx["requester"]["api_key"]) == 200
        assert status(budget_url, ctx["operator_key"]) == 200

        for key in (outsider["api_key"], observer["api_key"]):
            assert status(escrow_url, key) == 403
            assert status(budget_url, key) == 403
        # The provider is a party to the escrow, not to the budget.
        assert status(budget_url, ctx["provider"]["api_key"]) == 403

        bound = client.post(
            "/v1/exchange/assurance/bind-account",
            headers=auth_header(ctx["operator_key"]),
            json={"account_id": observer["account"]["id"], "policy_id": ctx["policy_id"]},
        )
        assert bound.status_code == 200, bound.text
        assert status(escrow_url, observer["api_key"]) == 200
        assert status(budget_url, observer["api_key"]) == 200
        assert status(escrow_url, outsider["api_key"]) == 403
