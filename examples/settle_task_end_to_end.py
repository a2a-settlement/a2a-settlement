from __future__ import annotations

import asyncio
import os
import time
from multiprocessing import Process
from typing import Any

import httpx

from a2a_settlement.sdk.agentcard import build_settlement_extension
from a2a_settlement.sdk.client import SettlementExchangeClient
from a2a_settlement.sdk.metadata import build_settlement_metadata


EXCHANGE_URL = os.getenv("A2A_EXCHANGE_URL", "http://127.0.0.1:3000")
PROVIDER_URL = os.getenv("A2A_PROVIDER_URL", "http://127.0.0.1:8001")


def _wait_http_ok(url: str, *, timeout_s: float = 10.0) -> None:
    deadline = time.time() + timeout_s
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=1.0)
            if r.status_code < 500:
                return
        except Exception as e:  # noqa: BLE001 - demo script
            last_err = e
        time.sleep(0.2)
    raise RuntimeError(f"Timed out waiting for {url}") from last_err


def _run_exchange() -> None:
    # SQLite by default; keep it fully self-contained for the demo.
    import tempfile

    demo_db = os.getenv("A2A_EXCHANGE_DEMO_DB")
    if not demo_db:
        demo_db = f"sqlite:///{tempfile.gettempdir()}/a2a_exchange_demo_{os.getpid()}.db"
    os.environ.setdefault("A2A_EXCHANGE_DATABASE_URL", demo_db)
    os.environ.setdefault("A2A_EXCHANGE_AUTO_CREATE_SCHEMA", "true")
    os.environ.setdefault("A2A_EXCHANGE_PORT", "3000")
    os.environ.setdefault("A2A_EXCHANGE_HOST", "127.0.0.1")
    from a2a_settlement.exchange.__main__ import main

    raise SystemExit(main())


def _run_provider(provider_account_id: str, provider_api_key: str) -> None:
    # Build and serve an A2A agent that verifies escrow metadata before working.
    import uvicorn
    from a2a.server.apps.jsonrpc.fastapi_app import A2AFastAPIApplication
    from a2a.server.agent_execution.agent_executor import AgentExecutor
    from a2a.server.events.in_memory_queue_manager import InMemoryQueueManager
    from a2a.server.request_handlers.default_request_handler import DefaultRequestHandler
    from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
    from a2a.types import AgentCapabilities, AgentCard, AgentSkill, Message, Task, TaskState, TaskStatus, TaskStatusUpdateEvent, TextPart

    exchange = SettlementExchangeClient(EXCHANGE_URL, api_key=provider_api_key)

    class ProviderExecutor(AgentExecutor):
        async def execute(self, context, event_queue) -> None:  # type: ignore[override]
            msg: Message = context.message
            md = msg.metadata or {}
            se = md.get("a2a-se") if isinstance(md.get("a2a-se"), dict) else None
            escrow_id = se.get("escrowId") if se else None
            if not escrow_id:
                status = TaskStatus(state=TaskState.rejected, timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
                await event_queue.enqueue_event(
                    TaskStatusUpdateEvent(
                        contextId=context.context_id,
                        taskId=context.task_id,
                        status=status,
                        final=True,
                        metadata={"a2a-se": {"settlementStatus": "pending"}},
                    )
                )
                await event_queue.enqueue_event(
                    Task(
                        id=context.task_id,
                        contextId=context.context_id,
                        status=status,
                        metadata={"a2a-se": {"settlementStatus": "pending"}},
                        artifacts=None,
                        history=None,
                    )
                )
                return

            escrow = exchange.get_escrow(escrow_id=escrow_id)
            if escrow.get("status") != "held" or escrow.get("provider_id") != provider_account_id:
                status = TaskStatus(state=TaskState.rejected, timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
                await event_queue.enqueue_event(
                    Task(
                        id=context.task_id,
                        contextId=context.context_id,
                        status=status,
                        metadata={"a2a-se": {"escrowId": escrow_id, "settlementStatus": "disputed"}},
                        artifacts=None,
                        history=None,
                    )
                )
                return

            # Acknowledge settlement and do “work”.
            working = TaskStatus(state=TaskState.working, timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    contextId=context.context_id,
                    taskId=context.task_id,
                    status=working,
                    final=False,
                    metadata={"a2a-se": {"escrowId": escrow_id, "settlementStatus": "acknowledged"}},
                )
            )

            await asyncio.sleep(0.25)

            completed = TaskStatus(state=TaskState.completed, timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            await event_queue.enqueue_event(
                Task(
                    id=context.task_id,
                    contextId=context.context_id,
                    status=completed,
                    metadata={
                        "a2a-se": {"escrowId": escrow_id, "settlementStatus": "acknowledged"},
                        "result": {"text": "sentiment=positive confidence=0.91"},
                    },
                    artifacts=None,
                    history=[
                        msg,
                        Message(
                            messageId="provider-result",
                            role="agent",
                            parts=[TextPart(text="sentiment=positive confidence=0.91")],
                            metadata=None,
                        ),
                    ],
                )
            )

        async def cancel(self, context, event_queue) -> None:  # type: ignore[override]
            canceled = TaskStatus(state=TaskState.canceled, timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            await event_queue.enqueue_event(
                Task(
                    id=context.task_id,
                    contextId=context.context_id,
                    status=canceled,
                    metadata=None,
                    artifacts=None,
                    history=None,
                )
            )

    # AgentCard includes our settlement extension + pricing.
    settlement_ext = build_settlement_extension(
        exchange_url=f"{EXCHANGE_URL}/v1",
        account_id=provider_account_id,
        pricing={
            "sentiment-analysis": {
                "baseTokens": 10,
                "model": "per-request",
                "currency": "ATE",
            }
        },
        reputation=0.5,
        availability=0.95,
        required=True,
    )

    card = AgentCard(
        name="DemoProvider",
        version="0.1.0",
        description="Provider agent that verifies escrow before working.",
        url=PROVIDER_URL,
        defaultInputModes=["text/plain"],
        defaultOutputModes=["text/plain"],
        capabilities=AgentCapabilities(streaming=True, extensions=[settlement_ext]),
        skills=[
            AgentSkill(
                id="sentiment-analysis",
                name="Sentiment Analysis",
                description="Returns a fake sentiment result.",
                tags=["sentiment", "demo"],
                examples=["Analyze sentiment of this text"],
            )
        ],
    )

    handler = DefaultRequestHandler(
        agent_executor=ProviderExecutor(),
        task_store=InMemoryTaskStore(),
        queue_manager=InMemoryQueueManager(),
    )

    a2a_app = A2AFastAPIApplication(agent_card=card, http_handler=handler).build(
        agent_card_url="/.well-known/agent-card.json",
        rpc_url="/",
    )

    # Convenience alias to match the draft spec wording.
    @a2a_app.get("/.well-known/agent.json")
    def _alias_card() -> dict[str, Any]:
        return card.model_dump(by_alias=True)

    uvicorn.run(a2a_app, host="127.0.0.1", port=8001, log_level="warning")


async def _run_requester(provider_account_id: str, requester_api_key: str) -> None:
    from a2a.client.legacy import A2AClient
    from a2a.types import JSONRPCErrorResponse, Message, MessageSendParams, SendMessageRequest, SendMessageResponse, SendMessageSuccessResponse, Task, TaskState, TextPart

    exchange = SettlementExchangeClient(EXCHANGE_URL, api_key=requester_api_key)

    async with httpx.AsyncClient() as httpx_client:
        a2a_client = A2AClient(httpx_client=httpx_client, url=PROVIDER_URL)

        card = await a2a_client.get_card()
        # Find settlement extension params and pricing.
        settlement_ext = None
        for ext in (card.capabilities.extensions or []):
            if ext.uri == "https://a2a-settlement.org/extensions/settlement/v1":
                settlement_ext = ext
                break
        if settlement_ext is None:
            raise RuntimeError("Provider does not advertise settlement extension")

        pricing = (settlement_ext.params or {}).get("pricing") or {}
        price = int(pricing.get("sentiment-analysis", {}).get("baseTokens", 10))

        escrow = exchange.create_escrow(provider_id=provider_account_id, amount=price, task_id="demo-task-1", task_type="sentiment-analysis")
        md = build_settlement_metadata(
            escrow_id=escrow["escrow_id"],
            amount=escrow["amount"],
            fee_amount=escrow["fee_amount"],
            exchange_url=f"{EXCHANGE_URL}/v1",
            expires_at=escrow["expires_at"],
        )

        msg = Message(
            messageId="msg-1",
            role="user",
            parts=[TextPart(text="Analyze the sentiment of: profits are up, churn is down.")],
            metadata=md,
        )

        resp: SendMessageResponse = await a2a_client.send_message(
            SendMessageRequest(id="req-1", params=MessageSendParams(message=msg))
        )

        root = resp.root
        if isinstance(root, JSONRPCErrorResponse):
            raise RuntimeError(f"A2A error: {root.error}")
        if not isinstance(root, SendMessageSuccessResponse):
            raise RuntimeError(f"Unexpected response envelope: {type(root)}")

        result: Any = root.result
        if not isinstance(result, Task):
            raise RuntimeError(f"Unexpected message/send result type: {type(result)}")

        state = result.status.state
        if state != TaskState.completed:
            raise RuntimeError(f"Unexpected task state: {state}")

        exchange.release_escrow(escrow_id=escrow["escrow_id"])

        requester_bal = exchange.get_balance()
        print("Requester balance:", requester_bal)


def main() -> int:
    exchange_proc = Process(target=_run_exchange, daemon=True)
    exchange_proc.start()
    _wait_http_ok(f"{EXCHANGE_URL}/health")

    # Register requester + provider on the exchange.
    public_exchange = SettlementExchangeClient(EXCHANGE_URL)
    provider_reg = public_exchange.register_account(
        bot_name="DemoProvider",
        developer_id="dev-demo",
        description="Provider demo agent",
        skills=["sentiment-analysis"],
    )
    requester_reg = public_exchange.register_account(
        bot_name="DemoRequester",
        developer_id="dev-demo",
        description="Requester demo agent",
        skills=["orchestration"],
    )

    provider_id = provider_reg["account"]["id"]
    provider_key = provider_reg["api_key"]
    requester_key = requester_reg["api_key"]

    provider_proc = Process(target=_run_provider, args=(provider_id, provider_key), daemon=True)
    provider_proc.start()
    _wait_http_ok(f"{PROVIDER_URL}/.well-known/agent-card.json")

    try:
        asyncio.run(_run_requester(provider_id, requester_key))
    finally:
        provider_proc.terminate()
        exchange_proc.terminate()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

