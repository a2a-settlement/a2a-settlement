"""A2A Provider Agent (Summarize) with A2A-SE escrow verification.

Runs an A2A JSON-RPC server and advertises the settlement extension in its Agent Card.

Usage:
  A2A_EXCHANGE_URL=http://localhost:8000 python3 test_agents/provider_agent.py
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

import httpx
import uvicorn

from a2a.server.apps.jsonrpc.fastapi_app import A2AFastAPIApplication
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.events.in_memory_queue_manager import InMemoryQueueManager
from a2a.server.request_handlers.default_request_handler import DefaultRequestHandler
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill, Message, TextPart


SETTLEMENT_EXTENSION_URI = "https://a2a-settlement.org/extensions/settlement/v1"

EXCHANGE_URL = os.getenv("A2A_EXCHANGE_URL", "http://localhost:8000").rstrip("/")
EXCHANGE_API_URL = os.getenv("A2A_EXCHANGE_API_URL", f"{EXCHANGE_URL}/v1").rstrip("/")

PROVIDER_HOST = os.getenv("PROVIDER_HOST", "0.0.0.0")
PROVIDER_PORT = int(os.getenv("PROVIDER_PORT", "9001"))
PROVIDER_URL = os.getenv("PROVIDER_URL", f"http://localhost:{PROVIDER_PORT}").rstrip("/")

BOT_NAME = os.getenv("PROVIDER_BOT_NAME", "SummarizerBotV2")
DEVELOPER_ID = os.getenv("PROVIDER_DEVELOPER_ID", "test-provider@example.com")
DEVELOPER_NAME = os.getenv("PROVIDER_DEVELOPER_NAME", "Test Provider")
CONTACT_EMAIL = os.getenv("PROVIDER_CONTACT_EMAIL", "test-provider@example.com")

_CREDS_PATH = Path(__file__).with_name(".provider_credentials.json")


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def ensure_provider_account() -> dict[str, Any]:
    existing = _load_json(_CREDS_PATH)
    if existing and existing.get("api_key") and existing.get("account_id") and existing.get("exchange_api_url") == EXCHANGE_API_URL:
        return existing

    # Create a unique bot name on-demand if the default collides.
    bot_name = BOT_NAME
    for _ in range(3):
        r = httpx.post(
            f"{EXCHANGE_API_URL}/accounts/register",
            json={
                "bot_name": bot_name,
                "developer_id": DEVELOPER_ID,
                "developer_name": DEVELOPER_NAME,
                "contact_email": CONTACT_EMAIL,
                "description": "A2A summarize provider (demo)",
                "skills": ["summarize", "summarization"],
            },
            timeout=10.0,
        )
        if r.status_code == 409:
            bot_name = f"{BOT_NAME}-{secrets.token_hex(3)}"
            continue
        r.raise_for_status()
        reg = r.json()
        creds = {
            "exchange_api_url": EXCHANGE_API_URL,
            "account_id": reg["account"]["id"],
            "api_key": reg["api_key"],
            "bot_name": reg["account"]["bot_name"],
        }
        _save_json(_CREDS_PATH, creds)
        return creds

    raise RuntimeError("Failed to register provider after multiple name collisions")


def build_agent_card(*, provider_account_id: str) -> AgentCard:
    return AgentCard(
        name=BOT_NAME,
        version="1.0.0",
        description="Summarizes text inputs. Requires escrow verification via A2A-SE.",
        url=f"{PROVIDER_URL}/",
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(
            streaming=False,
            push_notifications=False,
            extensions=[
                {
                    "uri": SETTLEMENT_EXTENSION_URI,
                    "description": "Accepts token-based payment via A2A Settlement Exchange",
                    "required": False,
                    "params": {
                        "exchangeUrl": EXCHANGE_API_URL,
                        "accountId": provider_account_id,
                        "pricing": {
                            "summarize": {"baseTokens": 10, "model": "per-request", "currency": "ATE"}
                        },
                    },
                }
            ],
        ),
        skills=[
            AgentSkill(
                id="summarize",
                name="Text Summarization",
                description="Summarizes text input into a concise overview.",
                tags=["summarization", "demo"],
                examples=["Summarize this paragraph"],
            )
        ],
    )


class ProviderExecutor(AgentExecutor):
    def __init__(self, *, provider_account_id: str, provider_api_key: str):
        self._provider_account_id = provider_account_id
        self._provider_api_key = provider_api_key

    async def execute(self, context, event_queue) -> None:  # type: ignore[override]
        msg: Message = context.message

        # Extract text content.
        text_parts: list[str] = []
        for p in msg.parts:
            if isinstance(p, TextPart) and p.text:
                text_parts.append(p.text)
        input_text = "\n".join(text_parts).strip()

        md = msg.metadata or {}
        se = md.get("a2a-se") if isinstance(md.get("a2a-se"), dict) else None
        escrow_id = se.get("escrowId") if se else None

        if escrow_id:
            print(f"[Provider] Received task with escrow: {escrow_id}")
            # Verify escrow exists and is targeted at *this* provider account.
            async with httpx.AsyncClient(timeout=10.0) as http:
                r = await http.get(
                    f"{EXCHANGE_API_URL}/exchange/escrows/{escrow_id}",
                    headers={"Authorization": f"Bearer {self._provider_api_key}"},
                )
                r.raise_for_status()
                escrow = r.json()
                if escrow.get("status") != "held" or escrow.get("provider_id") != self._provider_account_id:
                    raise RuntimeError(f"Escrow invalid for provider: {escrow}")
                print(
                    "[Provider] Escrow verified:",
                    f"status={escrow.get('status')}",
                    f"amount={escrow.get('amount')}",
                    f"fee={escrow.get('fee_amount')}",
                )
        else:
            print("[Provider] Received task without settlement metadata")

        word_count = len([w for w in input_text.split() if w.strip()])
        result_text = f"Summary: The input contained {word_count} words. Key themes identified and condensed."

        # Return a direct agent Message result.
        await event_queue.enqueue_event(
            Message(
                messageId="provider-result",
                role="agent",
                parts=[TextPart(text=result_text)],
                metadata=None,
            )
        )

    async def cancel(self, context, event_queue) -> None:  # type: ignore[override]
        raise RuntimeError("cancel not supported in this demo")


def main() -> int:
    creds = ensure_provider_account()
    card = build_agent_card(provider_account_id=creds["account_id"])

    handler = DefaultRequestHandler(
        agent_executor=ProviderExecutor(
            provider_account_id=creds["account_id"],
            provider_api_key=creds["api_key"],
        ),
        task_store=InMemoryTaskStore(),
        queue_manager=InMemoryQueueManager(),
    )

    a2a_app = A2AFastAPIApplication(agent_card=card, http_handler=handler).build(
        agent_card_url="/.well-known/agent-card.json",
        rpc_url="/",
    )

    # Back-compat alias.
    @a2a_app.get("/.well-known/agent.json")
    def _alias_card() -> dict[str, Any]:
        return card.model_dump(by_alias=True)

    print(f"[Provider] Starting {creds['bot_name']} at {PROVIDER_URL}")
    print(f"[Provider] Agent Card: {PROVIDER_URL}/.well-known/agent-card.json")
    print(f"[Provider] Exchange API: {EXCHANGE_API_URL}")
    print(f"[Provider] Exchange accountId: {creds['account_id']}")

    uvicorn.run(a2a_app, host=PROVIDER_HOST, port=PROVIDER_PORT, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

