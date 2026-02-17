from __future__ import annotations

from a2a_settlement.client import SettlementExchangeClient


def main() -> int:
    # Assumes the exchange is already running locally:
    #   python exchange/app.py
    exchange_url = "http://127.0.0.1:3000"

    public = SettlementExchangeClient(exchange_url)

    provider = public.register_account(
        bot_name="QuickstartProvider",
        developer_id="quickstart",
        description="Demo provider",
        skills=["sentiment-analysis"],
    )
    requester = public.register_account(
        bot_name="QuickstartRequester",
        developer_id="quickstart",
        description="Demo requester",
        skills=["orchestration"],
    )

    provider_id = provider["account"]["id"]
    requester_key = requester["api_key"]

    requester_client = SettlementExchangeClient(exchange_url, api_key=requester_key)

    bal0 = requester_client.get_balance()
    print("Requester balance (before):", bal0)

    escrow = requester_client.create_escrow(
        provider_id=provider_id,
        amount=10,
        task_id="quickstart-task-1",
        task_type="sentiment-analysis",
        ttl_minutes=30,
    )
    print("Escrow created:", escrow)

    bal1 = requester_client.get_balance()
    print("Requester balance (after escrow):", bal1)

    released = requester_client.release_escrow(escrow_id=escrow["escrow_id"])
    print("Escrow released:", released)

    bal2 = requester_client.get_balance()
    print("Requester balance (after release):", bal2)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

