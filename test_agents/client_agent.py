"""A2A Client Agent - discovers provider, creates escrow, sends task, settles.

Usage:
  A2A_EXCHANGE_URL=http://localhost:8000 A2A_PROVIDER_URL=http://localhost:9001 \
    python3 test_agents/client_agent.py
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from pathlib import Path
from typing import Any

import httpx

from a2a.client.legacy import A2AClient
from a2a.types import (
    JSONRPCErrorResponse,
    Message,
    MessageSendParams,
    SendMessageRequest,
    SendMessageResponse,
    SendMessageSuccessResponse,
    Task,
    TaskState,
    TextPart,
)


SETTLEMENT_EXTENSION_URI = "https://a2a-settlement.org/extensions/settlement/v1"

EXCHANGE_URL = os.getenv("A2A_EXCHANGE_URL", "http://localhost:8000").rstrip("/")
EXCHANGE_API_URL_DEFAULT = os.getenv("A2A_EXCHANGE_API_URL", f"{EXCHANGE_URL}/v1").rstrip("/")
PROVIDER_URL = os.getenv("A2A_PROVIDER_URL", "http://localhost:9001").rstrip("/")

BOT_NAME = os.getenv("CLIENT_BOT_NAME", "ClientAgentV2")
DEVELOPER_ID = os.getenv("CLIENT_DEVELOPER_ID", "test-client@example.com")
DEVELOPER_NAME = os.getenv("CLIENT_DEVELOPER_NAME", "Test Client")
CONTACT_EMAIL = os.getenv("CLIENT_CONTACT_EMAIL", "test-client@example.com")

_CREDS_PATH = Path(__file__).with_name(".client_credentials.json")


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


async def ensure_client_account(http: httpx.AsyncClient, *, exchange_api_url: str) -> dict[str, Any]:
    existing = _load_json(_CREDS_PATH)
    if existing and existing.get("api_key") and existing.get("account_id") and existing.get("exchange_api_url") == exchange_api_url:
        return existing

    bot_name = BOT_NAME
    for _ in range(3):
        r = await http.post(
            f"{exchange_api_url}/accounts/register",
            json={
                "bot_name": bot_name,
                "developer_id": DEVELOPER_ID,
                "developer_name": DEVELOPER_NAME,
                "contact_email": CONTACT_EMAIL,
                "description": "A2A client (demo)",
                "skills": ["orchestration"],
            },
        )
        if r.status_code == 409:
            bot_name = f"{BOT_NAME}-{secrets.token_hex(3)}"
            continue
        r.raise_for_status()
        reg = r.json()
        creds = {
            "exchange_api_url": exchange_api_url,
            "account_id": reg["account"]["id"],
            "api_key": reg["api_key"],
            "bot_name": reg["account"]["bot_name"],
        }
        _save_json(_CREDS_PATH, creds)
        return creds

    raise RuntimeError("Failed to register client after multiple name collisions")


def _find_settlement_ext(card) -> Any | None:
    for ext in (card.capabilities.extensions or []):
        if ext.uri == SETTLEMENT_EXTENSION_URI:
            return ext
    return None


async def main() -> int:
    async with httpx.AsyncClient(timeout=20.0) as http:
        a2a = A2AClient(httpx_client=http, url=PROVIDER_URL)

        print("\n=== Step A: Agent Discovery ===")
        card = await a2a.get_card()
        print(f"Discovered: {card.name}")
        print(f"Skills: {[s.name for s in card.skills]}")

        settlement_ext = _find_settlement_ext(card)
        if settlement_ext is None:
            raise RuntimeError("Provider does not advertise settlement extension")

        params = settlement_ext.params or {}
        exchange_api_url = (params.get("exchangeUrl") or EXCHANGE_API_URL_DEFAULT).rstrip("/")
        provider_id = params.get("accountId")
        pricing = params.get("pricing") or {}
        price = int(pricing.get("summarize", {}).get("baseTokens", 10))

        print(f"Settlement supported! exchangeUrl={exchange_api_url}")
        print(f"Provider accountId: {provider_id}")
        print(f"Pricing: {pricing}")

        if not provider_id:
            raise RuntimeError("Provider settlement params.accountId missing")

        print("\n=== Step B: Register Client on Exchange ===")
        client = await ensure_client_account(http, exchange_api_url=exchange_api_url)
        print(f"Client registered: {client['account_id']} ({client['bot_name']})")

        print("\n=== Step C: Create Escrow ===")
        escrow_r = await http.post(
            f"{exchange_api_url}/exchange/escrow",
            headers={"Authorization": f"Bearer {client['api_key']}"},
            json={
                "provider_id": provider_id,
                "amount": price,
                "task_id": "a2a-demo-task-1",
                "task_type": "summarize",
                "ttl_minutes": 30,
            },
        )
        escrow_r.raise_for_status()
        escrow = escrow_r.json()
        escrow_id = escrow["escrow_id"]
        print(f"Escrow created: {escrow_id}")
        print(f"Amount held: {escrow.get('total_held')} (fee={escrow.get('fee_amount')})")

        print("\n=== Step D: Send A2A Task ===")
        md = {
            "a2a-se": {
                "escrowId": escrow_id,
                "amount": escrow.get("amount", price),
                # Match updated spec field naming.
                "feeAmount": escrow.get("fee_amount"),
                "exchangeUrl": exchange_api_url,
                "expiresAt": escrow.get("expires_at"),
            }
        }
        msg = Message(
            messageId="msg-1",
            role="user",
            parts=[
                TextPart(
                    text=(
                        "The Agent2Agent protocol is an open standard designed to facilitate communication "
                        "and interoperability between independent AI agent systems."
                    )
                )
            ],
            metadata=md,
        )

        resp: SendMessageResponse = await a2a.send_message(SendMessageRequest(id="req-1", params=MessageSendParams(message=msg)))
        root = resp.root
        if isinstance(root, JSONRPCErrorResponse):
            print("A2A error:", root.error)
            ok = False
            agent_text = None
        else:
            assert isinstance(root, SendMessageSuccessResponse)
            result = root.result

            ok = False
            agent_text = None
            if isinstance(result, Task):
                ok = result.status.state == TaskState.completed
                if result.history:
                    for h in result.history:
                        if isinstance(h, Message) and h.role == "agent":
                            for p in h.parts:
                                if isinstance(p, TextPart) and p.text:
                                    agent_text = p.text
                                    break
            elif isinstance(result, Message):
                ok = result.role == "agent"
                for p in result.parts:
                    if isinstance(p, TextPart) and p.text:
                        agent_text = p.text
                        break

        print(f"Task status: {'completed' if ok else 'failed'}")
        if agent_text:
            print("Agent result:", agent_text)

        print("\n=== Step E: Settlement ===")
        if ok:
            r = await http.post(
                f"{exchange_api_url}/exchange/release",
                headers={"Authorization": f"Bearer {client['api_key']}"},
                json={"escrow_id": escrow_id},
            )
            r.raise_for_status()
            print("Escrow RELEASED:", json.dumps(r.json(), indent=2))
        else:
            r = await http.post(
                f"{exchange_api_url}/exchange/refund",
                headers={"Authorization": f"Bearer {client['api_key']}"},
                json={"escrow_id": escrow_id, "reason": "A2A task failed"},
            )
            r.raise_for_status()
            print("Escrow REFUNDED:", json.dumps(r.json(), indent=2))

        print("\n=== Step F: Final Balances ===")
        bal_r = await http.get(
            f"{exchange_api_url}/exchange/balance",
            headers={"Authorization": f"Bearer {client['api_key']}"},
        )
        bal_r.raise_for_status()
        print("Client balance:", json.dumps(bal_r.json(), indent=2))

        stats_r = await http.get(f"{exchange_api_url}/stats")
        stats_r.raise_for_status()
        print("Exchange stats:", json.dumps(stats_r.json(), indent=2))

        print("\n=== Test Complete ===")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

