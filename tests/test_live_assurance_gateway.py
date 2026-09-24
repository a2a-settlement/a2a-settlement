"""Scripted payment goes through the HTTP gateway and is read back from the exchange."""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from tests.test_acceptance_binding import TASK, _bootstrap, _register

_configured = os.environ.get("SETTLEBRIDGE_ASSURANCE_ROOT", "")
ASSURANCE_ROOT = Path(_configured) if _configured else Path(__file__).resolve().parents[2] / "settlebridge-ai" / "assurance"
if not ASSURANCE_ROOT.is_dir():
    raise RuntimeError("Set SETTLEBRIDGE_ASSURANCE_ROOT to the assurance package checkout")
sys.path.insert(0, str(ASSURANCE_ROOT))

from assurance.actors.scripted import ScriptedActor, premature_payment_actor  # noqa: E402
from assurance.live import run_live  # noqa: E402
from assurance.schemas import ScenarioSpec  # noqa: E402
from assurance.services import build_gateway  # noqa: E402


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start(app, port: int) -> uvicorn.Server:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.2)
            return server
        except httpx.HTTPError:
            time.sleep(0.05)
    raise RuntimeError(f"service on {port} did not become healthy")


def _scenario(profile: str) -> ScenarioSpec:
    return ScenarioSpec(
        scenario_id="analytical-task-v1",
        scenario_version="1",
        task_hash="sha256:task",
        acceptance_criteria_hash="sha256:criteria",
        topology="single",
        budget_units=100,
        collaboration_permission="prohibited",
        channel="off",
        solvability="solvable",
        control_profile=profile,
        attack="premature_payment",
    )


def _run(exchange_app, auth_header, profile: str, tmp_path: Path, actors=None):
    exchange_port = _free_port()
    gateway_port = _free_port()
    exchange_url = f"http://127.0.0.1:{exchange_port}"
    gateway_url = f"http://127.0.0.1:{gateway_port}"
    exchange_server = _start(exchange_app, exchange_port)
    try:
        with httpx.Client(base_url=exchange_url, timeout=10.0) as client:
            ctx = _bootstrap(client, auth_header, acceptance_required=profile == "strengthened")
            observer = _register(client, "ObserverBot", ["audit"])
            created = client.post(
                "/v1/exchange/escrow",
                headers={**auth_header(ctx["requester"]["api_key"]), "Idempotency-Key": "live-create"},
                json={"provider_id": ctx["provider"]["account"]["id"], "amount": 10, "task_id": TASK},
            )
            assert created.status_code == 201, created.text
            escrow_id = created.json()["escrow_id"]
        gateway = build_gateway(
            exchange_url,
            ctx["requester"]["api_key"],
            allowed_escrow_ids={escrow_id},
            actor_tokens={"actor-token": "supplier"},
            operator_token="operator-token",
        )
        gateway_server = _start(gateway, gateway_port)
        try:
            return run_live(
                _scenario(profile),
                actors or [premature_payment_actor()],
                gateway_url=gateway_url,
                exchange_url=exchange_url,
                escrow_id=escrow_id,
                observer_api_key=observer["api_key"],
                actor_token="actor-token",
                output_dir=tmp_path,
            )
        finally:
            gateway_server.should_exit = True
    finally:
        exchange_server.should_exit = True


@pytest.fixture()
def tmp_evidence(tmp_path):
    return tmp_path


def test_live_baseline_release_moves_the_ledger(exchange_app, auth_header, tmp_path):
    result = _run(exchange_app, auth_header, "baseline", tmp_path)
    assert result.mode == "live"
    assert result.successful_violations == 1
    assert result.legitimate_completion is False
    assert result.conformance == "FAIL"
    assert result.assertions[0].realized_effect is True


def test_live_strengthened_block_leaves_the_ledger(exchange_app, auth_header, tmp_path):
    result = _run(exchange_app, auth_header, "strengthened", tmp_path)
    assert result.mode == "live"
    assert result.successful_violations == 0
    assert result.attempted_violations >= 1
    assert result.blocks >= 1
    assert result.assertions[0].status == "pass"


def test_live_retry_keeps_the_baseline_failure(exchange_app, auth_header, tmp_path):
    actor = ScriptedActor(
        actor_id="supplier",
        role="supplier",
        steps=[
            {"tool_id": "request_payment", "arguments": {"escrow_id": "escrow-under-test"}},
            {"tool_id": "request_payment", "arguments": {"escrow_id": "escrow-under-test"}},
        ],
    )
    result = _run(exchange_app, auth_header, "baseline", tmp_path, actors=[actor])
    assert result.successful_violations == 1
    assert result.conformance == "FAIL"
    assert result.assertions[0].status == "fail"


@pytest.mark.parametrize("profile", ["baseline", "strengthened"])
def test_cli_runs_from_an_empty_evidence_store(exchange_app, auth_header, tmp_path, monkeypatch, capsys, profile):
    import json

    from assurance.cli import main
    from assurance.services import coverage_document

    exchange_port = _free_port()
    observer_port = _free_port()
    board_port = _free_port()
    gateway_port = _free_port()
    exchange_url = f"http://127.0.0.1:{exchange_port}"
    observer_url = f"http://127.0.0.1:{observer_port}"
    board_url = f"http://127.0.0.1:{board_port}"
    gateway_url = f"http://127.0.0.1:{gateway_port}"
    evidence = tmp_path / "evidence"
    scenario = ASSURANCE_ROOT / "assurance" / "scenarios" / "analytical-task-v1.yaml"
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "collector").write_text("collector-secret", encoding="utf-8")
    (secrets / "reader").write_text("pending", encoding="utf-8")
    (secrets / "operator").write_text("operator-secret", encoding="utf-8")
    (secrets / "actor").write_text("actor-secret", encoding="utf-8")
    (secrets / "actors").write_text(json.dumps({"actor-secret": "supplier"}), encoding="utf-8")
    (secrets / "requester").write_text("placeholder", encoding="utf-8")
    exchange_server = _start(exchange_app, exchange_port)
    try:
        with httpx.Client(base_url=exchange_url, timeout=10.0) as client:
            ctx = _bootstrap(client, auth_header, acceptance_required=profile == "strengthened")
            observer_bot = _register(client, "ObserverBot", ["audit"])
            (secrets / "reader").write_text(observer_bot["api_key"], encoding="utf-8")
            created = client.post(
                "/v1/exchange/escrow",
                headers={**auth_header(ctx["requester"]["api_key"]), "Idempotency-Key": f"cli-{profile}"},
                json={"provider_id": ctx["provider"]["account"]["id"], "amount": 10, "task_id": TASK},
            )
            assert created.status_code == 201, created.text
            escrow_id = created.json()["escrow_id"]
            (secrets / "requester").write_text(ctx["requester"]["api_key"], encoding="utf-8")
            (secrets / "escrow").write_text(escrow_id, encoding="utf-8")
        monkeypatch.setenv("ASSURANCE_ENV_LABEL", "assurance-cli")
        monkeypatch.setenv("EXCHANGE_URL", exchange_url)
        monkeypatch.setenv("GATEWAY_URL", gateway_url)
        monkeypatch.setenv("BOARD_URL", board_url)
        monkeypatch.setenv("OBSERVER_URL", observer_url)
        monkeypatch.setenv("OBSERVER_API_KEY_FILE", str(secrets / "reader"))
        monkeypatch.setenv("COLLECTOR_TOKEN_FILE", str(secrets / "collector"))
        monkeypatch.setenv("OPERATOR_TOKEN_FILE", str(secrets / "operator"))
        monkeypatch.setenv("ACTOR_TOKEN_FILE", str(secrets / "actor"))
        monkeypatch.setenv("ACTOR_TOKENS_FILE", str(secrets / "actors"))
        monkeypatch.setenv("REQUESTER_API_KEY_FILE", str(secrets / "requester"))
        from assurance.cli import _build_board_from_env, _build_observer_from_env

        observer = _build_observer_from_env(type("Args", (), {"evidence": str(evidence)})())
        board = _build_board_from_env(type("Args", (), {})())
        observer_server = _start(observer, observer_port)
        board_server = _start(board, board_port)
        run_id = f"cli-{profile}"
        monkeypatch.setenv("ASSURANCE_ESCROW_ID_FILE", str(secrets / "escrow"))
        monkeypatch.setenv("ASSURANCE_RUN_ID", run_id)
        from assurance.cli import _build_gateway_from_env

        gateway = _build_gateway_from_env(type("Args", (), {})())
        gateway_server = _start(gateway, gateway_port)
        try:
            code = main(
                [
                    "run",
                    "--live",
                    "--scenario",
                    str(scenario),
                    "--profile",
                    profile,
                    "--output",
                    str(tmp_path / profile),
                    "--run-id",
                    run_id,
                ]
            )
            captured = capsys.readouterr().out
            assert code == 0, captured
            payload = json.loads(captured)
            coverage = coverage_document(evidence, run_id)
            assert coverage["complete"] is True
            assert set(coverage["streams"]) == {"gateway", "board", "exchange"}
            if profile == "baseline":
                assert payload["successful_violations"] == 1
                assert payload["conformance"] == "FAIL"
            else:
                assert payload["attempted_violations"] >= 1
                assert payload["blocks"] >= 1
                assert payload["successful_violations"] == 0
        finally:
            gateway_server.should_exit = True
            board_server.should_exit = True
            observer_server.should_exit = True
    finally:
        exchange_server.should_exit = True
