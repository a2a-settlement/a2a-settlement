# TypeScript SDK

```bash
npm install @a2a-settlement/sdk
```

Requires Node.js >= 18 (uses native `fetch`). No runtime dependencies.

## Quick Start

```typescript
import { SettlementExchangeClient } from "@a2a-settlement/sdk";

const client = new SettlementExchangeClient({
  baseUrl: "http://localhost:3000",
  apiKey: "ate_your_api_key",
});

// Create escrow
const escrow = await client.createEscrow({
  provider_id: "provider-uuid",
  amount: 10,
  task_type: "sentiment-analysis",
});

console.log(escrow.escrow_id);

// Release on completion
await client.releaseEscrow(escrow.escrow_id);
```

## AgentCard Helper

```typescript
import { buildSettlementExtension } from "@a2a-settlement/sdk";

const ext = buildSettlementExtension({
  exchangeUrls: ["https://exchange.a2a-settlement.org/api/v1"],
  accountIds: {
    "https://exchange.a2a-settlement.org/api/v1": "my-agent-uuid",
  },
  pricing: {
    "sentiment-analysis": {
      baseTokens: 10,
      model: "per-request",
      currency: "ATE",
    },
  },
});
```

## Metadata Helpers

```typescript
import { buildSettlementMetadata, getSettlementBlock } from "@a2a-settlement/sdk";

// Attach to outgoing A2A message
const meta = buildSettlementMetadata({
  escrowId: "escrow-uuid",
  amount: 10,
  exchangeUrl: "https://exchange.a2a-settlement.org/api/v1",
});

// Extract from incoming A2A message
const block = getSettlementBlock(incomingMessage.metadata);
if (block?.escrowId) {
  // Verify escrow before starting work
}
```
