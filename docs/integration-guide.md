# Integration Guide

This guide shows how to add the A2A Settlement Extension (A2A-SE) to an existing A2A client/provider setup without modifying A2A core.

## Provider (agent offering a paid skill)

1. Register an account on the exchange to obtain an `accountId` and API key.
2. Add the settlement extension block to your AgentCard `capabilities.extensions`:
   - `uri`: `https://a2a-settlement.org/extensions/settlement/v1`
   - `params.exchangeUrls`, `params.accountIds`, and optional `params.pricing`
3. When receiving an incoming `a2a.types.Message`, read `message.metadata["a2a-se"]`:
   - verify `escrowId` exists and is valid by calling the exchange (`GET /exchange/escrows/{id}`)
   - reject work if escrow is missing and your agent marks settlement as `required: true`
4. Optionally register a webhook (`PUT /accounts/webhook`) for real-time settlement notifications.

## Requester (client agent paying for a skill)

1. Discover provider AgentCard and inspect `capabilities.extensions` for the A2A-SE URI.
2. Select a mutually trusted exchange (see Section 5.1 of the spec for the negotiation algorithm).
3. Compute price from `params.pricing[skill_id]`.
4. Call the exchange `POST /exchange/escrow` with an `Idempotency-Key` header to lock `amount` + fee.
5. Send your A2A request message with `message.metadata["a2a-se"]` containing:
   - `escrowId`, `amount`, `feeAmount`, `exchangeUrl`, `expiresAt`
6. On terminal A2A TaskState:
   - `completed` -> `POST /exchange/release`
   - `failed` / `canceled` / `rejected` -> `POST /exchange/refund`

## Using the Python SDK

```python
from a2a_settlement import SettlementExchangeClient
from a2a_settlement.agentcard import build_settlement_extension
from a2a_settlement.metadata import build_settlement_metadata

client = SettlementExchangeClient(base_url="http://localhost:3000", api_key="ate_...")

# Create escrow
escrow = client.create_escrow(provider_id="...", amount=10, task_type="sentiment-analysis")

# Build metadata for A2A message
meta = build_settlement_metadata(
    escrow_id=escrow["escrow_id"],
    amount=escrow["amount"],
    exchange_url="http://localhost:3000",
)

# Release on completion
client.release_escrow(escrow_id=escrow["escrow_id"])
```

## Using the TypeScript SDK

```typescript
import { SettlementExchangeClient, buildSettlementMetadata } from "@a2a-settlement/sdk";

const client = new SettlementExchangeClient({
  baseUrl: "http://localhost:3000",
  apiKey: "ate_...",
});

const escrow = await client.createEscrow({
  provider_id: "...",
  amount: 10,
  task_type: "sentiment-analysis",
});

const meta = buildSettlementMetadata({
  escrowId: escrow.escrow_id,
  amount: escrow.amount,
  exchangeUrl: "http://localhost:3000",
});

await client.releaseEscrow(escrow.escrow_id);
```

## Building an AgentCard extension (Python)

```python
from a2a_settlement.agentcard import build_settlement_extension

ext = build_settlement_extension(
    exchange_urls=["https://exchange.a2a-settlement.org/api/v1"],
    account_ids={"https://exchange.a2a-settlement.org/api/v1": "my-uuid"},
    pricing={
        "sentiment-analysis": {
            "baseTokens": 10,
            "model": "per-request",
            "currency": "ATE",
        }
    },
    reputation=0.87,
)
# Add ext to your AgentCard capabilities.extensions array
```
