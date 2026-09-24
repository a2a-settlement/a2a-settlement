"""Trusted obligation policy binds release to task, criteria, artifact, and approver."""

from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from tests.test_dispute import _promote_to_operator

CRITERIA = hashlib.sha256(b"criteria-v1").hexdigest()
ARTIFACT = hashlib.sha256(b"analysis-v1").hexdigest()
OTHER_ARTIFACT = hashlib.sha256(b"analysis-v2").hexdigest()
TASK = "task-analytical-1"


def _register(client, name, skills):
    resp = client.post(
        "/v1/accounts/register",
        json={
            "bot_name": name,
            "developer_id": "dev",
            "developer_name": "Test Dev",
            "contact_email": "test@test.dev",
            "skills": skills,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _bootstrap(client, auth_header, *, acceptance_required: bool, limit_units: int = 100):
    operator = _register(client, "OperatorBot", ["ops"])
    requester = _register(client, "RequesterBot", ["orchestration"])
    provider = _register(client, "ProviderBot", ["analysis"])
    approver = _register(client, "ApproverBot", ["review"])
    _promote_to_operator("OperatorBot")
    op_key = operator["api_key"]
    budget = client.post(
        "/v1/exchange/assurance/root-budgets",
        headers=auth_header(op_key),
        json={"principal_account_id": requester["account"]["id"], "limit_units": limit_units},
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
            "acceptance_required": acceptance_required,
        },
    )
    assert policy.status_code == 200, policy.text
    bound = client.post(
        "/v1/exchange/assurance/bind-account",
        headers=auth_header(op_key),
        json={"account_id": requester["account"]["id"], "policy_id": policy.json()["policy_id"]},
    )
    assert bound.status_code == 200, bound.text
    return {
        "operator_key": op_key,
        "requester": requester,
        "provider": provider,
        "approver": approver,
        "policy_id": policy.json()["policy_id"],
        "root_budget_id": budget.json()["root_budget_id"],
    }


def _escrow(client, auth_header, ctx, **extra):
    body = {
        "provider_id": ctx["provider"]["account"]["id"],
        "amount": 10,
        "task_id": TASK,
        **extra,
    }
    return client.post(
        "/v1/exchange/escrow",
        headers={**auth_header(ctx["requester"]["api_key"]), "Idempotency-Key": extra.get("idem", "create-1")},
        json=body,
    )


def test_omitted_flag_still_requires_acceptance(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        ctx = _bootstrap(client, auth_header, acceptance_required=True)
        created = _escrow(client, auth_header, ctx)
        assert created.status_code == 201, created.text
        assert created.json()["escrow_id"]
        release = client.post(
            "/v1/exchange/release",
            headers={**auth_header(ctx["requester"]["api_key"]), "Idempotency-Key": "rel-1"},
            json={"escrow_id": created.json()["escrow_id"]},
        )
        assert release.status_code == 409, release.text


def test_caller_cannot_disable_acceptance(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        ctx = _bootstrap(client, auth_header, acceptance_required=True)
        created = _escrow(client, auth_header, ctx, acceptance_required=False, idem="create-off")
        assert created.status_code == 400, created.text


def test_second_escrow_inherits_policy(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        ctx = _bootstrap(client, auth_header, acceptance_required=True, limit_units=50)
        provider2 = _register(client, "ProviderTwo", ["analysis"])
        first = _escrow(client, auth_header, ctx, idem="escrow-a")
        assert first.status_code == 201, first.text
        second = client.post(
            "/v1/exchange/escrow",
            headers={**auth_header(ctx["requester"]["api_key"]), "Idempotency-Key": "escrow-b"},
            json={"provider_id": provider2["account"]["id"], "amount": 10, "task_id": TASK, "acceptance_required": False},
        )
        assert second.status_code == 400


def test_instant_settle_rejected_for_bound_account(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        ctx = _bootstrap(client, auth_header, acceptance_required=False)
        resp = client.post(
            "/v1/exchange/instant-settle",
            headers={**auth_header(ctx["requester"]["api_key"]), "Idempotency-Key": "instant-1"},
            json={"provider_id": ctx["provider"]["account"]["id"], "amount": 10, "task_id": TASK},
        )
        assert resp.status_code == 403, resp.text


def test_wrong_approver_and_artifact_change(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        ctx = _bootstrap(client, auth_header, acceptance_required=True)
        created = _escrow(client, auth_header, ctx)
        assert created.status_code == 201, created.text
        escrow_id = created.json()["escrow_id"]
        stranger = _register(client, "StrangerBot", ["review"])
        denied = client.post(
            "/v1/exchange/acceptance",
            headers=auth_header(stranger["api_key"]),
            json={
                "policy_id": ctx["policy_id"],
                "task_id": TASK,
                "criteria_hash": CRITERIA,
                "artifact_sha256": ARTIFACT,
            },
        )
        assert denied.status_code == 403
        delivered = client.post(
            f"/v1/exchange/escrow/{escrow_id}/deliver",
            headers=auth_header(ctx["provider"]["api_key"]),
            json={"content": "analysis-v1"},
        )
        assert delivered.status_code == 200, delivered.text
        approved = client.post(
            "/v1/exchange/acceptance",
            headers=auth_header(ctx["approver"]["api_key"]),
            json={
                "policy_id": ctx["policy_id"],
                "task_id": TASK,
                "criteria_hash": CRITERIA,
                "artifact_sha256": ARTIFACT,
            },
        )
        assert approved.status_code == 200, approved.text
        changed = client.post(
            f"/v1/exchange/escrow/{escrow_id}/deliver",
            headers=auth_header(ctx["provider"]["api_key"]),
            json={"content": "analysis-v2"},
        )
        assert changed.status_code == 200, changed.text
        release = client.post(
            "/v1/exchange/release",
            headers={**auth_header(ctx["requester"]["api_key"]), "Idempotency-Key": "rel-stale"},
            json={"escrow_id": escrow_id},
        )
        assert release.status_code == 409, release.text


def test_matching_approval_releases(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        ctx = _bootstrap(client, auth_header, acceptance_required=True)
        created = _escrow(client, auth_header, ctx, idem="ok-create")
        escrow_id = created.json()["escrow_id"]
        client.post(
            f"/v1/exchange/escrow/{escrow_id}/deliver",
            headers=auth_header(ctx["provider"]["api_key"]),
            json={"content": "analysis-v1"},
        )
        client.post(
            "/v1/exchange/acceptance",
            headers=auth_header(ctx["approver"]["api_key"]),
            json={
                "policy_id": ctx["policy_id"],
                "task_id": TASK,
                "criteria_hash": CRITERIA,
                "artifact_sha256": ARTIFACT,
            },
        )
        release = client.post(
            "/v1/exchange/release",
            headers={**auth_header(ctx["requester"]["api_key"]), "Idempotency-Key": "rel-ok"},
            json={"escrow_id": escrow_id},
        )
        assert release.status_code == 200, release.text
        assert release.json()["status"] == "released"


def test_baseline_policy_releases_without_approval(exchange_app, auth_header):
    with TestClient(exchange_app) as client:
        ctx = _bootstrap(client, auth_header, acceptance_required=False)
        created = _escrow(client, auth_header, ctx, idem="base-create")
        release = client.post(
            "/v1/exchange/release",
            headers={**auth_header(ctx["requester"]["api_key"]), "Idempotency-Key": "base-rel"},
            json={"escrow_id": created.json()["escrow_id"]},
        )
        assert release.status_code == 200, release.text
