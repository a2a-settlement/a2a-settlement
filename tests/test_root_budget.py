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
