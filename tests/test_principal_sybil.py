"""Principal Sybil-smell risk_score from diversity sweep."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select


def _reg(client, name: str, developer_id: str = "dev-sybil") -> dict:
    return client.post(
        "/v1/accounts/register",
        json={
            "bot_name": name,
            "developer_id": developer_id,
            "developer_name": "Dev",
            "contact_email": "a@b.com",
        },
    ).json()


def test_diversity_sweep_sets_principal_risk_score(exchange_app, auth_header):
    from exchange.config import get_session
    from exchange.models import Account, AgentPrincipalLink, Principal
    from exchange.tasks import _update_principal_risk_scores, run_diversity_sweep

    with TestClient(exchange_app) as client:
        agents = [_reg(client, f"SybilBot{i}") for i in range(5)]
        owner = agents[0]
        agent_ids = [a["account"]["id"] for a in agents]

        session = next(get_session())
        try:
            with session.begin():
                principal = Principal(principal_type="unknown", kya_level="none")
                session.add(principal)
                session.flush()
                principal_id = principal.id
                for aid in agent_ids:
                    session.add(
                        AgentPrincipalLink(
                            agent_id=aid,
                            principal_id=principal_id,
                            link_source="payment_graph",
                            confidence=0.4,
                        )
                    )
                    acct = session.execute(
                        select(Account).where(Account.id == aid)
                    ).scalar_one()
                    acct.diversity_score = 0.1
                    session.add(acct)
        finally:
            session.close()

        # Direct risk update (same helper the sweep calls)
        session = next(get_session())
        try:
            with session.begin():
                n = _update_principal_risk_scores(session)
                assert n >= 1
                p = session.execute(
                    select(Principal).where(Principal.id == principal_id)
                ).scalar_one()
                assert p.risk_score == min(1.0, 0.2 * 5)
        finally:
            session.close()

        resp = client.get(
            f"/v1/accounts/{owner['account']['id']}/principal",
            headers=auth_header(owner["api_key"]),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["risk_score"] == 1.0
        assert body["low_confidence_peer_count"] == 5
        assert body["sybil_risk"] is True
        assert body["links"][0]["sybil_risk"] is True
        assert body["links"][0]["risk_score"] == 1.0

        # Full sweep still runs and returns the new counter
        result = run_diversity_sweep()
        assert "principals_risk_scored" in result
        assert result["principals_risk_scored"] >= 1


def test_risk_score_decays_without_sybil_smell(exchange_app, auth_header):
    from exchange.config import get_session
    from exchange.models import Account, AgentPrincipalLink, Principal
    from exchange.tasks import _update_principal_risk_scores

    with TestClient(exchange_app) as client:
        agent = _reg(client, "CleanBot", developer_id="dev-clean")
        aid = agent["account"]["id"]

        session = next(get_session())
        try:
            with session.begin():
                principal = Principal(
                    principal_type="unknown",
                    kya_level="basic",
                    risk_score=0.8,
                )
                session.add(principal)
                session.flush()
                principal_id = principal.id
                session.add(
                    AgentPrincipalLink(
                        agent_id=aid,
                        principal_id=principal_id,
                        link_source="registration",
                        confidence=0.9,
                    )
                )
                acct = session.execute(
                    select(Account).where(Account.id == aid)
                ).scalar_one()
                acct.diversity_score = 0.9
                session.add(acct)
        finally:
            session.close()

        session = next(get_session())
        try:
            with session.begin():
                _update_principal_risk_scores(session)
                p = session.execute(
                    select(Principal).where(Principal.id == principal_id)
                ).scalar_one()
                assert p.risk_score == 0.4
        finally:
            session.close()

        resp = client.get(
            f"/v1/accounts/{aid}/principal",
            headers=auth_header(agent["api_key"]),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["risk_score"] == 0.4
        assert body["low_confidence_peer_count"] == 0
        assert "sybil_risk" not in body


def test_inheritance_not_implemented_hygiene_comment():
    """Assert confidence hygiene is documented; inheritance remains unimplemented."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "exchange" / "principal_resolver.py"
    text = src.read_text()
    assert "must never" in text
    assert "reputation inheritance" in text.lower()
    assert "hard_match" in text
    # No blending function shipped yet
    assert "inherit_reputation" not in text
    assert "blend_reputation" not in text
