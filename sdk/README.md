# a2a-settlement

Python SDK for the [A2A Settlement Extension (A2A-SE)](https://a2a-settlement.org) — the settlement layer for autonomous agent commerce.

**A2A-SE** defines settlement semantics for A2A tasks: economic commitment, delivery, verification, release/refund, disputes, reputation, and finality. Escrow is one mechanism that implements commitment. Zero modifications to A2A core. Currency-agnostic.

```
RequesterAgent  <---- A2A ---->  ProviderAgent
     |                               |
     +--------- HTTPS (A2A-SE) ------+
                 Exchange
          escrow / release / refund
```

## Install

```bash
pip install a2a-settlement
```

With Ed25519 identity helpers:

```bash
pip install a2a-settlement[identity]
```

## Quick start

```python
from a2a_settlement import SettlementExchangeClient

client = SettlementExchangeClient("https://your-exchange.example.com")

# Create an escrow
escrow = await client.create_escrow(
    payer_id="agent-alice",
    payee_id="agent-bob",
    amount="10.00",
    currency="USD",
    task_id="task-123",
)

# Release funds on task completion
await client.release_escrow(escrow["escrow_id"])
```

## Features

- **Escrow lifecycle** — create, release, refund, and dispute escrows
- **AgentCard builder** — generate A2A-SE extension blocks for agent cards
- **Signed requests** — Ed25519 request signing for authenticated API calls
- **Task metadata** — helpers for A2A message/task `metadata["a2a-se"]`
- **Pricing models** — fixed, per-message, and time-based pricing

## Links

- [Full documentation](https://github.com/a2a-settlement/a2a-settlement/tree/main/docs)
- [Specification (SPEC.md)](https://github.com/a2a-settlement/a2a-settlement/blob/main/SPEC.md)
- [OpenAPI spec](https://github.com/a2a-settlement/a2a-settlement/blob/main/openapi.yaml)
- [TypeScript SDK](https://github.com/a2a-settlement/a2a-settlement/tree/main/sdk-ts)

## License

MIT
