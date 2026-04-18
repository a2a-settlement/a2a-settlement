#!/usr/bin/env python3
"""
Multi-Agent A2A Settlement Pipeline Test

Orchestrates a cross-domain research pipeline between two OpenClaw agents:
  - EAFTOS (GovCon lane): Palantir defense contract intelligence
  - AlphaSignal (Fintech lane): PLTR stock investment analysis

Escrow is held on the A2A Settlement Exchange until both deliverables
are verified. Only then is payment released to the provider agents.

Usage:
    export OPENCLAW_GATEWAY_TOKEN="..."
    python examples/multi-agent-pipeline.py
"""

from __future__ import annotations

import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "sdk"))

import httpx

from a2a_settlement.client import SettlementExchangeClient

EXCHANGE_URL = os.environ.get(
    "A2A_EXCHANGE_URL", "http://127.0.0.1:3000"
)
GATEWAY_URL = "http://127.0.0.1:18789"
OUTPUT_DIR = pathlib.Path("/tmp/a2a-test-deliverables")

ESCROW_AMOUNT = 15
ESCROW_TTL_MIN = 30


def _token() -> str:
    token = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "")
    if not token:
        env_path = pathlib.Path(__file__).resolve().parent.parent.parent / "openclaw-docker" / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("OPENCLAW_GATEWAY_TOKEN="):
                    token = line.split("=", 1)[1].strip()
                    break
    return token


def banner(msg: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {msg}")
    print(f"{'=' * 60}\n")


def section(msg: str) -> None:
    print(f"\n[{msg}]")


def send_to_agent(agent_id: str, message: str, token: str) -> str:
    """Send a message to an OpenClaw agent via the HTTP chat completions API."""
    with httpx.Client(timeout=300.0) as client:
        resp = client.post(
            f"{GATEWAY_URL}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "model": f"openclaw:{agent_id}",
                "messages": [{"role": "user", "content": message}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


def verify_deliverable(text: str, *, min_chars: int = 300, min_headers: int = 2) -> tuple[bool, str]:
    if not text:
        return False, "Empty response"
    if len(text) < min_chars:
        return False, f"Too short ({len(text)} chars, need {min_chars}+)"
    header_count = text.count("\n#")
    if header_count < min_headers:
        return False, f"Missing structure ({header_count} headers, need {min_headers}+)"
    return True, f"OK ({len(text):,} chars, {header_count} sections)"


def main() -> int:
    banner("A2A Settlement Multi-Agent Pipeline Test")
    print(f"Exchange:  {EXCHANGE_URL}")
    print(f"Gateway:   {GATEWAY_URL}")

    gw_token = _token()
    if not gw_token:
        print("ERROR: OPENCLAW_GATEWAY_TOKEN not set and not found in .env")
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── Setup: register exchange accounts ────────────────────────
    section("Setup: Registering exchange accounts")

    public = SettlementExchangeClient(EXCHANGE_URL)
    ts = str(int(time.time()))

    orch_reg = public.register_account(
        bot_name=f"PipelineOrchestrator-{ts}",
        developer_id="a2a-pipeline-test",
        developer_name="Pipeline Test",
        contact_email="test@a2a-settlement.org",
        description="Test orchestrator commissioning cross-domain research",
        skills=["orchestration"],
    )
    eaftos_reg = public.register_account(
        bot_name=f"EAFTOS-Provider-{ts}",
        developer_id="a2a-pipeline-test",
        developer_name="EAFTOS Agent",
        contact_email="eaftos@a2a-settlement.org",
        description="GovCon BD research agent (defense contract intel)",
        skills=["govcon-research", "contract-intel"],
    )
    alpha_reg = public.register_account(
        bot_name=f"AlphaSignal-Provider-{ts}",
        developer_id="a2a-pipeline-test",
        developer_name="AlphaSignal Agent",
        contact_email="alphasignal@a2a-settlement.org",
        description="Investment analysis agent (equity research)",
        skills=["investment-analysis", "stock-research"],
    )

    orch_key = orch_reg["api_key"]
    eaftos_id = eaftos_reg["account"]["id"]
    alpha_id = alpha_reg["account"]["id"]

    print(f"  Orchestrator:  {orch_reg['account']['id']}")
    print(f"  EAFTOS:        {eaftos_id}")
    print(f"  AlphaSignal:   {alpha_id}")

    orch_client = SettlementExchangeClient(EXCHANGE_URL, api_key=orch_key)
    bal = orch_client.get_balance()
    print(f"  Orchestrator balance: {bal['available']} tokens")

    # ── Escrow: lock funds for both deliverables ─────────────────
    section("Escrow: Creating payment holds")

    escrow1 = orch_client.create_escrow(
        provider_id=eaftos_id,
        amount=ESCROW_AMOUNT,
        task_id="palantir-defense-contract-report",
        task_type="govcon-research",
        ttl_minutes=ESCROW_TTL_MIN,
    )
    print(f"  Escrow #1 (contract report):     {escrow1['escrow_id']} "
          f"({escrow1['amount']} tokens, {escrow1['status']})")

    escrow2 = orch_client.create_escrow(
        provider_id=alpha_id,
        amount=ESCROW_AMOUNT,
        task_id="pltr-investment-analysis",
        task_type="investment-analysis",
        ttl_minutes=ESCROW_TTL_MIN,
    )
    print(f"  Escrow #2 (investment analysis): {escrow2['escrow_id']} "
          f"({escrow2['amount']} tokens, {escrow2['status']})")

    bal = orch_client.get_balance()
    print(f"  Orchestrator balance after escrow: {bal['available']} tokens")

    # ── Phase 1: EAFTOS — Palantir defense contract intel ────────
    section("Phase 1: EAFTOS -- Palantir Defense Contract Intelligence")
    print("  Sending task to eaftos agent...")

    eaftos_prompt = (
        "Research Palantir Technologies' active and upcoming U.S. defense and "
        "intelligence contracts. Produce a structured analyst report covering:\n\n"
        "- Recent contract wins and awards (2024-2026)\n"
        "- Major recompetes and pipeline opportunities\n"
        "- Key agency relationships (DOD, Army, SOCOM, USIC, DISA, etc.)\n"
        "- Platforms deployed (Maven, Gotham, Foundry, AIP)\n"
        "- Competitive positioning vs BAH, Leidos, SAIC, Raytheon\n\n"
        "Write this as a professional GovCon analyst report in markdown with "
        "clear section headers. Be specific about contract names, dollar values "
        "where known, and sponsoring agencies. Return the full report as your "
        "response -- do not write to any files."
    )

    t0 = time.time()
    contract_report = send_to_agent("eaftos", eaftos_prompt, gw_token)
    elapsed1 = time.time() - t0

    report_path = OUTPUT_DIR / "palantir-defense-contracts.md"
    report_path.write_text(contract_report, encoding="utf-8")

    ok1, msg1 = verify_deliverable(contract_report)
    status1 = "VERIFIED" if ok1 else f"FAILED: {msg1}"
    print(f"  Deliverable: {report_path.name} ... {status1}")
    print(f"  Time: {elapsed1:.1f}s")

    # ── Phase 2: AlphaSignal — PLTR investment analysis ──────────
    section("Phase 2: AlphaSignal -- PLTR Investment Analysis")
    print(f"  Injecting contract report as context ({len(contract_report):,} chars)")
    print("  Sending task to alphasignal agent...")

    alpha_prompt = (
        "You are producing an equity research note on Palantir Technologies "
        "(PLTR). A GovCon analyst has prepared the following defense contract "
        "intelligence report. Use it as primary source material.\n\n"
        "--- BEGIN CONTRACT INTELLIGENCE REPORT ---\n"
        f"{contract_report}\n"
        "--- END CONTRACT INTELLIGENCE REPORT ---\n\n"
        "Produce a structured investment analysis covering:\n\n"
        "- Revenue impact of the defense pipeline (estimated TAM, contract "
        "values, implied growth)\n"
        "- Growth catalysts tied to specific contracts and agency expansion\n"
        "- Risk factors (customer concentration, recompete risk, budget "
        "sequestration, political exposure)\n"
        "- Competitive moat in defense AI/ML vs traditional primes\n"
        "- Conviction level and directional thesis\n\n"
        "Write this as a professional equity research note in markdown with "
        "clear section headers. Reference specific contracts and data points "
        "from the intelligence report. Return the full analysis as your "
        "response -- do not write to any files."
    )

    t0 = time.time()
    investment_analysis = send_to_agent("alphasignal", alpha_prompt, gw_token)
    elapsed2 = time.time() - t0

    analysis_path = OUTPUT_DIR / "pltr-investment-analysis.md"
    analysis_path.write_text(investment_analysis, encoding="utf-8")

    ok2, msg2 = verify_deliverable(investment_analysis)
    status2 = "VERIFIED" if ok2 else f"FAILED: {msg2}"
    print(f"  Deliverable: {analysis_path.name} ... {status2}")
    print(f"  Time: {elapsed2:.1f}s")

    # ── Settlement: release or refund based on verification ──────
    section("Settlement")

    if ok1:
        orch_client.release_escrow(escrow_id=escrow1["escrow_id"])
        print(f"  Escrow #1 (contract report):     RELEASED "
              f"(eaftos-provider +{escrow1['amount']} tokens)")
    else:
        orch_client.refund_escrow(escrow_id=escrow1["escrow_id"], reason=msg1)
        print(f"  Escrow #1 (contract report):     REFUNDED ({msg1})")

    if ok2:
        orch_client.release_escrow(escrow_id=escrow2["escrow_id"])
        print(f"  Escrow #2 (investment analysis): RELEASED "
              f"(alphasignal-provider +{escrow2['amount']} tokens)")
    else:
        orch_client.refund_escrow(escrow_id=escrow2["escrow_id"], reason=msg2)
        print(f"  Escrow #2 (investment analysis): REFUNDED ({msg2})")

    if ok1 and ok2:
        result = "PASS"
    elif ok1 or ok2:
        result = "PARTIAL"
    else:
        result = "FAIL"

    # ── Final report ─────────────────────────────────────────────
    section("Final Balances")
    bal_orch = orch_client.get_balance()
    print(f"  Orchestrator:  {bal_orch['available']} available, "
          f"{bal_orch.get('held_in_escrow', 0)} held")

    section("Deliverables on disk")
    print(f"  {report_path}")
    print(f"  {analysis_path}")

    section("Transaction Log")
    txns = orch_client.get_transactions(limit=10)
    for tx in txns.get("transactions", []):
        tx_type = tx.get("type") or tx.get("tx_type", "?")
        print(f"  {tx.get('created_at', '?')[:19]}  "
              f"{tx_type:12s}  "
              f"{tx.get('amount', '?'):>6} tokens  "
              f"{tx.get('description', '')}")

    banner(f"RESULT: {result}")
    return 0 if result == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
